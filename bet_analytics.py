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

    print("bet_analytics self-check passed")
