"""
pages/8_Parlay_Builder.py — Smart and manual parlay builder.
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import streamlit as st
from itertools import combinations as iter_combinations
from collections import defaultdict
from datetime import datetime, timezone, timedelta, date

from ingestion.odds_client import fetch_mlb_odds
from ingestion.stats_scraper import get_full_team_stats
from models.predictor import MLBPredictor, build_matchup_features, evaluate_value
from database import init_db, get_connection
from theme import init_theme, palette

init_db()

st.set_page_config(page_title="Parlay Builder", page_icon="🎰", layout="wide")
init_theme("#c2410c")   # burnt orange — parlay builder

st.title("🎰 Parlay Builder")
st.caption("Build model-backed parlays or construct your own — all logged to the Parlay Tracker.")

_c = palette()

# ── Parlay math ────────────────────────────────────────────────────────────────

def american_to_decimal(odds: int) -> float:
    return (odds / 100 + 1.0) if odds > 0 else (100 / abs(odds) + 1.0)

def decimal_to_american(d: float) -> int:
    if d >= 2.0:
        return round((d - 1) * 100)
    return round(-100 / (d - 1))

def combine_legs(odds_list: list) -> tuple:
    """Returns (american_odds, decimal_odds)"""
    d = 1.0
    for o in odds_list:
        d *= american_to_decimal(o)
    return decimal_to_american(d), d

def fmt_ml(o): return f"+{o}" if o > 0 else str(o)
def fmt_pct(p): return f"{p*100:.1f}%"

# ── Load data ──────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_data():
    return fetch_mlb_odds(), get_full_team_stats()

with st.spinner("Loading today's games..."):
    odds_list, stats_df = load_data()

def _is_upcoming_today(ct: str, grace: int = 5) -> bool:
    try:
        dt_utc  = datetime.fromisoformat(ct.replace("Z", "+00:00"))
        now_utc = datetime.now(timezone.utc)
        if dt_utc <= now_utc - timedelta(minutes=grace):
            return False
        local_tz   = datetime.now().astimezone().tzinfo
        dt_local   = dt_utc.astimezone(local_tz)
        now_local  = datetime.now(local_tz)
        today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        return today_start <= dt_local < today_start + timedelta(hours=26)
    except Exception:
        return True

if not odds_list:
    st.warning("No games available. Odds may not be posted yet.")
    st.stop()

odds_today = [g for g in odds_list if _is_upcoming_today(g["commence_time"])]
if not odds_today:
    st.warning("No upcoming games for today.")
    st.stop()

# Deduplicate — one entry per (base_game_id, bookmaker)
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

# Available sportsbooks
all_books = sorted(set(g["bookmaker"] for g in odds_today))
caesars_default = next((b for b in all_books if "caesars" in b.lower()), all_books[0])

# Build model predictions once
predictor = MLBPredictor()
game_preds: dict = {}
for g in all_games:
    features  = build_matchup_features(g["home_team"], g["away_team"], stats_df, is_home_game=True)
    home_prob = predictor.predict_proba(features)
    away_prob = 1 - home_prob
    home_eval = evaluate_value(home_prob, g["home_implied_prob"], g["home_ml"])
    away_eval = evaluate_value(away_prob, g["away_implied_prob"], g["away_ml"])
    game_preds[g["base_game_id"]] = {
        "home_prob": home_prob, "away_prob": away_prob,
        "home_edge": home_eval["edge"], "away_edge": away_eval["edge"],
    }

def game_time_str(ct: str) -> str:
    try:
        dt = datetime.fromisoformat(ct.replace("Z", "+00:00")) + timedelta(hours=-4)
        return dt.strftime("%I:%M %p ET").lstrip("0")
    except Exception:
        return ""


# ── Smart Recommendations ──────────────────────────────────────────────────────

st.subheader("🤖 Smart Parlay Recommendations")
st.caption("Select your parameters — the model scores every possible combination and returns the top 3.")

sc1, sc2, sc3, sc4 = st.columns(4)
smart_book     = sc1.selectbox("Sportsbook", all_books,
                               index=all_books.index(caesars_default) if caesars_default in all_books else 0,
                               key="smart_book")
smart_budget   = sc2.number_input("Budget ($)", min_value=1.0, value=20.0, step=5.0, key="smart_budget")
smart_n_legs   = sc3.number_input("Number of legs", min_value=2, max_value=len(all_games),
                                   value=min(3, len(all_games)), step=1, key="smart_n_legs")
smart_strategy = sc4.selectbox("Strategy", [
    "Highest Probability of Hitting",
    "Highest Expected Value",
    "Value Bets Only (4%+ Edge)",
], key="smart_strategy")

def _build_recommendations(games, book, n_legs, strategy, budget):
    """Return (list of up to 3 parlay dicts, error_str_or_None)."""
    # Find the best odds for each game from the chosen book, or fall back to any book
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

        if strategy == "Highest Probability of Hitting":
            score = combined_prob
        elif strategy == "Highest Expected Value":
            score = ev
        else:
            score = ev

        scored.append({
            "legs": legs, "combined_prob": combined_prob,
            "parlay_odds": p_american, "parlay_decimal": p_decimal,
            "net_payout": net_payout, "ev": ev, "score": score,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:3], None


if st.button("🔍 Build Recommendations", type="primary", key="build_btn"):
    with st.spinner("Evaluating all combinations..."):
        recs, err = _build_recommendations(
            all_games, smart_book, smart_n_legs, smart_strategy, smart_budget
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

recs = st.session_state.get("smart_recs", [])
rec_params = st.session_state.get("smart_recs_params", {})

if recs:
    st.caption(
        f"Showing top {len(recs)} recommendations · "
        f"{rec_params.get('strategy')} · "
        f"{rec_params.get('n_legs')}-leg parlay · "
        f"${rec_params.get('budget'):.2f} budget · "
        f"{rec_params.get('book')}"
    )
    for i, rec in enumerate(recs):
        ev_color = _c["green"] if rec["ev"] >= 0 else _c["red"]
        rank_labels = ["🥇 Best", "🥈 2nd", "🥉 3rd"]
        rank = rank_labels[i] if i < 3 else f"#{i+1}"

        with st.container():
            st.markdown(f"""
<div style="background:{_c['surface2']}; border:1px solid {_c['border']}; border-radius:10px;
            padding:1rem 1.2rem; margin-bottom:1rem;">
  <div style="font-size:1rem; font-weight:800; color:{_c['text']}; margin-bottom:0.6rem;">
    {rank} &nbsp;·&nbsp;
    <span style="color:{_c['accent']};">Parlay Odds: {fmt_ml(rec['parlay_odds'])}</span>
    &nbsp;·&nbsp; Potential Net Win: <span style="color:{_c['green']};">${rec['net_payout']:.2f}</span>
    &nbsp;·&nbsp; Win Probability: {fmt_pct(rec['combined_prob'])}
    &nbsp;·&nbsp; EV: <span style="color:{ev_color};">{'+' if rec['ev'] >= 0 else ''}${rec['ev']:.2f}</span>
  </div>
  <div style="display:flex; flex-direction:column; gap:0.3rem;">
""", unsafe_allow_html=True)

            for leg in rec["legs"]:
                edge_color = _c["green"] if leg["edge"] >= 0.04 else _c["muted"]
                st.markdown(f"""
    <div style="font-size:0.85rem; color:{_c['text2']}; padding:0.25rem 0;">
      ⚾ <strong>{leg['game_label']}</strong> · {leg['time_str']}
      &nbsp;·&nbsp; Bet: <strong>{leg['team']}</strong>
      &nbsp;·&nbsp; Odds: <code>{fmt_ml(leg['odds'])}</code>
      &nbsp;·&nbsp; Model: {fmt_pct(leg['model_prob'])}
      &nbsp;·&nbsp; Edge: <span style="color:{edge_color};">{leg['edge']*100:+.1f}%</span>
    </div>
""", unsafe_allow_html=True)

            st.markdown("</div></div>", unsafe_allow_html=True)

            if st.button(f"📋 Log Parlay {rank}", key=f"log_rec_{i}"):
                p_american, p_decimal = combine_legs([l["odds"] for l in rec["legs"]])
                net = round(rec_params["budget"] * (p_decimal - 1), 2)
                conn = get_connection()
                cur = conn.execute(
                    "INSERT INTO parlays (created_date, sportsbook, stake, legs_count, parlay_odds, potential_payout, notes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (str(date.today()), rec_params["book"], rec_params["budget"],
                     len(rec["legs"]), p_american, net,
                     f"{rec_params['strategy']} · Prob: {rec['combined_prob']*100:.1f}%")
                )
                parlay_id = cur.lastrowid
                for leg in rec["legs"]:
                    conn.execute(
                        "INSERT INTO parlay_legs (parlay_id, game_date, home_team, away_team, bet_on, odds) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (parlay_id, leg["game_date"], leg["home_team"], leg["away_team"],
                         leg["team"], int(leg["odds"]))
                    )
                conn.commit()
                conn.close()
                st.success(f"✅ Parlay logged! Head to **Parlay Tracker** to track results.")


st.divider()


# ── Manual Parlay Builder ──────────────────────────────────────────────────────

st.subheader("✍️ Manual Parlay Builder")
st.caption("Pick any games and sides. Combined odds and payout update as you select.")

mc1, mc2 = st.columns(2)
manual_book   = mc1.selectbox("Sportsbook", all_books,
                               index=all_books.index(caesars_default) if caesars_default in all_books else 0,
                               key="manual_book")
manual_budget = mc2.number_input("Budget ($)", min_value=1.0, value=20.0, step=5.0, key="manual_budget")

st.markdown("**Select legs:**")
manual_legs = []

for g in all_games:
    bid   = g["base_game_id"]
    preds = game_preds[bid]

    # Find odds for chosen book, fall back to best available
    entry = next((b for b in books_by_game.get(bid, []) if b["bookmaker"] == manual_book), None)
    if not entry:
        entry = game_best.get(bid, g)

    col_chk, col_info = st.columns([0.5, 5])
    checked = col_chk.checkbox("", key=f"ml_{bid}", label_visibility="collapsed")

    home_edge = preds["home_edge"]
    away_edge = preds["away_edge"]
    label = (f"**{g['away_team']} @ {g['home_team']}** · {game_time_str(g['commence_time'])} · "
             f"Home: `{fmt_ml(entry['home_ml'])}` ({fmt_pct(preds['home_prob'])}) · "
             f"Away: `{fmt_ml(entry['away_ml'])}` ({fmt_pct(preds['away_prob'])})")
    col_info.markdown(label)

    if checked:
        rec_idx = 0 if preds.get("home_edge", 0) >= preds.get("away_edge", 0) else 1
        side = st.radio(
            f"Bet on · {g['away_team']} @ {g['home_team']}",
            options=[g["home_team"], g["away_team"]],
            index=rec_idx,
            key=f"mside_{bid}",
            horizontal=True,
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

if manual_legs:
    p_am, p_dec = combine_legs([l["odds"] for l in manual_legs])
    net         = round(manual_budget * (p_dec - 1), 2)
    comb_prob   = 1.0
    for l in manual_legs:
        comb_prob *= l["model_prob"]
    ev = comb_prob * net - (1 - comb_prob) * manual_budget

    ev_color = _c["green"] if ev >= 0 else _c["red"]

    st.markdown(f"""
<div style="background:{_c['surface2']}; border:1px solid {_c['border']}; border-radius:10px;
            padding:0.9rem 1.2rem; margin:0.8rem 0;">
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
</div>
""", unsafe_allow_html=True)

    if st.button("📋 Log Manual Parlay", type="primary", key="log_manual"):
        conn = get_connection()
        cur  = conn.execute(
            "INSERT INTO parlays (created_date, sportsbook, stake, legs_count, parlay_odds, potential_payout, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(date.today()), manual_book, manual_budget, len(manual_legs),
             p_am, net, "Manual parlay")
        )
        parlay_id = cur.lastrowid
        for leg in manual_legs:
            conn.execute(
                "INSERT INTO parlay_legs (parlay_id, game_date, home_team, away_team, bet_on, odds) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (parlay_id, leg["game_date"], leg["home_team"], leg["away_team"],
                 leg["team"], int(leg["odds"]))
            )
        conn.commit()
        conn.close()
        st.success("✅ Parlay logged! Head to **Parlay Tracker** to track results.")
else:
    st.info("☝️ Check at least one game above to build a manual parlay.")

st.divider()
st.caption("⚠️ Parlay odds are calculated from individual leg odds assuming no boosts. Not financial advice.")
