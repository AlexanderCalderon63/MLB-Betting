"""
bet_analytics.py — Slice resolved real bets into ROI breakdowns for the Bet Tracker.

Pure pandas: no Streamlit, no DB. The page passes in the `completed` bets
DataFrame (outcome in Win/Loss/Push/Cashout) plus a per-bet "bucket" Series,
and renders the returned display-ready tables with `ui.responsive_table`.

Win% counts Push/Cashout in the denominator to match the page's top-line
win-rate definition (3_Bet_Tracker.py). ROI = P&L / amount staked.

Run `python bet_analytics.py` for the money-math self-check.
"""
import pandas as pd

# Same thresholds as evaluate_value / Model Performance, expressed as buckets.
TIER_ORDER = ["🔥 Strong (≥8%)", "✅ Value (4–8%)", "⚠️ Slight (1–4%)", "➖ No edge (<1%)"]


def signal_tier(edge: float) -> str | None:
    """Bucket a model-minus-market edge into the app's signal tiers (None if unknown)."""
    if pd.isna(edge):
        return None
    if edge >= 0.08:
        return TIER_ORDER[0]
    if edge >= 0.04:
        return TIER_ORDER[1]
    if edge >= 0.01:
        return TIER_ORDER[2]
    return TIER_ORDER[3]


def roi_breakdown(completed: pd.DataFrame, bucket: pd.Series, label: str,
                  order: list | None = None) -> pd.DataFrame:
    """ROI / win-rate / P&L grouped by a per-bet bucket label.

    completed  resolved bets (needs columns: outcome, stake, profit_loss)
    bucket     Series aligned to `completed`, one bucket label per bet (NaN = dropped)
    order      optional fixed row order (labels absent from the data are skipped)

    Returns a display-ready frame: ROI % and P&L ($) are signed strings so
    responsive_table's signed_cols can tint them green/red.
    """
    d = completed.assign(
        _b=bucket.values,
        _win=(completed["outcome"] == "Win").astype(float),
        _stake=pd.to_numeric(completed["stake"], errors="coerce"),
        _pnl=pd.to_numeric(completed["profit_loss"], errors="coerce"),
    )
    d = d[d["_b"].notna()]
    if d.empty:
        return pd.DataFrame()

    agg = d.groupby("_b").agg(
        Bets=("_b", "size"),
        Win=("_win", "mean"),
        Staked=("_stake", "sum"),
        PnL=("_pnl", "sum"),
    )
    if order:
        agg = agg.reindex([o for o in order if o in agg.index])
    agg["ROI"] = agg["PnL"] / agg["Staked"].where(agg["Staked"] != 0) * 100

    out = agg.reset_index().rename(columns={"_b": label})
    out["Bets"]       = out["Bets"].astype(int)
    out["Win%"]       = (out["Win"] * 100).map(lambda v: f"{v:.0f}%")
    out["Staked ($)"] = out["Staked"].map(lambda v: f"${v:,.2f}")
    out["P&L ($)"]    = out["PnL"].map(lambda v: f"${v:+,.2f}")
    out["ROI %"]      = out["ROI"].map(lambda v: "—" if pd.isna(v) else f"{v:+.1f}%")
    return out[[label, "Bets", "Win%", "Staked ($)", "P&L ($)", "ROI %"]]


def calibration(completed: pd.DataFrame, min_n: int = 10) -> pd.DataFrame:
    """Predicted win prob vs. actual win rate, bucketed. Empty until min_n Win/Loss bets.

    Push/Cashout are excluded — they'd distort whether the model's probability matched reality.
    """
    d = completed.copy()
    d["_p"] = pd.to_numeric(d["model_prob"], errors="coerce")
    d = d[d["_p"].notna() & d["outcome"].isin(["Win", "Loss"])]
    if len(d) < min_n:
        return pd.DataFrame()

    d["_win"] = (d["outcome"] == "Win").astype(float)
    bins   = [0, 0.50, 0.55, 0.60, 0.65, 1.01]
    labels = ["<50%", "50–55%", "55–60%", "60–65%", "65%+"]
    d["_bin"] = pd.cut(d["_p"], bins=bins, labels=labels, right=False)

    agg = d.groupby("_bin", observed=True).agg(
        Bets=("_win", "size"),
        Predicted=("_p", "mean"),
        Actual=("_win", "mean"),
    ).reset_index()
    agg["Predicted Win%"] = (agg["Predicted"] * 100).map(lambda v: f"{v:.0f}%")
    agg["Actual Win%"]    = (agg["Actual"] * 100).map(lambda v: f"{v:.0f}%")
    return agg.rename(columns={"_bin": "Model Prob Bucket"})[
        ["Model Prob Bucket", "Bets", "Predicted Win%", "Actual Win%"]
    ]


# ── Signal-performance views (Pareto / heatmap / leaderboard / reliability) ──────
# These power the visual section shared by both tracker pages and the per-team
# win-rate note on Games & Sizing. The "team" is always the side you backed
# (bet_on); a "win" is outcome == "Win". All inputs are already user-scoped by
# the caller, so nothing here mixes users.

# The three tiers that carry an actionable edge — the ones you bet on.
SIGNAL_TIERS = TIER_ORDER[:3]   # 🔥 Strong · ✅ Value · ⚠️ Slight


def _prep_signals(completed: pd.DataFrame) -> pd.DataFrame:
    """Attach edge / tier / backed-team / win / stake / pnl helper columns."""
    d = completed.copy()
    mp = pd.to_numeric(d.get("model_prob"), errors="coerce")
    ip = pd.to_numeric(d.get("implied_prob"), errors="coerce")
    d["_edge"]  = mp - ip
    d["_imp"]   = ip
    d["_tier"]  = d["_edge"].map(signal_tier)
    d["_team"]  = d["bet_on"].astype(str).str.strip()
    d["_win"]   = (d["outcome"] == "Win").astype(float)
    d["_stake"] = pd.to_numeric(d.get("stake"), errors="coerce")
    d["_pnl"]   = pd.to_numeric(d.get("profit_loss"), errors="coerce")
    return d


def pareto_by_signal(completed: pd.DataFrame, tier: str,
                     teams: list | None = None) -> pd.DataFrame:
    """Per-team win counts for the bets that carried `tier`, ranked most→least wins.

    Teams with zero wins in this tier are dropped (a Pareto of wins — they add
    nothing, per req 1.2.4). `teams` optionally filters to a subset (None = all).
    Returns: Team, Wins, Bets, WinRate, CumPct — sorted, with the cumulative-%
    column for the Pareto overlay line.
    """
    d = _prep_signals(completed)
    d = d[d["_tier"] == tier]
    if teams is not None:
        d = d[d["_team"].isin(teams)]
    if d.empty:
        return pd.DataFrame()

    g = (d.groupby("_team")
           .agg(Wins=("_win", "sum"), Bets=("_team", "size"))
           .reset_index().rename(columns={"_team": "Team"}))
    g["Wins"] = g["Wins"].astype(int)
    g = g[g["Wins"] > 0]
    if g.empty:
        return pd.DataFrame()

    g = g.sort_values(["Wins", "Bets"], ascending=[False, True]).reset_index(drop=True)
    g["WinRate"] = g["Wins"] / g["Bets"] * 100
    g["CumPct"]  = g["Wins"].cumsum() / g["Wins"].sum() * 100
    return g


def team_signal_matrix(completed: pd.DataFrame,
                       tiers: list | None = None) -> pd.DataFrame:
    """Tidy (Team, Tier) win-rate grid for the heatmap. One row per backed
    team × tier that has at least one settled bet. Returns Team, Tier, Bets,
    Wins, WinRate. Teams are ordered by total bets (most-bet first)."""
    tiers = tiers or SIGNAL_TIERS
    d = _prep_signals(completed)
    d = d[d["_tier"].isin(tiers)]
    if d.empty:
        return pd.DataFrame()

    g = (d.groupby(["_team", "_tier"])
           .agg(Bets=("_team", "size"), Wins=("_win", "sum"))
           .reset_index().rename(columns={"_team": "Team", "_tier": "Tier"}))
    g["WinRate"] = g["Wins"] / g["Bets"] * 100
    order = g.groupby("Team")["Bets"].sum().sort_values(ascending=False).index.tolist()
    g["_o"] = g["Team"].map({t: i for i, t in enumerate(order)})
    return g.sort_values("_o").drop(columns="_o").reset_index(drop=True)


def team_leaderboard(completed: pd.DataFrame,
                     teams: list | None = None) -> pd.DataFrame:
    """Net P&L / ROI / win-rate per backed team, ranked by P&L (most profitable
    first). `teams` optionally filters. Returns Team, Bets, Wins, WinRate,
    Staked, PnL, ROI (raw numerics — the chart formats them)."""
    d = _prep_signals(completed)
    if teams is not None:
        d = d[d["_team"].isin(teams)]
    if d.empty:
        return pd.DataFrame()

    g = (d.groupby("_team")
           .agg(Bets=("_team", "size"), Wins=("_win", "sum"),
                Staked=("_stake", "sum"), PnL=("_pnl", "sum"))
           .reset_index().rename(columns={"_team": "Team"}))
    g["ROI"]     = g["PnL"] / g["Staked"].where(g["Staked"] != 0) * 100
    g["WinRate"] = g["Wins"] / g["Bets"] * 100
    return g.sort_values("PnL", ascending=False).reset_index(drop=True)


def signal_reliability(completed: pd.DataFrame) -> pd.DataFrame:
    """Per signal tier: bets, actual win%, average market break-even (implied)
    win%, and ROI. Ordered weakest→strongest edge so a rising win% reads as
    'the stronger the signal, the more it wins'. Returns Tier, Bets, WinRate,
    BreakEven, ROI."""
    d = _prep_signals(completed)
    d = d[d["_tier"].notna()]
    if d.empty:
        return pd.DataFrame()

    g = (d.groupby("_tier")
           .agg(Bets=("_tier", "size"), Wins=("_win", "sum"),
                Staked=("_stake", "sum"), PnL=("_pnl", "sum"),
                BreakEven=("_imp", "mean"))
           .reset_index().rename(columns={"_tier": "Tier"}))
    g["WinRate"]   = g["Wins"] / g["Bets"] * 100
    g["BreakEven"] = g["BreakEven"] * 100
    g["ROI"]       = g["PnL"] / g["Staked"].where(g["Staked"] != 0) * 100

    weak_to_strong = list(reversed(TIER_ORDER))   # ➖ → ⚠️ → ✅ → 🔥
    g["_o"] = g["Tier"].map({t: i for i, t in enumerate(weak_to_strong)})
    return g.sort_values("_o").drop(columns="_o").reset_index(drop=True)


def team_signal_history(combined: pd.DataFrame) -> dict:
    """Map (team_lower, tier) → (wins, n, win_rate) over settled bets, for the
    Games & Sizing note ('TEAM has won X% of games at this signal'). `combined`
    pools the user's real + paper bets — already user-scoped by the caller."""
    out: dict = {}
    if combined is None or combined.empty:
        return out
    d = _prep_signals(combined)
    d = d[d["_tier"].notna()]
    if d.empty:
        return out
    g = d.groupby(["_team", "_tier"]).agg(n=("_team", "size"), wins=("_win", "sum"))
    for (team, tier), row in g.iterrows():
        n, wins = int(row["n"]), int(row["wins"])
        out[(team.lower(), tier)] = (wins, n, (wins / n * 100) if n else 0.0)
    return out


if __name__ == "__main__":
    df = pd.DataFrame([
        dict(model_prob=0.60, implied_prob=0.50,  odds=-110, outcome="Win",  stake=10, profit_loss=9.09,
             home_team="A", away_team="B", bet_on="A", game_date="2026-04-01"),
        dict(model_prob=0.58, implied_prob=0.55,  odds=-120, outcome="Loss", stake=10, profit_loss=-10.0,
             home_team="A", away_team="B", bet_on="B", game_date="2026-04-15"),
        dict(model_prob=0.52, implied_prob=0.515, odds=+100, outcome="Win",  stake=10, profit_loss=10.0,
             home_team="C", away_team="D", bet_on="D", game_date="2026-05-02"),
    ])

    assert signal_tier(0.10) == TIER_ORDER[0]
    assert signal_tier(0.05) == TIER_ORDER[1]
    assert signal_tier(0.02) == TIER_ORDER[2]
    assert signal_tier(0.00) == TIER_ORDER[3]
    assert signal_tier(float("nan")) is None

    tiers = df["model_prob"].sub(df["implied_prob"]).map(signal_tier)
    t = roi_breakdown(df, tiers, "Signal Tier", order=TIER_ORDER)
    assert t["Bets"].sum() == 3
    strong = t[t["Signal Tier"] == TIER_ORDER[0]].iloc[0]
    assert strong["ROI %"].startswith("+90"), strong["ROI %"]   # 9.09 / 10 staked

    side = pd.to_numeric(df["odds"]).map(lambda o: "Favorite" if o < 0 else "Underdog")
    s = roi_breakdown(df, side, "Side")
    assert set(s["Side"]) == {"Favorite", "Underdog"}
    fav = s[s["Side"] == "Favorite"].iloc[0]
    assert fav["ROI %"].startswith("-"), fav["ROI %"]           # -0.91 / 20 staked

    assert calibration(df).empty                                # only 2 Win/Loss bets < min_n

    # ── Signal-performance views ────────────────────────────────────────────────
    sig = pd.DataFrame([
        # Yankees: 3 strong (2 W, 1 L), 1 value (W), 1 slight (L)
        dict(model_prob=0.62, implied_prob=0.50, odds=-110, outcome="Win",  stake=10, profit_loss=9.09,  bet_on="Yankees", home_team="Yankees", away_team="Rays"),
        dict(model_prob=0.61, implied_prob=0.50, odds=-110, outcome="Win",  stake=10, profit_loss=9.09,  bet_on="Yankees", home_team="Yankees", away_team="Rays"),
        dict(model_prob=0.63, implied_prob=0.50, odds=-110, outcome="Loss", stake=10, profit_loss=-10.0, bet_on="Yankees", home_team="Yankees", away_team="Rays"),
        dict(model_prob=0.56, implied_prob=0.50, odds=-110, outcome="Win",  stake=10, profit_loss=9.09,  bet_on="Yankees", home_team="Yankees", away_team="Rays"),
        dict(model_prob=0.52, implied_prob=0.50, odds=-110, outcome="Loss", stake=10, profit_loss=-10.0, bet_on="Yankees", home_team="Yankees", away_team="Rays"),
        # Dodgers: 1 strong (W); Mets: 1 strong (L, zero wins → must not appear in Pareto)
        dict(model_prob=0.64, implied_prob=0.50, odds=-110, outcome="Win",  stake=10, profit_loss=9.09,  bet_on="Dodgers", home_team="Dodgers", away_team="Padres"),
        dict(model_prob=0.60, implied_prob=0.50, odds=-110, outcome="Loss", stake=10, profit_loss=-10.0, bet_on="Mets",    home_team="Mets",    away_team="Braves"),
    ])

    strong = TIER_ORDER[0]
    par = pareto_by_signal(sig, strong)
    assert list(par["Team"]) == ["Yankees", "Dodgers"], list(par["Team"])  # ranked by wins; Mets (0 W) dropped
    assert par.iloc[0]["Wins"] == 2 and par.iloc[-1]["CumPct"] == 100.0
    assert pareto_by_signal(sig, strong, teams=["Dodgers"])["Team"].tolist() == ["Dodgers"]

    mat = team_signal_matrix(sig)
    yk_strong = mat[(mat["Team"] == "Yankees") & (mat["Tier"] == strong)].iloc[0]
    assert yk_strong["Bets"] == 3 and abs(yk_strong["WinRate"] - 66.67) < 0.1

    lb = team_leaderboard(sig)
    assert lb.iloc[0]["Team"] == "Dodgers"          # +9.09 P&L, ranked first
    assert lb.iloc[-1]["Team"] == "Mets"            # -10 P&L, ranked last

    rel = signal_reliability(sig)
    assert list(rel["Tier"]) == [TIER_ORDER[2], TIER_ORDER[1], TIER_ORDER[0]]  # weak→strong, present tiers only

    hist = team_signal_history(sig)
    assert hist[("yankees", strong)][:2] == (2, 3)
    assert ("mets", strong) in hist and hist[("mets", strong)][:2] == (0, 1)

    print("bet_analytics self-check passed")
