"""
pages/9_Parlay_Tracker.py — Track parlay outcomes, ROI, and P&L.
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import requests
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import date
from database import init_db, get_connection
from theme import init_theme, palette

init_db()

st.set_page_config(page_title="Parlay Tracker", page_icon="🎰", layout="wide")
init_theme("#e11d48")   # rose — parlay tracker

st.title("🎰 Parlay Tracker")
st.caption("Track parlay outcomes — win rate, ROI, and P&L across all logged parlays.")

_c = palette()

BASE_MLB = "https://statsapi.mlb.com/api/v1"
HEADERS  = {"User-Agent": "Mozilla/5.0 (compatible; mlb-betting-app/1.0)"}


# ── Helpers ────────────────────────────────────────────────────────────────────

def fmt_ml(o): return f"+{o}" if o > 0 else str(o)

def _team_match(api_name: str, stored_name: str) -> bool:
    a, s = api_name.lower().strip(), stored_name.lower().strip()
    return a == s or s in a or a in s or a.split()[-1] == s.split()[-1]

def _get_game_result(game_date: str, home_team: str, away_team: str, bet_on: str) -> str:
    """Query MLB Stats API for a game result. Returns 'Win', 'Loss', or 'Pending'."""
    try:
        resp = requests.get(
            f"{BASE_MLB}/schedule",
            params={"sportId": 1, "date": game_date, "gameType": "R"},
            headers=HEADERS, timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return "Pending"

    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            if game.get("status", {}).get("detailedState") != "Final":
                continue
            h = game["teams"]["home"]
            a = game["teams"]["away"]
            if not (_team_match(h["team"]["name"], home_team) and
                    _team_match(a["team"]["name"], away_team)):
                continue
            h_score = h.get("score") or 0
            a_score = a.get("score") or 0
            if h_score == a_score:
                return "Pending"
            home_won     = h_score > a_score
            bet_on_home  = _team_match(h["team"]["name"], bet_on) or _team_match(home_team, bet_on)
            return "Win" if (home_won == bet_on_home) else "Loss"
    return "Pending"


def _fetch_and_resolve(parlay_id: int) -> str:
    """
    Check each pending leg via MLB API. Mark parlay Lost on first failure,
    Won when all legs are confirmed wins.
    """
    conn  = get_connection()
    row   = conn.execute("SELECT * FROM parlays WHERE id = ?", (parlay_id,)).fetchone()
    legs  = conn.execute("SELECT * FROM parlay_legs WHERE parlay_id = ?", (parlay_id,)).fetchall()
    if not row or not legs:
        conn.close()
        return "Parlay not found."

    parlay = dict(row)
    any_loss    = False
    all_decided = True

    for leg in legs:
        leg = dict(leg)
        if leg["result"] in ("Win", "Loss"):
            if leg["result"] == "Loss":
                any_loss = True
            continue

        result = _get_game_result(leg["game_date"], leg["home_team"], leg["away_team"], leg["bet_on"])
        if result in ("Win", "Loss"):
            conn.execute("UPDATE parlay_legs SET result = ? WHERE id = ?", (result, leg["id"]))
            if result == "Loss":
                any_loss = True
        else:
            all_decided = False

    conn.commit()

    if any_loss:
        conn.execute(
            "UPDATE parlays SET outcome = 'Loss', profit_loss = ? WHERE id = ?",
            (-parlay["stake"], parlay_id),
        )
        msg = "❌ Parlay marked **Lost** — at least one leg failed."
    elif all_decided:
        profit = parlay["potential_payout"]
        conn.execute(
            "UPDATE parlays SET outcome = 'Win', profit_loss = ? WHERE id = ?",
            (profit, parlay_id),
        )
        msg = "✅ Parlay marked **Won** — all legs hit!"
    else:
        msg = "⏳ Some games are not yet final — check back later."

    conn.commit()
    conn.close()
    return msg


# ── Load data ──────────────────────────────────────────────────────────────────

conn      = get_connection()
parlays   = pd.read_sql("SELECT * FROM parlays ORDER BY created_date DESC, id DESC", conn)
legs_all  = pd.read_sql("SELECT * FROM parlay_legs", conn)
conn.close()

if parlays.empty:
    st.info("No parlays logged yet. Head to the **Parlay Builder** to get started.")
    st.stop()

parlays["stake"]           = pd.to_numeric(parlays["stake"],           errors="coerce")
parlays["profit_loss"]     = pd.to_numeric(parlays["profit_loss"],     errors="coerce")
parlays["potential_payout"]= pd.to_numeric(parlays["potential_payout"],errors="coerce")


# ── Performance summary ────────────────────────────────────────────────────────

resolved   = parlays[parlays["outcome"].isin(["Win", "Loss", "Cashout"])].copy()
win_loss   = parlays[parlays["outcome"].isin(["Win", "Loss"])].copy()
n_resolved = len(resolved)
n_pending  = parlays["outcome"].isna().sum() + (parlays["outcome"] == "").sum()

win_rate   = (win_loss["outcome"] == "Win").mean() * 100 if len(win_loss) > 0 else 0.0
total_pnl  = resolved["profit_loss"].sum() if n_resolved > 0 else 0.0
total_stk  = resolved["stake"].sum()       if n_resolved > 0 else 0.0
roi        = (total_pnl / total_stk * 100) if total_stk > 0 else 0.0

pnl_color = _c["green"] if total_pnl >= 0 else _c["red"]
roi_color = _c["green"] if roi       >= 0 else _c["red"]

st.subheader("Performance Summary")
st.markdown(f"""
<div style="display:flex; gap:12px; margin-bottom:1rem; flex-wrap:wrap;">
  <div class="stat-box" style="flex:1; min-width:120px;">
    <div style="font-size:0.72rem; color:{_c['muted']}; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px;">Resolved Parlays</div>
    <div style="font-size:2rem; font-weight:800; font-family:'Manrope',sans-serif; color:{_c['text']};">{n_resolved}</div>
    <div style="font-size:0.72rem; color:{_c['muted']};">{n_pending} pending</div>
  </div>
  <div class="stat-box" style="flex:1; min-width:120px;">
    <div style="font-size:0.72rem; color:{_c['muted']}; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px;">Win Rate</div>
    <div style="font-size:2rem; font-weight:800; font-family:'Manrope',sans-serif; color:{_c['text']};">{win_rate:.1f}%</div>
    <div style="font-size:0.72rem; color:{_c['muted']};">Win/Loss only — excludes cashouts</div>
  </div>
  <div class="stat-box" style="flex:1; min-width:120px;">
    <div style="font-size:0.72rem; color:{_c['muted']}; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px;">P&amp;L</div>
    <div style="font-size:2rem; font-weight:800; font-family:'Manrope',sans-serif; color:{pnl_color};">${total_pnl:+.2f}</div>
    <div style="font-size:0.72rem; color:{roi_color};">ROI: {roi:+.1f}%</div>
  </div>
  <div class="stat-box" style="flex:1; min-width:120px;">
    <div style="font-size:0.72rem; color:{_c['muted']}; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px;">Total Wagered</div>
    <div style="font-size:2rem; font-weight:800; font-family:'Manrope',sans-serif; color:{_c['text']};">${total_stk:.2f}</div>
  </div>
</div>
""", unsafe_allow_html=True)

st.divider()


# ── Pending parlays ────────────────────────────────────────────────────────────

pending = parlays[parlays["outcome"].isna() | (parlays["outcome"] == "")]

if not pending.empty:
    st.subheader("⏳ Pending Parlays")

    for _, row in pending.iterrows():
        pid   = int(row["id"])
        legs  = legs_all[legs_all["parlay_id"] == pid]

        st.markdown(f"""
<div style="background:{_c['surface2']}; border:1px solid {_c['border']}; border-radius:10px;
            padding:0.9rem 1.2rem; margin-bottom:0.8rem;">
  <div style="font-weight:700; color:{_c['text']}; margin-bottom:0.4rem;">
    Parlay #{pid} &nbsp;·&nbsp; {row['created_date']} &nbsp;·&nbsp;
    {row['sportsbook'] or '—'} &nbsp;·&nbsp;
    {int(row['legs_count'])} legs &nbsp;·&nbsp;
    Odds: {fmt_ml(int(row['parlay_odds']))} &nbsp;·&nbsp;
    Stake: ${row['stake']:.2f} &nbsp;·&nbsp;
    Potential win: +${row['potential_payout']:.2f}
  </div>
""", unsafe_allow_html=True)

        for _, leg in legs.iterrows():
            res_color = (_c["green"] if leg["result"] == "Win"
                         else _c["red"] if leg["result"] == "Loss"
                         else _c["muted"])
            res_label = leg["result"] or "Pending"
            st.markdown(
                f"<div style='font-size:0.83rem; color:{_c['text2']}; padding:0.15rem 0;'>"
                f"  ⚾ {leg['away_team']} @ {leg['home_team']} · "
                f"Bet: <strong>{leg['bet_on']}</strong> @ <code>{fmt_ml(int(leg['odds']))}</code> · "
                f"<span style='color:{res_color};'>{res_label}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

        st.markdown("</div>", unsafe_allow_html=True)

        btn_c1, btn_c2, btn_c3, btn_c4 = st.columns([2, 2, 2, 4])

        if btn_c1.button("🔍 Fetch Results", key=f"fetch_{pid}"):
            with st.spinner("Checking game scores..."):
                msg = _fetch_and_resolve(pid)
            st.info(msg)
            st.rerun()

        cashout_clicked = btn_c2.button("💵 Cashout", key=f"co_btn_{pid}")
        if cashout_clicked:
            st.session_state[f"show_cashout_{pid}"] = True

        if st.session_state.get(f"show_cashout_{pid}"):
            with st.form(key=f"cashout_form_{pid}"):
                payout = st.number_input(
                    "Cashout payout received ($)", min_value=0.0,
                    value=float(row["stake"]), step=0.01,
                    key=f"co_val_{pid}",
                    help="Enter the total amount returned to you by the sportsbook."
                )
                if st.form_submit_button("Confirm Cashout"):
                    profit = round(payout - row["stake"], 2)
                    conn2  = get_connection()
                    conn2.execute(
                        "UPDATE parlays SET outcome = 'Cashout', profit_loss = ? WHERE id = ?",
                        (profit, pid),
                    )
                    conn2.execute(
                        "UPDATE parlay_legs SET result = 'Cashout' WHERE parlay_id = ? AND result IS NULL",
                        (pid,),
                    )
                    conn2.commit()
                    conn2.close()
                    st.session_state.pop(f"show_cashout_{pid}", None)
                    st.success(f"Cashout recorded — P&L: ${profit:+.2f}")
                    st.rerun()

    st.divider()


# ── Full parlay log ────────────────────────────────────────────────────────────

st.subheader("Full Parlay Log")

for _, row in parlays.iterrows():
    pid  = int(row["id"])
    legs = legs_all[legs_all["parlay_id"] == pid]

    outcome = row["outcome"] or "Pending"
    if outcome == "Win":
        outcome_color = _c["green"]
    elif outcome == "Loss":
        outcome_color = _c["red"]
    elif outcome == "Cashout":
        outcome_color = _c["accent"]
    else:
        outcome_color = _c["muted"]

    pnl_str = ""
    if pd.notna(row["profit_loss"]):
        pnl_str = f" · P&L: ${row['profit_loss']:+.2f}"

    header = (
        f"**#{pid}** · {row['created_date']} · {row['sportsbook'] or '—'} · "
        f"{int(row['legs_count'])} legs · Odds: {fmt_ml(int(row['parlay_odds']))} · "
        f"Stake: ${row['stake']:.2f} · "
        f"Potential: +${row['potential_payout']:.2f}{pnl_str} · "
        f"**{outcome}**"
    )

    with st.expander(header, expanded=False):
        for _, leg in legs.iterrows():
            res = leg["result"] or "Pending"
            res_icon = "✅" if res == "Win" else "❌" if res == "Loss" else "⏳"
            st.markdown(
                f"{res_icon} **{leg['away_team']} @ {leg['home_team']}** ({leg['game_date']}) · "
                f"Bet: **{leg['bet_on']}** @ `{fmt_ml(int(leg['odds']))}` · {res}"
            )

st.divider()


# ── Running P&L chart ──────────────────────────────────────────────────────────

if n_resolved >= 3:
    st.subheader("Running P&L")
    sorted_res = resolved.sort_values("created_date")
    sorted_res["running_pnl"] = sorted_res["profit_loss"].cumsum()

    colors = [_c["plot_green"] if v >= 0 else _c["plot_red"] for v in sorted_res["running_pnl"]]
    _tmpl  = _c["plotly_template"]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=list(range(1, len(sorted_res) + 1)),
        y=sorted_res["running_pnl"],
        mode="lines+markers",
        line=dict(color=_c["plot_green"], width=2),
        marker=dict(color=colors, size=8),
        name="Cumulative P&L ($)",
    ))
    fig.add_hline(y=0, line_dash="dash", line_color="gray")
    fig.update_layout(
        template=_tmpl,
        paper_bgcolor=_c["plot_paper"],
        plot_bgcolor=_c["plot_bg"],
        font=dict(family="Manrope", color=_c["plot_font"]),
        xaxis_title="Parlay #",
        yaxis_title="P&L ($)",
        height=350,
    )
    st.plotly_chart(fig, use_container_width=True)

st.caption("⚠️ Parlays are high-variance bets. Win rate and ROI require a large sample to be meaningful. Not financial advice.")
