import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from ingestion.stats_scraper import get_full_team_stats
from theme import init_theme, palette

st.set_page_config(page_title="Stats Explorer", page_icon="📊", layout="wide")
init_theme("#0f766e")   # teal — stats explorer

st.title("📊 Team Stats Explorer")
st.caption("Season-level data scraped from Baseball Reference — the features powering the model")

@st.cache_data(ttl=3600)
def load_stats():
    return get_full_team_stats()

with st.spinner("Loading stats..."):
    df = load_stats()

if df.empty:
    st.error("Could not load stats.")
    st.stop()

# --- Overview Table ---
st.subheader("League Standings & Key Metrics")

display_cols = ["team", "wins", "losses", "win_pct", "run_diff", "pythag_pct"]
display_df = df[display_cols].copy()
display_df["win_pct"]    = (display_df["win_pct"]    * 100).round(1).astype(str) + "%"
display_df["pythag_pct"] = (display_df["pythag_pct"] * 100).round(1).astype(str) + "%"
display_df["run_diff"]   = display_df["run_diff"].map(lambda x: f"+{int(x)}" if x > 0 else str(int(x)))
if "last_ten_wins" in df.columns:
    display_df["last_ten"] = df["last_ten_wins"].apply(
        lambda w: f"{int(w)}-{10 - int(w)}" if pd.notna(w) else "—"
    )
    display_df.columns = ["Team", "W", "L", "Win%", "Run Diff", "Pythag%", "Last 10"]
else:
    display_df.columns = ["Team", "W", "L", "Win%", "Run Diff", "Pythag%"]
display_df = display_df.sort_values("W", ascending=False)

st.dataframe(display_df, use_container_width=True, hide_index=True)

st.divider()

# --- Scatter: Win% vs Pythagorean ---
st.subheader("Actual Win% vs. Pythagorean Expected Win%")
st.caption("Teams above the line are 'lucky' (winning more than expected). Below = 'unlucky'. Pythagorean % is often a better predictor of future performance.")

fig = px.scatter(
    df, x="pythag_pct", y="win_pct",
    text="team",
    labels={"pythag_pct": "Pythagorean Win%", "win_pct": "Actual Win%"},
    color="run_diff",
    color_continuous_scale="RdYlGn",
    template=palette()["plotly_template"],
)
fig.add_shape(type="line", x0=0.3, y0=0.3, x1=0.7, y1=0.7,
              line=dict(color="gray", dash="dash", width=1))
fig.update_traces(textposition="top center", marker=dict(size=10))
_c = palette()
fig.update_layout(
    height=550,
    paper_bgcolor=_c["plot_paper"],
    plot_bgcolor=_c["plot_bg"],
    font=dict(family="Manrope", color=_c["plot_font"]),
    coloraxis_colorbar=dict(title="Run Diff"),
)
st.plotly_chart(fig, use_container_width=True)

st.divider()

# --- Home vs Away Splits ---
if "home_win_pct" in df.columns and "away_win_pct" in df.columns:
    st.subheader("Home vs. Away Win%")

    split_df = df[["team", "home_win_pct", "away_win_pct", "win_pct"]].copy()
    split_df = split_df.sort_values("home_win_pct", ascending=False)

    fig2 = go.Figure()
    _c2 = palette()
    fig2.add_trace(go.Bar(
        name="Home Win%", x=split_df["team"],
        y=(split_df["home_win_pct"] * 100).round(1),
        marker_color=_c2["plot_green"]
    ))
    fig2.add_trace(go.Bar(
        name="Away Win%", x=split_df["team"],
        y=(split_df["away_win_pct"] * 100).round(1),
        marker_color=_c2["plot_blue"]
    ))
    fig2.update_layout(
        barmode="group",
        template=_c2["plotly_template"],
        height=500,
        paper_bgcolor=_c2["plot_paper"],
        plot_bgcolor=_c2["plot_bg"],
        font=dict(family="Manrope", color=_c2["plot_font"]),
        yaxis_title="Win%",
    )
    st.plotly_chart(fig2, use_container_width=True)

st.divider()

# --- Team Deep Dive ---
st.subheader("Team Deep Dive")
selected_team = st.selectbox("Select a team", sorted(df["team"].dropna().tolist()))
team_row = df[df["team"] == selected_team]

if not team_row.empty:
    r = team_row.iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Record", f"{int(r['wins'])}-{int(r['losses'])}")
    c2.metric("Win%", f"{r['win_pct']*100:.1f}%")
    c3.metric("Pythagorean%", f"{r.get('pythag_pct', r['win_pct'])*100:.1f}%")
    c4.metric("Run Differential", f"{'+' if r['run_diff'] > 0 else ''}{int(r['run_diff'])}")

    if "home_win_pct" in r:
        c5, c6 = st.columns(2)
        c5.metric("Home Win%", f"{r['home_win_pct']*100:.1f}%")
        c6.metric("Away Win%", f"{r['away_win_pct']*100:.1f}%")

    luck_diff = r["win_pct"] - r.get("pythag_pct", r["win_pct"])
    if abs(luck_diff) > 0.02:
        direction = "overperforming" if luck_diff > 0 else "underperforming"
        st.info(f"📌 {selected_team} is **{direction}** their Pythagorean expectation by {abs(luck_diff)*100:.1f} points — regression to the mean likely.")
