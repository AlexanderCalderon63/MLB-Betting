"""
pages/5_Model_Performance.py — Model calibration and signal tier performance
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from database import get_connection, init_db
from models.predictor import MODEL_PATH, VALUE_THRESHOLD, MLBPredictor
from ingestion.historical_scraper import build_and_store_season, backfill_pitcher_data, backfill_last_ten, get_historical_summary
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from theme import init_theme, palette
from ui import responsive_chart, responsive_table

init_db()


def _signal_tier(edge: float) -> str:
    if edge >= 0.08:
        return "🔥 Strong Value"
    elif edge >= VALUE_THRESHOLD:
        return "✅ Value Bet"
    elif edge >= 0.01:
        return "⚠️ Slight Edge"
    elif edge <= -VALUE_THRESHOLD:
        return "❌ Avoid"
    else:
        return "➖ No Edge"

def _run_seasonal_holdout() -> tuple:
    """
    Leave-one-season-out cross-validation on historical_games.
    For each season, trains a fresh model on all other seasons and predicts on that season.
    Returns (results_df, error_message_or_None).
    """
    conn = get_connection()
    df = pd.read_sql("SELECT * FROM historical_games ORDER BY game_date", conn)
    conn.close()

    if df.empty:
        return pd.DataFrame(), "No historical data found."

    TEAM_COLS    = ["win_pct_diff", "pythag_diff", "run_diff_diff", "rs_diff", "ra_diff", "home_advantage"]
    PITCHER_RAW  = ["home_sp_era", "home_sp_whip", "home_sp_k9", "home_sp_bb9",
                    "away_sp_era", "away_sp_whip", "away_sp_k9", "away_sp_bb9"]
    PITCHER_DIFF = ["sp_era_diff", "sp_whip_diff", "sp_k9_diff", "sp_bb9_diff"]

    seasons = sorted(df["season"].dropna().astype(int).unique())
    if len(seasons) < 2:
        return pd.DataFrame(), (
            f"Need at least 2 seasons to run backtesting (found: {seasons}). "
            "Use the Fetch Season buttons above to add more historical data."
        )

    has_pitcher_schema = all(c in df.columns for c in PITCHER_RAW)

    def _build_Xy(frame, use_pitcher):
        frame = frame.copy()
        if use_pitcher:
            frame = frame.dropna(subset=["home_sp_era", "away_sp_era"])
            if frame.empty:
                return None, None
            frame["sp_era_diff"]  = frame["away_sp_era"]  - frame["home_sp_era"]
            frame["sp_whip_diff"] = frame["away_sp_whip"] - frame["home_sp_whip"]
            frame["sp_k9_diff"]   = frame["home_sp_k9"]   - frame["away_sp_k9"]
            frame["sp_bb9_diff"]  = frame["away_sp_bb9"]  - frame["home_sp_bb9"]
            cols = TEAM_COLS + PITCHER_DIFF
        else:
            cols = TEAM_COLS
        frame = frame.dropna(subset=cols)
        return frame[cols].copy(), frame["home_win"].astype(int)

    records = []

    for test_season in seasons:
        train_df = df[df["season"] != test_season]
        test_df  = df[df["season"] == test_season]

        # Decide feature set based on pitcher data availability in training split
        use_pitcher = False
        if has_pitcher_schema:
            n_with_p = train_df["home_sp_era"].notna().sum()
            use_pitcher = n_with_p >= 100

        X_train, y_train = _build_Xy(train_df, use_pitcher)
        if X_train is None or len(X_train) < 50:
            use_pitcher = False
            X_train, y_train = _build_Xy(train_df, False)
        if X_train is None or len(X_train) < 50:
            continue

        X_test, y_test = _build_Xy(test_df, use_pitcher)
        if X_test is None or len(X_test) < 10:
            X_test, y_test = _build_Xy(test_df, False)
            if X_test is None or len(X_test) < 10:
                continue
            # Realign training to team-only if test fell back
            if use_pitcher:
                X_train, y_train = _build_Xy(train_df, False)

        pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("lr", LogisticRegression(C=1.0, max_iter=1000)),
        ])
        pipe.fit(X_train, y_train)
        probs = pipe.predict_proba(X_test)[:, 1]

        for prob, actual in zip(probs, y_test):
            records.append({
                "season":        int(test_season),
                "model_prob":    round(float(prob), 4),
                "home_win":      int(actual),
                "predicted_win": int(prob >= 0.5),
            })

    if not records:
        return pd.DataFrame(), "No valid season pairs found for cross-validation."

    return pd.DataFrame(records), None


st.set_page_config(page_title="Model Performance", page_icon="📊", layout="wide")
init_theme("#2563eb")   # blue — model performance

st.title("📊 Model Performance")
st.caption("Calibration and signal tier analysis — how well does the model's confidence match reality?")

# ── Model status ───────────────────────────────────────────────────────────────

st.subheader("Model Status")

model_trained = os.path.exists(MODEL_PATH)

if model_trained:
    import joblib
    try:
        saved = joblib.load(MODEL_PATH)
        n_samples     = saved.get("n_samples", 0)
        with_pitchers = saved.get("trained_with_pitchers", False)
        best_C        = saved.get("best_C", None)
        cv_brier      = saved.get("cv_brier", None)
        cal_method    = saved.get("cal_method", None)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Training Samples", f"{n_samples:,}")
        c2.metric("Pitcher Features", "Yes" if with_pitchers else "No")
        c3.metric("Best C",           str(best_C) if best_C is not None else "—",
                  help="Regularization strength selected by cross-validation. Lower = more conservative.")
        c4.metric("CV Brier Score",   f"{cv_brier:.4f}" if cv_brier is not None else "—",
                  help="Cross-validated Brier score. Lower is better. 0.25 = random, ~0.22 is typical for sports.")
    except Exception:
        st.warning("Model file found but could not be read.")
else:
    st.warning(
        "Model is running in **heuristic mode** — no trained model found. "
        "Run `python scripts/train_model.py` to train on historical data."
    )

# ── Training data & retrain ────────────────────────────────────────────────────

st.subheader("Training Data")

db_summary = get_historical_summary()
conn = get_connection()
paper_training_count = 0
pitcher_coverage = {}
try:
    paper_training_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM paper_bets WHERE outcome IN ('Win','Loss') AND win_pct_diff IS NOT NULL"
    ).fetchone()["cnt"]
    rows = conn.execute("""
        SELECT season,
               COUNT(*) as total,
               SUM(CASE WHEN home_sp_era IS NOT NULL THEN 1 ELSE 0 END) as with_pitcher
        FROM historical_games GROUP BY season
    """).fetchall()
    for row in rows:
        pitcher_coverage[int(row["season"])] = (int(row["total"]), int(row["with_pitcher"]))
except Exception:
    pass
conn.close()

if db_summary:
    summary_rows = []
    for s, c in sorted(db_summary.items()):
        total, with_p = pitcher_coverage.get(int(s), (c, 0))
        pct = f"{with_p / total * 100:.0f}%" if total > 0 else "0%"
        summary_rows.append({"Season": str(s), "Games": f"{c:,}", "Pitcher Data": pct})
    summary_rows.append({"Season": "Total", "Games": f"{sum(db_summary.values()):,}", "Pitcher Data": ""})
    col_tbl, col_spacer = st.columns([1.5, 1.5])
    with col_tbl:
        responsive_table(pd.DataFrame(summary_rows), key="mp_training", numeric_cols=["Games", "Pitcher Data"])
    if paper_training_count:
        st.caption(f"+ {paper_training_count} completed paper bet(s) with feature data also included in training.")
else:
    st.caption("No training data found in DB.")

# ── Fetch historical season / backfill pitcher controls ────────────────────────

fc1, fc2, fc3 = st.columns(3)

with fc1:
    st.markdown("**Fetch Historical Season**")
    all_years     = [2021, 2022, 2023, 2024, 2025, 2026]
    loaded        = set(db_summary.keys()) if db_summary else set()
    fetchable     = [y for y in all_years if y not in loaded]
    if fetchable:
        yr = st.selectbox("Season", fetchable, key="fetch_yr")
        if st.button("⬇️ Fetch Season", key="fetch_btn"):
            with st.spinner(f"Fetching {yr} season from MLB Stats API (may take a few minutes)..."):
                n = build_and_store_season(yr)
            st.success(f"Added {n:,} games for {yr}. Retrain the model to include them.")
            st.rerun()
    else:
        st.caption("All seasons 2021–2026 are already loaded.")

with fc2:
    st.markdown("**Backfill Pitcher Data**")
    needs_backfill = [
        s for s, (total, with_p) in pitcher_coverage.items()
        if total > 0 and with_p / total < 0.5
    ]
    if needs_backfill:
        seasons_sel = st.multiselect(
            "Seasons to backfill",
            sorted(needs_backfill),
            default=sorted(needs_backfill),
            key="bf_sel",
        )
        st.caption("⚠️ Allow 10–15 min per season — keep this tab open.")
        if st.button("⬇️ Backfill Pitcher Stats", key="bf_btn") and seasons_sel:
            with st.spinner(f"Backfilling pitcher data for {seasons_sel}..."):
                backfill_pitcher_data(seasons_sel)
            st.success("Pitcher data backfilled. Retrain the model to use it.")
            st.rerun()
    else:
        st.caption("All loaded seasons have pitcher data (≥ 50% coverage).")

with fc3:
    st.markdown("**Backfill Last-10 Records**")
    conn_lt = get_connection()
    needs_lt = []
    for s in sorted(db_summary.keys()):
        row = conn_lt.execute(
            "SELECT COUNT(*) as total, SUM(CASE WHEN home_last_ten_wins IS NOT NULL THEN 1 ELSE 0 END) as filled "
            "FROM historical_games WHERE season = ?", (s,)
        ).fetchone()
        if row and row["total"] > 0 and (row["filled"] or 0) / row["total"] < 0.5:
            needs_lt.append(s)
    conn_lt.close()

    if needs_lt:
        lt_sel = st.multiselect("Seasons to backfill", sorted(needs_lt), default=sorted(needs_lt), key="lt_sel")
        st.caption("⚠️ Allow ~2–3 min per season.")
        if st.button("⬇️ Backfill Last-10 Records", key="lt_btn") and lt_sel:
            with st.spinner(f"Backfilling last-10 records for {lt_sel}..."):
                backfill_last_ten(lt_sel)
            st.success("Last-10 records backfilled. Retrain the model to use them.")
            st.rerun()
    else:
        st.caption("All loaded seasons have last-10 records (≥ 50% coverage).")

# ── Retrain ────────────────────────────────────────────────────────────────────

if "retrain_msg" in st.session_state:
    msg_type, msg_text = st.session_state.pop("retrain_msg")
    if msg_type == "success":
        st.success(msg_text)
    else:
        st.error(msg_text)

_conn_last = get_connection()
_last_date_row = _conn_last.execute(
    "SELECT MAX(game_date) as last_date FROM historical_games WHERE season = 2026"
).fetchone()
_conn_last.close()
_last_2026_date = _last_date_row["last_date"] if _last_date_row else None
_btn_label = (
    f"🔄 Fetch 2026 Games since {_last_2026_date} & Retrain"
    if _last_2026_date
    else "🔄 Fetch 2026 Games & Retrain"
)

if st.button(_btn_label, type="primary"):
    with st.spinner("Fetching completed 2026 games and retraining model..."):
        new_games = build_and_store_season(2026, start_date=_last_2026_date)
        fresh = MLBPredictor()
        success = fresh.load_and_train()
    if success:
        st.session_state["retrain_msg"] = (
            "success",
            f"Model retrained on {fresh.n_samples:,} games — {new_games} new 2026 game(s) added.",
        )
    else:
        st.session_state["retrain_msg"] = (
            "error",
            "Retraining failed — check that historical data is in the DB.",
        )
    st.rerun()

st.divider()

# ── Load resolved bets (real + paper combined) ─────────────────────────────────

conn = get_connection()
real_raw  = pd.read_sql("SELECT * FROM bets ORDER BY game_date", conn)
paper_raw = pd.DataFrame()
try:
    paper_raw = pd.read_sql("SELECT * FROM paper_bets ORDER BY game_date", conn)
except Exception:
    pass
conn.close()

real_resolved  = real_raw[real_raw["outcome"].isin(["Win", "Loss"])].copy()
real_resolved["source"] = "Real"

paper_resolved = paper_raw[paper_raw["outcome"].isin(["Win", "Loss"])].copy() if not paper_raw.empty else pd.DataFrame()
if not paper_resolved.empty:
    paper_resolved["source"] = "Paper"

n_real  = len(real_resolved)
n_paper = len(paper_resolved) if not paper_resolved.empty else 0

st.divider()

include_paper = st.toggle(
    "Include paper bets in analysis",
    value=True,
    help="When on, paper bets with recorded outcomes are combined with real bets for all charts. Turn off to evaluate real bets only."
)

if include_paper and not paper_resolved.empty:
    resolved = pd.concat([real_resolved, paper_resolved], ignore_index=True)
else:
    resolved = real_resolved.copy()

for col in ["model_prob", "implied_prob"]:
    resolved[col] = pd.to_numeric(resolved[col], errors="coerce")

resolved["won"]         = (resolved["outcome"] == "Win").astype(int)
resolved["edge"]        = resolved["model_prob"] - resolved["implied_prob"]
resolved["signal_tier"] = resolved["edge"].apply(_signal_tier)

_c = palette()
n_shown = len(resolved)
scope_label = f"{n_real} real · {n_paper} paper" if include_paper else f"{n_real} real only"

enough_bets = len(resolved) >= 5
if not enough_bets:
    label = f"{n_real} real" if not include_paper else f"{n_real} real + {n_paper} paper"
    st.info(
        f"**{len(resolved)} resolved bet(s) in scope ({label}).** "
        "Calibration charts need at least 20-30 bets across different probability ranges "
        "to be meaningful. Keep logging outcomes in the Bet Tracker and Paper Bet Tracker."
    )
else:
    st.markdown(
        f'<div style="font-size:0.82rem; color:{_c["muted"]}; margin-bottom:0.5rem;">'
        f"Analyzing <strong>{n_shown}</strong> resolved bets — {scope_label}."
        f"</div>",
        unsafe_allow_html=True,
    )

_c = palette()
_tmpl = _c["plotly_template"]

if enough_bets:
    # ── Calibration plot ───────────────────────────────────────────────────────

    st.subheader("Probability Calibration")
    st.caption(
        "Each dot is a bucket of bets grouped by model confidence. "
        "Points on the diagonal = perfectly calibrated. Above = model underestimates; below = overestimates."
    )

    bins = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 1.01]
    labels = ["40-45%", "45-50%", "50-55%", "55-60%", "60-65%", "65-70%", "70%+"]

    resolved["prob_bin"] = pd.cut(resolved["model_prob"], bins=bins, labels=labels, right=False)
    calib = (
        resolved.groupby("prob_bin", observed=True)
        .agg(
            predicted=("model_prob", "mean"),
            actual=("won", "mean"),
            n=("won", "count"),
        )
        .reset_index()
        .dropna()
    )

    MIN_BUCKET = 3
    calib_plot = calib[calib["n"] >= MIN_BUCKET]

    fig_calib = go.Figure()
    fig_calib.add_trace(go.Scatter(
        x=[0.40, 0.75], y=[0.40, 0.75],
        mode="lines",
        line=dict(color="gray", dash="dash", width=1),
        name="Perfect calibration",
        showlegend=True,
    ))

    if not calib_plot.empty:
        fig_calib.add_trace(go.Scatter(
            x=calib_plot["predicted"],
            y=calib_plot["actual"],
            mode="markers+text",
            marker=dict(
                size=calib_plot["n"].clip(upper=50) * 2 + 8,
                color=_c["plot_blue"],
                line=dict(color="white", width=1),
            ),
            text=calib_plot["n"].astype(str) + " bets",
            textposition="top center",
            name="Model buckets",
        ))

    fig_calib.update_layout(
        template=_tmpl,
        paper_bgcolor=_c["plot_paper"],
        plot_bgcolor=_c["plot_bg"],
        font=dict(family="Manrope", color=_c["plot_font"]),
        xaxis=dict(title="Model predicted probability", tickformat=".0%", range=[0.38, 0.77]),
        yaxis=dict(title="Actual win rate", tickformat=".0%", range=[0.38, 0.77]),
        height=420,
        legend=dict(x=0.02, y=0.98),
    )
    responsive_chart(fig_calib, key="mp_calib")

    if not calib_plot.empty:
        brier = ((resolved["model_prob"] - resolved["won"]) ** 2).mean()
        st.caption(f"Brier score: **{brier:.4f}** (lower is better; 0.25 = random, 0.00 = perfect)")

    st.divider()

    # ── Signal tier breakdown ──────────────────────────────────────────────────

    st.subheader("Performance by Signal Tier")
    st.caption("How do bets at each edge level actually perform?")

    tier_order = ["🔥 Strong Value", "✅ Value Bet", "⚠️ Slight Edge", "➖ No Edge", "❌ Avoid"]
    tier_stats = (
        resolved.groupby("signal_tier")
        .agg(
            bets=("won", "count"),
            win_rate=("won", "mean"),
            avg_edge=("edge", "mean"),
        )
        .reset_index()
    )
    valid_tiers = [t for t in tier_order if t in tier_stats["signal_tier"].values]
    tier_stats = tier_stats.set_index("signal_tier").reindex(valid_tiers).reset_index().dropna(subset=["bets"])

    if not tier_stats.empty:
        fig_wr = go.Figure(go.Bar(
            x=tier_stats["signal_tier"],
            y=(tier_stats["win_rate"] * 100).round(1),
            marker_color=[_c["plot_green"] if v >= 50 else _c["plot_red"] for v in tier_stats["win_rate"] * 100],
            text=(tier_stats["win_rate"] * 100).round(1).astype(str) + "%",
            textposition="outside",
        ))
        fig_wr.add_hline(y=50, line_dash="dash", line_color="gray")
        fig_wr.update_layout(
            title="Win Rate by Signal Tier",
            template=_tmpl,
            paper_bgcolor=_c["plot_paper"],
            plot_bgcolor=_c["plot_bg"],
            font=dict(family="Manrope", color=_c["plot_font"]),
            yaxis=dict(title="Win Rate (%)", range=[0, 100]),
            xaxis_title=None,
            height=380,
        )
        responsive_chart(fig_wr, key="mp_winrate")

        display = tier_stats.copy()
        display["win_rate"] = (display["win_rate"] * 100).round(1).astype(str) + "%"
        display["avg_edge"]  = (display["avg_edge"]  * 100).round(1).astype(str) + "%"
        display.columns = ["Signal Tier", "Bets", "Win Rate", "Avg Edge"]
        responsive_table(display, key="mp_tiers", numeric_cols=["Bets", "Win Rate", "Avg Edge"])

    st.divider()

    # ── Edge vs outcome scatter ────────────────────────────────────────────────

    st.subheader("Edge vs Outcome")
    st.caption("Each dot is one bet. Green = win, red = loss. A rightward cluster of wins = model edge is real.")

    if len(resolved) >= 10:
        fig_scatter = go.Figure()
        for outcome, color, label in [("Win", _c["plot_green"], "Win"), ("Loss", _c["plot_red"], "Loss")]:
            sub = resolved[resolved["outcome"] == outcome]
            fig_scatter.add_trace(go.Scatter(
                x=(sub["edge"] * 100).round(1),
                y=(sub["model_prob"] * 100).round(1),
                mode="markers",
                marker=dict(color=color, size=8, opacity=0.7),
                name=label,
                text=sub["away_team"] + " @ " + sub["home_team"] + "<br>Bet: " + sub["bet_on"],
                hovertemplate="%{text}<br>Edge: %{x:.1f}%<br>Model prob: %{y:.1f}%<extra></extra>",
            ))
        fig_scatter.add_vline(x=VALUE_THRESHOLD * 100, line_dash="dash", line_color=_c["plot_amber"],
                              annotation_text="4% threshold", annotation_font_color=_c["plot_amber"])
        fig_scatter.add_vline(x=0, line_dash="dot", line_color="gray")
        fig_scatter.update_layout(
            template=_tmpl,
            paper_bgcolor=_c["plot_paper"],
            plot_bgcolor=_c["plot_bg"],
            font=dict(family="Manrope", color=_c["plot_font"]),
            xaxis_title="Edge (model prob − market implied prob, %)",
            yaxis_title="Model probability (%)",
            height=400,
        )
        responsive_chart(fig_scatter, key="mp_scatter")

    st.divider()

# ── Historical Backtesting ─────────────────────────────────────────────────────

with st.expander("📈 Historical Backtesting (Out-of-Sample)", expanded=False):
    st.caption(
        "Leave-one-season-out cross-validation: for each season in the DB, a fresh model trains on all "
        "other seasons and predicts that season's games. These are genuine out-of-sample predictions — "
        "the model never saw those games during training."
    )

    if st.button("▶ Run Backtesting", key="run_bt"):
        with st.spinner("Running cross-validation across seasons..."):
            bt_df, bt_error = _run_seasonal_holdout()
        st.session_state["bt_df"]    = bt_df
        st.session_state["bt_error"] = bt_error

    bt_df    = st.session_state.get("bt_df",    None)
    bt_error = st.session_state.get("bt_error", None)

    if bt_df is None:
        st.info("Click **Run Backtesting** above to generate results.")
    elif bt_error:
        st.info(bt_error)
    else:
        n_games        = len(bt_df)
        seasons_tested = sorted(bt_df["season"].unique())
        model_acc      = (bt_df["predicted_win"] == bt_df["home_win"]).mean()
        home_baseline  = bt_df["home_win"].mean()
        brier_bt       = ((bt_df["model_prob"] - bt_df["home_win"]) ** 2).mean()
        acc_color      = _c["green"] if model_acc > home_baseline else _c["red"]

        # Summary stat cards
        st.markdown(f"""
<div style="display:flex; gap:12px; margin:1rem 0 1.5rem 0; flex-wrap:wrap;">
  <div class="stat-box" style="flex:1; min-width:120px;">
    <div style="font-size:0.72rem; color:{_c['muted']}; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px;">Games Tested</div>
    <div style="font-size:2rem; font-weight:800; font-family:'Manrope',sans-serif; color:{_c['text']};">{n_games:,}</div>
  </div>
  <div class="stat-box" style="flex:1; min-width:120px;">
    <div style="font-size:0.72rem; color:{_c['muted']}; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px;">Seasons</div>
    <div style="font-size:2rem; font-weight:800; font-family:'Manrope',sans-serif; color:{_c['text']};">{len(seasons_tested)}</div>
  </div>
  <div class="stat-box" style="flex:1; min-width:120px;">
    <div style="font-size:0.72rem; color:{_c['muted']}; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px;">Model Accuracy</div>
    <div style="font-size:2rem; font-weight:800; font-family:'Manrope',sans-serif; color:{acc_color};">{model_acc*100:.1f}%</div>
  </div>
  <div class="stat-box" style="flex:1; min-width:120px;">
    <div style="font-size:0.72rem; color:{_c['muted']}; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px;">Home Baseline</div>
    <div style="font-size:2rem; font-weight:800; font-family:'Manrope',sans-serif; color:{_c['text']};">{home_baseline*100:.1f}%</div>
  </div>
  <div class="stat-box" style="flex:1; min-width:120px;" title="Brier score: lower is better. 0.25 = coin flip, 0.00 = perfect.">
    <div style="font-size:0.72rem; color:{_c['muted']}; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px;">Brier Score ⓘ</div>
    <div style="font-size:2rem; font-weight:800; font-family:'Manrope',sans-serif; color:{_c['text']};">{brier_bt:.4f}</div>
  </div>
</div>
""", unsafe_allow_html=True)

        # Calibration chart
        bins_bt   = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 1.01]
        labels_bt = ["40-45%", "45-50%", "50-55%", "55-60%", "60-65%", "65-70%", "70%+"]
        bt_df["prob_bin"] = pd.cut(bt_df["model_prob"], bins=bins_bt, labels=labels_bt, right=False)

        calib_bt = (
            bt_df.groupby("prob_bin", observed=True)
            .agg(predicted=("model_prob", "mean"), actual=("home_win", "mean"), n=("home_win", "count"))
            .reset_index()
            .dropna()
        )
        calib_bt_plot = calib_bt[calib_bt["n"] >= 10]

        fig_bt = go.Figure()
        fig_bt.add_trace(go.Scatter(
            x=[0.40, 0.75], y=[0.40, 0.75],
            mode="lines",
            line=dict(color="gray", dash="dash", width=1),
            name="Perfect calibration",
        ))
        if not calib_bt_plot.empty:
            dot_sizes = (calib_bt_plot["n"] / calib_bt_plot["n"].max() * 30 + 10).clip(lower=10)
            fig_bt.add_trace(go.Scatter(
                x=calib_bt_plot["predicted"],
                y=calib_bt_plot["actual"],
                mode="markers+text",
                marker=dict(size=dot_sizes, color=_c["plot_blue"], line=dict(color="white", width=1)),
                text=calib_bt_plot["n"].astype(str) + " games",
                textposition="top center",
                name="Model buckets",
            ))
        fig_bt.update_layout(
            title="Historical Calibration (Out-of-Sample)",
            template=_tmpl,
            paper_bgcolor=_c["plot_paper"],
            plot_bgcolor=_c["plot_bg"],
            font=dict(family="Manrope", color=_c["plot_font"]),
            xaxis=dict(title="Model predicted probability", tickformat=".0%", range=[0.38, 0.77]),
            yaxis=dict(title="Actual win rate", tickformat=".0%", range=[0.38, 0.77]),
            height=420,
            legend=dict(x=0.02, y=0.98),
        )
        responsive_chart(fig_bt, key="mp_backtest")

        # Season-by-season breakdown table
        st.markdown("**Accuracy by Season**")
        season_rows = []
        for ssn in seasons_tested:
            sg = bt_df[bt_df["season"] == ssn]
            season_rows.append({
                "Season":          int(ssn),
                "Games":           len(sg),
                "Model Accuracy":  f"{(sg['predicted_win'] == sg['home_win']).mean()*100:.1f}%",
                "Home Win Rate":   f"{sg['home_win'].mean()*100:.1f}%",
                "Brier Score":     f"{((sg['model_prob'] - sg['home_win'])**2).mean():.4f}",
            })
        responsive_table(pd.DataFrame(season_rows), key="mp_season",
                         numeric_cols=["Season", "Games", "Model Accuracy", "Home Win Rate", "Brier Score"])
        st.caption(
            "Model Accuracy = % of games where model picked the correct winner (prob > 0.5). "
            "Home Win Rate = naive baseline of always picking the home team."
        )
