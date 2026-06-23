"""bet_charts.py — the "Signal Performance" visual section for both tracker pages.

Pairs with bet_analytics.py (which shapes the data, pure pandas) and ui.py (which
renders touch-friendly Plotly). This module owns the Plotly figure builders and a
single `render_signal_performance()` orchestrator so the Bet Tracker and Paper Bet
Tracker pages each drop the whole section in with one call — same look, own data.

Colours always come from theme.palette(); signal tiers reuse the exact hues the
recommendation badges already use on Games & Sizing, so a 🔥 amber badge there and
the amber Pareto here read as the same thing:

    🔥 Strong → amber   ✅ Value → green   ⚠️ Slight → accent (blue/violet/cyan per page)

The cumulative Pareto line is deep navy ink, not the textbook red — in this app red
means a loss, so a red overlay would fight the meaning. Navy stays neutral.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from theme import palette
from ui import responsive_chart
from bet_analytics import (
    SIGNAL_TIERS,
    pareto_by_signal,
    team_signal_matrix,
    team_leaderboard,
    signal_reliability,
)


def _tier_color(tier: str, c: dict) -> str:
    """Tier → the same semantic hue used by the rec badges elsewhere."""
    if tier.startswith("🔥"):
        return c["amber"]
    if tier.startswith("✅"):
        return c["green"]
    if tier.startswith("⚠️"):
        return c["accent"]
    return c["muted"]


def _short_tier(tier: str) -> str:
    """'🔥 Strong (≥8%)' → '🔥 Strong' for compact tabs / axes."""
    return tier.split(" (")[0]


def _base_layout(fig: go.Figure, c: dict, height: int) -> None:
    fig.update_layout(
        template=c["plotly_template"],
        paper_bgcolor=c["plot_paper"],
        plot_bgcolor=c["plot_bg"],
        font=dict(family="Manrope", color=c["plot_font"]),
        height=height,
        margin=dict(l=8, r=10, t=24, b=8),
    )


# ── Figure builders ──────────────────────────────────────────────────────────────

def pareto_figure(df: pd.DataFrame, tier: str, c: dict) -> go.Figure:
    """Bars = wins per backed team (tier hue), open-circle line = cumulative %."""
    color = _tier_color(tier, c)
    fig = go.Figure()
    fig.add_bar(
        x=df["Team"], y=df["Wins"],
        marker=dict(color=color, line=dict(width=0)),
        customdata=df[["Bets", "WinRate"]].to_numpy(),
        hovertemplate="<b>%{x}</b><br>Wins: %{y}<br>Bets: %{customdata[0]}"
                      "<br>Win rate: %{customdata[1]:.0f}%<extra></extra>",
        name="Wins",
    )
    fig.add_scatter(
        x=df["Team"], y=df["CumPct"], yaxis="y2",
        mode="lines+markers",
        line=dict(color=c["text"], width=2),
        marker=dict(size=8, color=c["surface"], line=dict(color=c["text"], width=2)),
        hovertemplate="Cumulative: %{y:.0f}%<extra></extra>",
        name="Cumulative %",
    )
    _base_layout(fig, c, height=360)
    fig.update_layout(
        bargap=0.35,
        showlegend=False,
        xaxis=dict(tickangle=-40, title=None),
        yaxis=dict(title="Wins", gridcolor=c["plot_grid"], rangemode="tozero"),
        yaxis2=dict(title="Cumulative %", overlaying="y", side="right",
                    range=[0, 105], showgrid=False, ticksuffix="%"),
    )
    return fig


def heatmap_figure(tidy: pd.DataFrame, c: dict, tiers: list) -> go.Figure:
    """Team (rows) × tier (cols) win-rate grid. Diverging red→neutral→green at 50%;
    cells show win% over sample size. Empty (team,tier) pairs render as gaps."""
    teams = list(dict.fromkeys(tidy["Team"]))   # preserve most-bet-first order
    wr = tidy.pivot(index="Team", columns="Tier", values="WinRate").reindex(index=teams, columns=tiers)
    n  = tidy.pivot(index="Team", columns="Tier", values="Bets").reindex(index=teams, columns=tiers)

    text = [[("" if pd.isna(wr.iat[r, col]) else f"{wr.iat[r, col]:.0f}%<br>{int(n.iat[r, col])}")
             for col in range(wr.shape[1])] for r in range(wr.shape[0])]

    fig = go.Figure(go.Heatmap(
        z=wr.values, x=[_short_tier(t) for t in tiers], y=teams,
        zmin=0, zmax=100,
        colorscale=[[0.0, c["red"]], [0.5, c["surface2"]], [1.0, c["green"]]],
        text=text, texttemplate="%{text}",
        textfont=dict(family="Space Mono", size=11, color=c["text"]),
        xgap=4, ygap=4,
        hovertemplate="<b>%{y}</b> · %{x}<br>Win rate: %{z:.0f}%<extra></extra>",
        colorbar=dict(title="Win&nbsp;%", ticksuffix="%", thickness=12, outlinewidth=0),
    ))
    _base_layout(fig, c, height=max(280, 46 * len(teams) + 90))
    fig.update_layout(
        xaxis=dict(side="top", title=None, fixedrange=True),
        yaxis=dict(autorange="reversed", title=None),
    )
    return fig


def leaderboard_figure(df: pd.DataFrame, c: dict) -> go.Figure:
    """Horizontal P&L bars per backed team — green profit, red loss, biggest on top."""
    df = df.sort_values("PnL")   # ascending → most profitable lands at the top
    colors = [c["green"] if v >= 0 else c["red"] for v in df["PnL"]]
    fig = go.Figure(go.Bar(
        x=df["PnL"], y=df["Team"], orientation="h",
        marker=dict(color=colors),
        customdata=df[["Bets", "ROI", "WinRate"]].to_numpy(),
        hovertemplate="<b>%{y}</b><br>P&L: $%{x:,.2f}<br>Bets: %{customdata[0]}"
                      "<br>ROI: %{customdata[1]:+.1f}%<br>Win rate: %{customdata[2]:.0f}%<extra></extra>",
        text=df["PnL"].map(lambda v: f"${v:+,.0f}"),
        textposition="outside",
        textfont=dict(family="Space Mono", size=11),
        cliponaxis=False,
    ))
    fig.add_vline(x=0, line_color=c["border2"], line_width=1)
    _base_layout(fig, c, height=max(240, 34 * len(df) + 80))
    fig.update_layout(
        xaxis=dict(title="Net P&L ($)", gridcolor=c["plot_grid"], zeroline=False),
        yaxis=dict(title=None),
    )
    return fig


def reliability_figure(df: pd.DataFrame, c: dict) -> go.Figure:
    """Actual win% bars per tier (tier hue) vs the dotted market break-even line.
    A bar above the line beat the market; ROI is printed on each bar."""
    colors = [_tier_color(t, c) for t in df["Tier"]]
    labels = [_short_tier(t) for t in df["Tier"]]
    fig = go.Figure()
    fig.add_bar(
        x=labels, y=df["WinRate"], marker=dict(color=colors),
        text=[f"{wr:.0f}%<br>ROI {roi:+.0f}%" if pd.notna(roi) else f"{wr:.0f}%"
              for wr, roi in zip(df["WinRate"], df["ROI"])],
        textposition="outside",
        textfont=dict(family="Space Mono", size=11, color=c["text"]),
        customdata=df[["Bets"]].to_numpy(),
        hovertemplate="<b>%{x}</b><br>Win rate: %{y:.0f}%<br>Bets: %{customdata[0]}<extra></extra>",
        name="Your win %",
        cliponaxis=False,
    )
    fig.add_scatter(
        x=labels, y=df["BreakEven"], mode="lines+markers",
        line=dict(color=c["text"], width=2, dash="dot"),
        marker=dict(size=8, color=c["surface"], line=dict(color=c["text"], width=2)),
        hovertemplate="Break-even: %{y:.0f}%<extra></extra>",
        name="Market break-even",
    )
    _base_layout(fig, c, height=340)
    fig.update_layout(
        yaxis=dict(title="Win %", gridcolor=c["plot_grid"], rangemode="tozero", ticksuffix="%"),
        xaxis=dict(title=None),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    return fig


# ── Section orchestrator (shared by both tracker pages) ──────────────────────────

def _eyebrow(c: dict, text: str) -> str:
    return f'<span class="eyebrow-pill">{text}</span>'


def render_signal_performance(completed: pd.DataFrame, *, key_prefix: str,
                              scope_label: str = "bets") -> None:
    """Render the full Signal Performance section for a user-scoped `completed`
    frame (outcome already filtered to settled bets). `key_prefix` namespaces all
    widget keys so Bet Tracker and Paper Bet Tracker never collide; `scope_label`
    tunes the copy ('bets' vs 'paper bets')."""
    c = palette()

    if completed is None or completed.empty:
        return

    rel = signal_reliability(completed)
    if rel.empty:
        return   # nothing carried a model signal yet — stay quiet

    st.divider()
    st.markdown(_eyebrow(c, "SIGNAL PERFORMANCE"), unsafe_allow_html=True)
    st.subheader("🎯 What's working — by signal & team")
    st.caption(
        f"Your settled {scope_label}, read through the model's own signals: where the "
        "edge actually shows up, which teams deliver it, and where to lean in."
    )

    # 1 ── Reliability: does a stronger signal really win more? (the thesis)
    st.markdown("**Signal reliability** — your win rate at each tier vs. the market break-even.")
    responsive_chart(reliability_figure(rel, c), key=f"{key_prefix}_reliability", expandable=False)
    st.caption(
        "Bars above the dotted line beat the price you paid. Ideally win% climbs left→right — "
        "the stronger the flagged edge, the more it should cash."
    )

    # 2 ── Pareto: wins by team, one chart per actionable signal (req 1.1–1.8)
    st.markdown("**Wins by team, per signal** — who delivers when a tier fires.")
    all_teams = sorted({str(t).strip() for t in completed["bet_on"].dropna()})
    picked = st.multiselect(
        "Filter teams (leave empty for all)",
        options=all_teams, default=[],
        key=f"{key_prefix}_pareto_teams",
        help="Narrow the Pareto charts to specific teams. Empty shows every team.",
    )
    team_filter = picked or None

    tabs = st.tabs([_short_tier(t) for t in SIGNAL_TIERS])
    for tab, tier in zip(tabs, SIGNAL_TIERS):
        with tab:
            par = pareto_by_signal(completed, tier, teams=team_filter)
            if par.empty:
                st.caption(f"No wins recorded with a {_short_tier(tier)} signal yet.")
                continue
            responsive_chart(pareto_figure(par, tier, c), key=f"{key_prefix}_pareto_{tier[:3]}")

    # 3 ── Heatmap: the cross-signal companion to the Pareto charts (all teams)
    mat = team_signal_matrix(completed)
    if not mat.empty:
        st.markdown("**Win-rate grid** — every backed team across all three signals at once.")
        responsive_chart(heatmap_figure(mat, c, list(SIGNAL_TIERS)), key=f"{key_prefix}_heatmap")
        st.caption("Green = you're winning that signal for that team · red = losing it · number = bets.")

    # 4 ── Leaderboard: where the money actually came from (all teams)
    lb = team_leaderboard(completed)
    if not lb.empty:
        st.markdown("**Team profit leaderboard** — lean into the green, fade the red.")
        responsive_chart(leaderboard_figure(lb, c), key=f"{key_prefix}_leaderboard", expandable=False)
        st.caption("Every backed team by net P&L — green made money, red lost it.")
