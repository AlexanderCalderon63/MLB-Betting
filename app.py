"""
app.py — Dashboard: daily summary, today's value bets, pending outcomes.
Run with: streamlit run app.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st
import pandas as pd
from datetime import date, datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from database import get_connection, init_db
from ingestion.odds_client import fetch_mlb_odds
from ingestion.stats_scraper import get_full_team_stats
from models.predictor import MLBPredictor, build_matchup_features, evaluate_value
from theme import init_theme, palette

init_db()

st.set_page_config(
    page_title="MLB Dashboard",
    page_icon="⚾",
    layout="wide",
    initial_sidebar_state="expanded",
)
init_theme()

_c = palette()

st.title("⚾ MLB Betting Dashboard")
st.caption(f"Today is {datetime.now().strftime('%A, %B %d, %Y').replace(' 0', ' ')}")

st.sidebar.markdown("### ⚾ MLB Value Finder")
st.sidebar.caption("Data: The Odds API · MLB Stats API")
st.sidebar.caption("Not financial advice. Bet responsibly.")

# ── Quick Stats (last 30 days) ─────────────────────────────────────────────────

cutoff_30 = (date.today() - timedelta(days=30)).isoformat()
conn = get_connection()

real_bets = pd.read_sql(
    "SELECT * FROM bets WHERE game_date >= ?", conn, params=(cutoff_30,)
)
paper_bets = pd.DataFrame()
try:
    paper_bets = pd.read_sql(
        "SELECT * FROM paper_bets WHERE game_date >= ?", conn, params=(cutoff_30,)
    )
except Exception:
    pass
conn.close()

pending_real  = real_bets[real_bets["outcome"].isna() | (real_bets["outcome"] == "")]
pending_paper = (
    paper_bets[paper_bets["outcome"].isna() | (paper_bets["outcome"] == "")]
    if not paper_bets.empty else pd.DataFrame()
)

real_res = real_bets[real_bets["outcome"].isin(["Win", "Loss", "Cashout"])].copy()
real_res["profit_loss"] = pd.to_numeric(real_res["profit_loss"], errors="coerce")
real_res["stake"]       = pd.to_numeric(real_res["stake"],       errors="coerce")

n_bets     = len(real_res)
win_rate   = (real_res["outcome"] == "Win").mean() * 100 if n_bets > 0 else 0
total_pnl  = real_res["profit_loss"].sum()             if n_bets > 0 else 0.0
total_stk  = real_res["stake"].sum()                   if n_bets > 0 else 0.0
roi        = (total_pnl / total_stk * 100)             if total_stk > 0 else 0.0
n_pending  = len(pending_real) + len(pending_paper)

pnl_color     = _c["green"] if total_pnl >= 0 else _c["red"]
roi_color     = _c["green"] if roi >= 0       else _c["red"]
pending_color = _c["red"]   if n_pending > 0  else _c["muted"]

st.subheader("Last 30 Days")
st.markdown(f"""
<div style="display:flex; gap:12px; margin-bottom:1rem; flex-wrap:wrap;">
  <div class="stat-box" style="flex:1; min-width:120px;">
    <div style="font-size:0.72rem; color:{_c['muted']}; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px;">Resolved Bets</div>
    <div style="font-size:2rem; font-weight:800; font-family:'Syne',sans-serif; color:{_c['text']};">{n_bets}</div>
    <div style="font-size:0.72rem; color:{_c['muted']};">real bets</div>
  </div>
  <div class="stat-box" style="flex:1; min-width:120px;">
    <div style="font-size:0.72rem; color:{_c['muted']}; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px;">Win Rate</div>
    <div style="font-size:2rem; font-weight:800; font-family:'Syne',sans-serif; color:{_c['text']};">{win_rate:.1f}%</div>
    <div style="font-size:0.72rem; color:{_c['muted']};">real bets only</div>
  </div>
  <div class="stat-box" style="flex:1; min-width:120px;">
    <div style="font-size:0.72rem; color:{_c['muted']}; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px;">P&L</div>
    <div style="font-size:2rem; font-weight:800; font-family:'Syne',sans-serif; color:{pnl_color};">${total_pnl:+.2f}</div>
    <div style="font-size:0.72rem; color:{roi_color};">ROI: {roi:+.1f}%</div>
  </div>
  <div class="stat-box" style="flex:1; min-width:120px;">
    <div style="font-size:0.72rem; color:{_c['muted']}; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px;">Pending Outcomes</div>
    <div style="font-size:2rem; font-weight:800; font-family:'Syne',sans-serif; color:{pending_color};">{n_pending}</div>
    <div style="font-size:0.72rem; color:{_c['muted']};">{len(pending_real)} real · {len(pending_paper)} paper</div>
  </div>
</div>
""", unsafe_allow_html=True)

if n_pending > 0:
    st.info(
        f"⚠️ **{n_pending}** bet(s) are missing outcomes — update them in "
        "**Bet Tracker** or **Paper Bet Tracker** to keep the model fed."
    )

st.divider()

# ── Today's Value Bets ─────────────────────────────────────────────────────────

st.subheader("Today's Value Bets")

@st.cache_data(ttl=300)
def _load_odds_and_stats():
    return fetch_mlb_odds(), get_full_team_stats()

def _is_upcoming_today(commence_time: str, grace: int = 5) -> bool:
    try:
        dt_utc    = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
        now_utc   = datetime.now(timezone.utc)
        if dt_utc <= now_utc - timedelta(minutes=grace):
            return False
        local_tz  = datetime.now().astimezone().tzinfo
        dt_local  = dt_utc.astimezone(local_tz)
        now_local = datetime.now(local_tz)
        today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        return today_start <= dt_local < today_start + timedelta(hours=26)
    except Exception:
        return True

try:
    with st.spinner("Loading today's odds..."):
        odds_list, stats_df = _load_odds_and_stats()
    odds_ok = True
except Exception:
    odds_ok = False

if not odds_ok:
    st.warning("Could not load today's odds. Check your API key or try refreshing.")
else:
    today_odds = [g for g in odds_list if _is_upcoming_today(g["commence_time"])]

    if not today_odds:
        st.info("No upcoming games today, or lines haven't been posted yet.")
    else:
        # Deduplicate — prefer Caesars
        game_best = {}
        for g in today_odds:
            bid = g["base_game_id"]
            if bid not in game_best or g["bookmaker_key"] == "caesars":
                game_best[bid] = g

        predictor = MLBPredictor()
        enriched  = []
        for bid, g in game_best.items():
            features  = build_matchup_features(g["home_team"], g["away_team"], stats_df, is_home_game=True)
            home_prob = predictor.predict_proba(features)
            away_prob = 1 - home_prob
            home_eval = evaluate_value(home_prob, g["home_implied_prob"], g["home_ml"])
            away_eval = evaluate_value(away_prob, g["away_implied_prob"], g["away_ml"])
            enriched.append({
                **g,
                "home_model_prob": home_prob,
                "away_model_prob": away_prob,
                "home_edge": home_eval["edge"],
                "away_edge": away_eval["edge"],
                "home_has_value": home_eval["has_value"],
                "away_has_value": away_eval["has_value"],
            })

        value_games = [g for g in enriched if g["home_has_value"] or g["away_has_value"]]
        value_games.sort(key=lambda g: max(g["home_edge"], g["away_edge"]), reverse=True)
        no_value    = [g for g in enriched if not g["home_has_value"] and not g["away_has_value"]]

        n_total = len(enriched)
        n_value = len(value_games)

        st.caption(f"{n_total} game(s) today · **{n_value}** with 4%+ edge · Head to **Today's Games** for full detail and pitcher data")

        if not value_games:
            st.info("No games cross the 4% edge threshold today.")
        else:
            for g in value_games:
                sides = []
                if g["home_has_value"]:
                    sides.append((g["home_team"], g["home_edge"], g["home_model_prob"], g["home_implied_prob"], g["home_ml"]))
                if g["away_has_value"]:
                    sides.append((g["away_team"], g["away_edge"], g["away_model_prob"], g["away_implied_prob"], g["away_ml"]))

                try:
                    dt_utc  = datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00"))
                    dt_et   = dt_utc + timedelta(hours=-4)
                    time_str = dt_et.strftime("%I:%M %p ET").lstrip("0")
                except Exception:
                    time_str = ""

                for (team, edge, model_prob, implied_prob, ml) in sides:
                    badge_color = _c["green"] if edge >= 0.08 else _c["accent"]
                    badge_label = "🔥 Strong Value" if edge >= 0.08 else "✅ Value Bet"
                    odds_str    = f"+{ml}" if ml > 0 else str(ml)

                    st.markdown(f"""
<div style="background:{_c['surface2']}; border:1px solid {_c['border']}; border-radius:10px;
            padding:0.8rem 1.1rem; margin-bottom:0.6rem;
            display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.75rem;">
  <div>
    <div style="font-size:0.75rem; color:{_c['muted']};">{g['away_team']} @ {g['home_team']} · {time_str}</div>
    <div style="font-size:1.05rem; font-weight:700; color:{_c['text']};">
      Bet: {team} &nbsp;
      <span style="font-size:0.82rem; color:{badge_color}; font-weight:600;">{badge_label}</span>
    </div>
  </div>
  <div style="display:flex; gap:1.5rem; text-align:right; flex-wrap:wrap;">
    <div>
      <div style="font-size:0.65rem; color:{_c['muted']}; text-transform:uppercase;">Edge</div>
      <div style="font-size:1.1rem; font-weight:700; color:{badge_color};">{edge*100:+.1f}%</div>
    </div>
    <div>
      <div style="font-size:0.65rem; color:{_c['muted']}; text-transform:uppercase;">Model</div>
      <div style="font-size:1.1rem; font-weight:700; color:{_c['text']};">{model_prob*100:.1f}%</div>
    </div>
    <div>
      <div style="font-size:0.65rem; color:{_c['muted']}; text-transform:uppercase;">Market</div>
      <div style="font-size:1.1rem; font-weight:700; color:{_c['muted']};">{implied_prob*100:.1f}%</div>
    </div>
    <div>
      <div style="font-size:0.65rem; color:{_c['muted']}; text-transform:uppercase;">Odds</div>
      <div style="font-size:1.1rem; font-weight:700; color:{_c['text']};">{odds_str}</div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

        if no_value:
            with st.expander(f"Other games today ({len(no_value)}) — no edge", expanded=False):
                for g in no_value:
                    best_edge = max(g["home_edge"], g["away_edge"])
                    try:
                        dt_et    = datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00")) + timedelta(hours=-4)
                        time_str = dt_et.strftime("%I:%M %p ET").lstrip("0")
                    except Exception:
                        time_str = ""
                    st.markdown(
                        f"<div style='font-size:0.85rem; color:{_c['muted']}; padding:0.2rem 0;'>"
                        f"**{g['away_team']} @ {g['home_team']}** · {time_str} · "
                        f"Best edge: {best_edge*100:+.1f}%</div>",
                        unsafe_allow_html=True,
                    )

st.divider()

# ── Pending Outcomes ───────────────────────────────────────────────────────────

if n_pending > 0:
    st.subheader("Pending Outcomes")

    if not pending_real.empty:
        st.markdown("**Real Bets**")
        disp = pending_real[["game_date", "away_team", "home_team", "bet_on", "odds", "stake"]].copy()
        disp.columns = ["Date", "Away", "Home", "Bet On", "Odds", "Stake ($)"]
        st.dataframe(disp, use_container_width=True, hide_index=True)

    if not pending_paper.empty:
        st.markdown("**Paper Bets**")
        disp_p = pending_paper[["game_date", "away_team", "home_team", "bet_on", "odds", "stake"]].copy()
        disp_p.columns = ["Date", "Away", "Home", "Bet On", "Odds", "Stake ($)"]
        st.dataframe(disp_p, use_container_width=True, hide_index=True)

st.caption("⚠️ Model uses season-level stats only — pitcher data on Today's Games page. Not financial advice. Gamble responsibly.")
