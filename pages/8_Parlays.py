"""
pages/8_Parlays.py — Build model-backed parlays and track their outcomes in one place.

Two tabs:
  🔮 Build  — smart recommendations + manual builder (formerly Parlay Builder)
  📊 Track  — pending parlays, full log, win rate / ROI / P&L (formerly Parlay Tracker)

Performance: today's odds, team stats, and model predictions are computed once and
cached (`_load_build_data`, ttl=300). Streamlit runs both tab bodies on every rerun,
so caching keeps Track-tab interactions (resolve, cashout) from re-running the model.
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from itertools import combinations as iter_combinations
from collections import defaultdict
from datetime import datetime, timezone, timedelta, date

import requests
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ingestion.odds_client import fetch_mlb_odds
from ingestion.stats_scraper import get_full_team_stats
from models.predictor import MLBPredictor, build_matchup_features, evaluate_value
from database import init_db, get_connection
from theme import init_theme, palette
from ui import responsive_chart
from auth import require_login, selected_user_id, current_user_id, user_clause, owner_clause

init_db()

st.set_page_config(page_title="Parlays", page_icon="🎰", layout="wide")
init_theme("#c2410c")   # burnt orange — the parlay identity
require_login()
_c = palette()

# Whose parlays the Track tab shows (admin → sidebar picker, default self; 1.5.1).
view_uid = selected_user_id()

BASE_MLB = "https://statsapi.mlb.com/api/v1"
HEADERS  = {"User-Agent": "Mozilla/5.0 (compatible; mlb-betting-app/1.0)"}


# ── Shared math + formatting ────────────────────────────────────────────────────

def american_to_decimal(odds: int) -> float:
    return (odds / 100 + 1.0) if odds > 0 else (100 / abs(odds) + 1.0)

def decimal_to_american(d: float) -> int:
    if d >= 2.0:
        return round((d - 1) * 100)
    return round(-100 / (d - 1))

def combine_legs(odds_list: list) -> tuple:
    """Returns (american_odds, decimal_odds) for the combined parlay."""
    d = 1.0
    for o in odds_list:
        d *= american_to_decimal(o)
    return decimal_to_american(d), d

def fmt_ml(o):  return f"+{o}" if o > 0 else str(o)
def fmt_pct(p): return f"{p*100:.1f}%"

def game_time_str(ct: str) -> str:
    try:
        dt = datetime.fromisoformat(ct.replace("Z", "+00:00")) + timedelta(hours=-4)
        return dt.strftime("%I:%M %p ET").lstrip("0")
    except Exception:
        return ""

def _is_upcoming_today(ct: str, grace: int = 5) -> bool:
    try:
        dt_utc  = datetime.fromisoformat(ct.replace("Z", "+00:00"))
        now_utc = datetime.now(timezone.utc)
        if dt_utc <= now_utc - timedelta(minutes=grace):
            return False
        local_tz    = datetime.now().astimezone().tzinfo
        dt_local    = dt_utc.astimezone(local_tz)
        now_local   = datetime.now(local_tz)
        today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        return today_start <= dt_local < today_start + timedelta(hours=26)
    except Exception:
        return True


# ── Cached build data (odds + stats + model predictions in one pass) ────────────

@st.cache_data(ttl=300, show_spinner=False)
def _load_build_data() -> dict:
    """Fetch today's odds, team stats, and model predictions once per 5 minutes.

    Returns a dict with a `status` key: 'no_odds', 'no_games', or 'ok'. On 'ok' it
    carries everything both builders need so reruns don't re-hit the API or model.
    """
    odds_list = fetch_mlb_odds()
    if not odds_list:
        return {"status": "no_odds"}

    odds_today = [g for g in odds_list if _is_upcoming_today(g["commence_time"])]
    if not odds_today:
        return {"status": "no_games"}

    stats_df = get_full_team_stats()

    books_by_game: dict = defaultdict(list)
    for g in odds_today:
        books_by_game[g["base_game_id"]].append(g)

    # Best line per game (prefer Caesars)
    game_best: dict = {}
    for g in odds_today:
        bid = g["base_game_id"]
        if bid not in game_best or g["bookmaker_key"] == "caesars":
            game_best[bid] = g
    all_games = list(game_best.values())

    all_books = sorted(set(g["bookmaker"] for g in odds_today))
    caesars_default = next((b for b in all_books if "caesars" in b.lower()), all_books[0])

    predictor = MLBPredictor()
    game_preds: dict = {}
    for g in all_games:
        features  = build_matchup_features(g["home_team"], g["away_team"], stats_df, is_home_game=True)
        home_prob = float(predictor.predict_proba(features))
        away_prob = 1 - home_prob
        home_eval = evaluate_value(home_prob, g["home_implied_prob"], g["home_ml"])
        away_eval = evaluate_value(away_prob, g["away_implied_prob"], g["away_ml"])
        game_preds[g["base_game_id"]] = {
            "home_prob": home_prob, "away_prob": away_prob,
            "home_edge": home_eval["edge"], "away_edge": away_eval["edge"],
        }

    return {
        "status": "ok",
        "books_by_game": dict(books_by_game),
        "game_best": game_best,
        "all_games": all_games,
        "all_books": all_books,
        "caesars_default": caesars_default,
        "game_preds": game_preds,
    }


def _log_parlay(book, budget, legs, p_american, net, notes) -> None:
    """Persist a parlay and its legs. `legs` items need team/odds/game_date/home/away."""
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO parlays (created_date, sportsbook, stake, legs_count, parlay_odds, potential_payout, notes, user_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (str(date.today()), book, budget, len(legs), p_american, net, notes, current_user_id()),
    )
    parlay_id = cur.lastrowid
    for leg in legs:
        conn.execute(
            "INSERT INTO parlay_legs (parlay_id, game_date, home_team, away_team, bet_on, odds) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (parlay_id, leg["game_date"], leg["home_team"], leg["away_team"],
             leg["team"], int(leg["odds"])),
        )
    conn.commit()
    conn.close()


# ── Build tab ───────────────────────────────────────────────────────────────────

def _build_recommendations(games, book, n_legs, strategy, budget, books_by_game, game_preds):
    """Return (list of up to 3 parlay dicts, error_str_or_None)."""
    def get_book_odds(base_id):
        entries = books_by_game.get(base_id, [])
        hit = next((b for b in entries if b["bookmaker"] == book), None)
        return hit or (entries[0] if entries else None)

    candidates = []
    for g in games:
        entry = get_book_odds(g["base_game_id"])
        if not entry:
            continue
        preds = game_preds[g["base_game_id"]]

        sides = [
            {"team": g["home_team"], "odds": entry["home_ml"],
             "model_prob": preds["home_prob"], "edge": preds["home_edge"],
             "game_label": f"{g['away_team']} @ {g['home_team']}",
             "home_team": g["home_team"], "away_team": g["away_team"],
             "game_date": g["commence_time"][:10], "time_str": game_time_str(g["commence_time"])},
            {"team": g["away_team"], "odds": entry["away_ml"],
             "model_prob": preds["away_prob"], "edge": preds["away_edge"],
             "game_label": f"{g['away_team']} @ {g['home_team']}",
             "home_team": g["home_team"], "away_team": g["away_team"],
             "game_date": g["commence_time"][:10], "time_str": game_time_str(g["commence_time"])},
        ]

        if strategy == "Value Bets Only (4%+ Edge)":
            value_sides = [s for s in sides if s["edge"] >= 0.04]
            if value_sides:
                candidates.append(max(value_sides, key=lambda s: s["edge"]))
        elif strategy == "Highest Probability of Hitting":
            candidates.append(max(sides, key=lambda s: s["model_prob"]))
        else:  # Highest EV
            for s in sides:
                d = american_to_decimal(s["odds"])
                s["ev"] = s["model_prob"] * (d - 1) - (1 - s["model_prob"])
            candidates.append(max(sides, key=lambda s: s["ev"]))

    if len(candidates) < n_legs:
        return [], (
            f"Only {len(candidates)} eligible leg(s) found for **{strategy}** — "
            f"need {n_legs}. Try fewer legs or a different strategy."
        )

    scored = []
    for combo in iter_combinations(range(len(candidates)), int(n_legs)):
        legs = [candidates[i] for i in combo]
        combined_prob = 1.0
        for leg in legs:
            combined_prob *= leg["model_prob"]
        p_american, p_decimal = combine_legs([l["odds"] for l in legs])
        net_payout = round(budget * (p_decimal - 1), 2)
        ev = combined_prob * net_payout - (1 - combined_prob) * budget
        score = combined_prob if strategy == "Highest Probability of Hitting" else ev
        scored.append({
            "legs": legs, "combined_prob": combined_prob,
            "parlay_odds": p_american, "parlay_decimal": p_decimal,
            "net_payout": net_payout, "ev": ev, "score": score,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:3], None


def render_build() -> None:
    with st.spinner("Loading today's games..."):
        data = _load_build_data()

    if data["status"] == "no_odds":
        st.warning("No games on the board yet. Odds usually post by late morning ET — check back then.")
        return
    if data["status"] == "no_games":
        st.warning("No upcoming games left for today. Come back tomorrow to build a new slip.")
        return

    books_by_game   = data["books_by_game"]
    game_best       = data["game_best"]
    all_games       = data["all_games"]
    all_books       = data["all_books"]
    caesars_default = data["caesars_default"]
    game_preds      = data["game_preds"]

    if len(all_games) < 2:
        st.info(
            "Only one upcoming game is on the board — a parlay needs at least two legs. "
            "Check back when more games post, or place a single bet on **Today's Games**."
        )
        return

    book_index = all_books.index(caesars_default) if caesars_default in all_books else 0

    # ── Smart Recommendations ───────────────────────────────────────────────────
    st.subheader("🤖 Smart Parlay Recommendations")
    st.caption("Set your parameters — the model scores every possible combination and returns the top 3.")

    sc1, sc2, sc3, sc4 = st.columns(4)
    smart_book   = sc1.selectbox("Sportsbook", all_books, index=book_index, key="smart_book")
    smart_budget = sc2.number_input("Budget ($)", min_value=1.0, value=20.0, step=5.0, key="smart_budget")

    max_legs     = len(all_games)                 # guaranteed ≥ 2 here
    smart_n_legs = sc3.number_input(
        "Number of legs", min_value=2, max_value=max_legs,
        value=min(3, max_legs), step=1, key="smart_n_legs",
    )
    smart_strategy = sc4.selectbox("Strategy", [
        "Highest Probability of Hitting",
        "Highest Expected Value",
        "Value Bets Only (4%+ Edge)",
    ], key="smart_strategy")

    if st.button("🔍 Build Recommendations", type="primary", key="build_btn"):
        with st.spinner("Evaluating all combinations..."):
            recs, err = _build_recommendations(
                all_games, smart_book, smart_n_legs, smart_strategy, smart_budget,
                books_by_game, game_preds,
            )
        if err:
            st.warning(err)
            st.session_state.pop("smart_recs", None)
        else:
            st.session_state["smart_recs"] = recs
            st.session_state["smart_recs_params"] = {
                "book": smart_book, "budget": smart_budget,
                "n_legs": smart_n_legs, "strategy": smart_strategy,
            }

    recs       = st.session_state.get("smart_recs", [])
    rec_params = st.session_state.get("smart_recs_params", {})

    if recs:
        st.caption(
            f"Showing top {len(recs)} · {rec_params.get('strategy')} · "
            f"{rec_params.get('n_legs')}-leg · ${rec_params.get('budget'):.2f} budget · "
            f"{rec_params.get('book')}"
        )
        rank_labels = ["🥇 Best", "🥈 2nd", "🥉 3rd"]
        for i, rec in enumerate(recs):
            ev_color = _c["green"] if rec["ev"] >= 0 else _c["red"]
            rank = rank_labels[i] if i < 3 else f"#{i+1}"

            legs_html = ""
            for leg in rec["legs"]:
                edge_color = _c["green"] if leg["edge"] >= 0.04 else _c["muted"]
                legs_html += (
                    f'<div style="font-size:0.85rem; color:{_c["text2"]}; padding:0.25rem 0;">'
                    f'⚾ <strong>{leg["game_label"]}</strong> · {leg["time_str"]}'
                    f' &nbsp;·&nbsp; Bet: <strong>{leg["team"]}</strong>'
                    f' &nbsp;·&nbsp; Odds: <code>{fmt_ml(leg["odds"])}</code>'
                    f' &nbsp;·&nbsp; Model: {fmt_pct(leg["model_prob"])}'
                    f' &nbsp;·&nbsp; Edge: <span style="color:{edge_color};">{leg["edge"]*100:+.1f}%</span>'
                    f'</div>'
                )

            st.markdown(f"""
<div style="background:{_c['surface']}; border:1px solid {_c['border']}; border-radius:16px;
            padding:1.1rem 1.3rem; margin-bottom:1rem; box-shadow:{_c['shadow']};">
  <div style="font-size:1rem; font-weight:800; color:{_c['text']}; margin-bottom:0.6rem;">
    {rank} &nbsp;·&nbsp;
    <span style="color:{_c['accent']};">Parlay Odds: {fmt_ml(rec['parlay_odds'])}</span>
    &nbsp;·&nbsp; Net if Win: <span style="color:{_c['green']};">${rec['net_payout']:.2f}</span>
    &nbsp;·&nbsp; Win Prob: {fmt_pct(rec['combined_prob'])}
    &nbsp;·&nbsp; EV: <span style="color:{ev_color};">{'+' if rec['ev'] >= 0 else ''}${rec['ev']:.2f}</span>
  </div>
  <div style="display:flex; flex-direction:column; gap:0.3rem;">{legs_html}</div>
</div>""", unsafe_allow_html=True)

            if st.button(f"📋 Log Parlay {rank}", key=f"log_rec_{i}"):
                p_american, p_decimal = combine_legs([l["odds"] for l in rec["legs"]])
                net = round(rec_params["budget"] * (p_decimal - 1), 2)
                _log_parlay(
                    rec_params["book"], rec_params["budget"], rec["legs"], p_american, net,
                    f"{rec_params['strategy']} · Prob: {rec['combined_prob']*100:.1f}%",
                )
                st.success("✅ Parlay logged! Open the **Track** tab to follow results.")

    st.divider()

    # ── Manual Parlay Builder ───────────────────────────────────────────────────
    st.subheader("✍️ Manual Parlay Builder")
    st.caption("Pick any games and sides. Combined odds and payout update as you select.")

    mc1, mc2 = st.columns(2)
    manual_book   = mc1.selectbox("Sportsbook", all_books, index=book_index, key="manual_book")
    manual_budget = mc2.number_input("Budget ($)", min_value=1.0, value=20.0, step=5.0, key="manual_budget")

    st.markdown("**Select legs:**")
    manual_legs = []

    for g in all_games:
        bid   = g["base_game_id"]
        preds = game_preds[bid]

        entry = next((b for b in books_by_game.get(bid, []) if b["bookmaker"] == manual_book), None)
        if not entry:
            entry = game_best.get(bid, g)

        col_chk, col_info = st.columns([0.5, 5])
        checked = col_chk.checkbox("", key=f"ml_{bid}", label_visibility="collapsed")
        col_info.markdown(
            f"**{g['away_team']} @ {g['home_team']}** · {game_time_str(g['commence_time'])} · "
            f"Home: `{fmt_ml(entry['home_ml'])}` ({fmt_pct(preds['home_prob'])}) · "
            f"Away: `{fmt_ml(entry['away_ml'])}` ({fmt_pct(preds['away_prob'])})"
        )

        if checked:
            rec_idx = 0 if preds.get("home_edge", 0) >= preds.get("away_edge", 0) else 1
            side = st.radio(
                f"Bet on · {g['away_team']} @ {g['home_team']}",
                options=[g["home_team"], g["away_team"]],
                index=rec_idx, key=f"mside_{bid}", horizontal=True,
                captions=[
                    f"Home · {fmt_ml(entry['home_ml'])} · Model {fmt_pct(preds['home_prob'])}",
                    f"Away · {fmt_ml(entry['away_ml'])} · Model {fmt_pct(preds['away_prob'])}",
                ],
            )
            is_home = (side == g["home_team"])
            manual_legs.append({
                "game_label": f"{g['away_team']} @ {g['home_team']}",
                "home_team":  g["home_team"],
                "away_team":  g["away_team"],
                "team":       side,
                "odds":       entry["home_ml"] if is_home else entry["away_ml"],
                "model_prob": preds["home_prob"] if is_home else preds["away_prob"],
                "game_date":  g["commence_time"][:10],
            })

    if len(manual_legs) >= 2:
        p_am, p_dec = combine_legs([l["odds"] for l in manual_legs])
        net         = round(manual_budget * (p_dec - 1), 2)
        comb_prob   = 1.0
        for l in manual_legs:
            comb_prob *= l["model_prob"]
        ev = comb_prob * net - (1 - comb_prob) * manual_budget
        ev_color = _c["green"] if ev >= 0 else _c["red"]

        st.markdown(f"""
<div style="background:{_c['surface']}; border:1px solid {_c['border']}; border-radius:16px;
            padding:0.9rem 1.2rem; margin:0.8rem 0; box-shadow:{_c['shadow']};">
  <div style="display:flex; gap:2rem; flex-wrap:wrap; font-size:0.9rem;">
    <div><span style="color:{_c['muted']}; font-size:0.72rem; text-transform:uppercase;">Legs</span>
         <div style="font-weight:700; color:{_c['text']};">{len(manual_legs)}</div></div>
    <div><span style="color:{_c['muted']}; font-size:0.72rem; text-transform:uppercase;">Parlay Odds</span>
         <div style="font-weight:700; color:{_c['accent']};">{fmt_ml(p_am)}</div></div>
    <div><span style="color:{_c['muted']}; font-size:0.72rem; text-transform:uppercase;">Net if Win</span>
         <div style="font-weight:700; color:{_c['green']};">+${net:.2f}</div></div>
    <div><span style="color:{_c['muted']}; font-size:0.72rem; text-transform:uppercase;">Win Probability</span>
         <div style="font-weight:700; color:{_c['text']};">{fmt_pct(comb_prob)}</div></div>
    <div><span style="color:{_c['muted']}; font-size:0.72rem; text-transform:uppercase;">Expected Value</span>
         <div style="font-weight:700; color:{ev_color};">{'+' if ev >= 0 else ''}${ev:.2f}</div></div>
  </div>
</div>""", unsafe_allow_html=True)

        if st.button("📋 Log Manual Parlay", type="primary", key="log_manual"):
            _log_parlay(manual_book, manual_budget, manual_legs, p_am, net, "Manual parlay")
            st.success("✅ Parlay logged! Open the **Track** tab to follow results.")
    elif len(manual_legs) == 1:
        st.info("☝️ Pick one more game — a parlay needs at least two legs.")
    else:
        st.info("☝️ Check at least two games above to build a manual parlay.")

    st.caption("⚠️ Parlay odds assume no boosts and are calculated from individual leg odds. Not financial advice.")


# ── Track tab ─────────────────────────────────────────────────────────────────

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
            home_won    = h_score > a_score
            bet_on_home = _team_match(h["team"]["name"], bet_on) or _team_match(home_team, bet_on)
            return "Win" if (home_won == bet_on_home) else "Loss"
    return "Pending"


def _fetch_and_resolve(parlay_id: int) -> str:
    """Check each pending leg; mark the parlay Lost on first failure, Won when all hit."""
    conn = get_connection()
    _oc, _op = owner_clause()   # a regular user can only resolve their own parlays
    row  = conn.execute(f"SELECT * FROM parlays WHERE id = ?{_oc}", (parlay_id, *_op)).fetchone()
    legs = conn.execute("SELECT * FROM parlay_legs WHERE parlay_id = ?", (parlay_id,)).fetchall()
    if not row or not legs:
        conn.close()
        return "Parlay not found."

    parlay      = dict(row)
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
        conn.execute("UPDATE parlays SET outcome = 'Loss', profit_loss = ? WHERE id = ?",
                     (-parlay["stake"], parlay_id))
        msg = "❌ Parlay marked **Lost** — at least one leg failed."
    elif all_decided:
        conn.execute("UPDATE parlays SET outcome = 'Win', profit_loss = ? WHERE id = ?",
                     (parlay["potential_payout"], parlay_id))
        msg = "✅ Parlay marked **Won** — all legs hit!"
    else:
        msg = "⏳ Some games are not yet final — check back later."

    conn.commit()
    conn.close()
    return msg


def render_track(uid) -> None:
    conn     = get_connection()
    uclause, uparams = user_clause(uid)
    parlays  = pd.read_sql(
        f"SELECT * FROM parlays{uclause} ORDER BY created_date DESC, id DESC", conn, params=uparams
    )
    legs_all = pd.read_sql("SELECT * FROM parlay_legs", conn)
    conn.close()

    if parlays.empty:
        st.info("No parlays logged yet. Open the **Build** tab to put your first slip together.")
        return

    parlays["stake"]            = pd.to_numeric(parlays["stake"],            errors="coerce")
    parlays["profit_loss"]      = pd.to_numeric(parlays["profit_loss"],      errors="coerce")
    parlays["potential_payout"] = pd.to_numeric(parlays["potential_payout"], errors="coerce")

    # ── Performance summary ─────────────────────────────────────────────────────
    resolved   = parlays[parlays["outcome"].isin(["Win", "Loss", "Cashout"])].copy()
    win_loss   = parlays[parlays["outcome"].isin(["Win", "Loss"])].copy()
    n_resolved = len(resolved)
    n_pending  = parlays["outcome"].isna().sum() + (parlays["outcome"] == "").sum()

    win_rate  = (win_loss["outcome"] == "Win").mean() * 100 if len(win_loss) > 0 else 0.0
    total_pnl = resolved["profit_loss"].sum() if n_resolved > 0 else 0.0
    total_stk = resolved["stake"].sum()       if n_resolved > 0 else 0.0
    roi       = (total_pnl / total_stk * 100) if total_stk > 0 else 0.0

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
</div>""", unsafe_allow_html=True)

    st.divider()

    # ── Pending parlays ─────────────────────────────────────────────────────────
    pending = parlays[parlays["outcome"].isna() | (parlays["outcome"] == "")]

    if not pending.empty:
        st.subheader("⏳ Pending Parlays")
        for _, row in pending.iterrows():
            pid  = int(row["id"])
            legs = legs_all[legs_all["parlay_id"] == pid]

            st.markdown(f"""
<div style="background:{_c['surface']}; border:1px solid {_c['border']}; border-radius:16px;
            padding:0.9rem 1.2rem; margin-bottom:0.8rem; box-shadow:{_c['shadow']};">
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
                    f"<span style='color:{res_color};'>{res_label}</span></div>",
                    unsafe_allow_html=True,
                )

            st.markdown("</div>", unsafe_allow_html=True)

            btn_c1, btn_c2, _ = st.columns([2, 2, 6])

            if btn_c1.button("🔍 Fetch Results", key=f"fetch_{pid}"):
                with st.spinner("Checking game scores..."):
                    msg = _fetch_and_resolve(pid)
                st.info(msg)
                st.rerun()

            if btn_c2.button("💵 Cashout", key=f"co_btn_{pid}"):
                st.session_state[f"show_cashout_{pid}"] = True

            if st.session_state.get(f"show_cashout_{pid}"):
                with st.form(key=f"cashout_form_{pid}"):
                    payout = st.number_input(
                        "Cashout payout received ($)", min_value=0.0,
                        value=float(row["stake"]), step=0.01, key=f"co_val_{pid}",
                        help="Enter the total amount returned to you by the sportsbook.",
                    )
                    if st.form_submit_button("Confirm Cashout"):
                        profit = round(payout - row["stake"], 2)
                        conn2  = get_connection()
                        _oc, _op = owner_clause()
                        conn2.execute(f"UPDATE parlays SET outcome = 'Cashout', profit_loss = ? WHERE id = ?{_oc}",
                                      (profit, pid, *_op))
                        conn2.execute("UPDATE parlay_legs SET result = 'Cashout' WHERE parlay_id = ? AND result IS NULL",
                                      (pid,))
                        conn2.commit()
                        conn2.close()
                        st.session_state.pop(f"show_cashout_{pid}", None)
                        st.success(f"Cashout recorded — P&L: ${profit:+.2f}")
                        st.rerun()

        st.divider()

    # ── Full parlay log ─────────────────────────────────────────────────────────
    st.subheader("Full Parlay Log")
    for _, row in parlays.iterrows():
        pid  = int(row["id"])
        legs = legs_all[legs_all["parlay_id"] == pid]

        outcome = row["outcome"] or "Pending"
        pnl_str = f" · P&L: ${row['profit_loss']:+.2f}" if pd.notna(row["profit_loss"]) else ""
        header = (
            f"**#{pid}** · {row['created_date']} · {row['sportsbook'] or '—'} · "
            f"{int(row['legs_count'])} legs · Odds: {fmt_ml(int(row['parlay_odds']))} · "
            f"Stake: ${row['stake']:.2f} · Potential: +${row['potential_payout']:.2f}{pnl_str} · "
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

    # ── Running P&L chart ───────────────────────────────────────────────────────
    if n_resolved >= 3:
        st.divider()
        st.subheader("Running P&L")
        sorted_res = resolved.sort_values("created_date")
        sorted_res["running_pnl"] = sorted_res["profit_loss"].cumsum()
        colors = [_c["plot_green"] if v >= 0 else _c["plot_red"] for v in sorted_res["running_pnl"]]

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
            template=_c["plotly_template"],
            paper_bgcolor=_c["plot_paper"], plot_bgcolor=_c["plot_bg"],
            font=dict(family="Manrope", color=_c["plot_font"]),
            xaxis_title="Parlay #", yaxis_title="P&L ($)", height=350,
        )
        responsive_chart(fig, key="plt_pnl")

    st.caption("⚠️ Parlays are high-variance. Win rate and ROI need a large sample to mean much. Not financial advice.")


# ── Page layout ───────────────────────────────────────────────────────────────

_today_str = datetime.now().strftime("%A, %b %-d") if os.name != "nt" else datetime.now().strftime("%A, %b %#d")

st.markdown(f"""
<div class="hero">
  <div class="hero-main">
    <span class="eyebrow-pill">🎰 Parlays</span>
    <h1>Stack the edges. Track every slip.</h1>
    <p class="hero-sub">Build model-backed parlays from today's value bets, then follow win rate, ROI, and P&amp;L as they settle — all in one place.</p>
    <div class="hero-meta">{_today_str}  ·  Caesars moneylines  ·  Model-scored combinations</div>
  </div>
</div>""", unsafe_allow_html=True)

tab_build, tab_track = st.tabs(["🔮  Build", "📊  Track"])

with tab_build:
    render_build()

with tab_track:
    render_track(view_uid)
