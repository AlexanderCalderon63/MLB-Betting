"""
pages/4_Bet_Sizing.py — Budget allocator and bet sizing tool.
Supports real bets (manual side selection) and paper bets (auto-allocated, feeds model training).
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import streamlit as st
from collections import defaultdict
from datetime import datetime, timezone, timedelta, date
from ingestion.odds_client import fetch_mlb_odds
from ingestion.stats_scraper import get_full_team_stats
from models.predictor import MLBPredictor, build_matchup_features, evaluate_value
from ingestion.pitcher_scraper import search_pitcher, get_probable_pitchers_today
from database import init_db, get_connection
from theme import init_theme, palette

init_db()

st.set_page_config(page_title="Bet Sizing", page_icon="💰", layout="wide")
init_theme()

st.markdown("""
<div class="page-header">
    <h2>💰 Bet Sizing Calculator</h2>
    <p>Set your daily budget · Pick your games · Choose sides · Get a full risk breakdown</p>
</div>
""", unsafe_allow_html=True)


# ── Load data ──────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_data():
    odds = fetch_mlb_odds()
    stats = get_full_team_stats()
    return odds, stats

with st.spinner("Loading today's games..."):
    odds_list, stats_df = load_data()

if not odds_list:
    st.warning("No games available right now. Odds may not be posted yet.")
    st.stop()

# ── Filter: today only + not yet started ──────────────────────────────────────

def _is_todays_upcoming_game(commence_time: str, grace_minutes: int = 5) -> bool:
    try:
        dt_utc    = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
        now_utc   = datetime.now(timezone.utc)
        if dt_utc <= now_utc - timedelta(minutes=grace_minutes):
            return False
        local_tz  = datetime.now().astimezone().tzinfo
        dt_local  = dt_utc.astimezone(local_tz)
        now_local = datetime.now(local_tz)
        today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end   = today_start + timedelta(hours=26)
        return today_start <= dt_local < today_end
    except Exception:
        return True

before = len(set(g["base_game_id"] for g in odds_list))
odds_list = [g for g in odds_list if _is_todays_upcoming_game(g["commence_time"])]
after = len(set(g["base_game_id"] for g in odds_list))
if before - after > 0:
    st.caption(f"⏱️ {before - after} game(s) hidden — already started or scheduled for another day.")

if not odds_list:
    st.warning("No upcoming games for today. Check back tomorrow morning when lines are posted.")
    st.stop()

@st.cache_data(ttl=1800)
def _load_probable_pitchers(date_str: str) -> dict:
    return get_probable_pitchers_today(date_str)

@st.cache_data(ttl=3600)
def _load_pitcher_stats(name: str) -> dict:
    return search_pitcher(name)

# Load pitcher data if Today's Games hasn't already populated session state
if "pitcher_data" not in st.session_state:
    _today = datetime.now().strftime("%Y-%m-%d")
    _probable = _load_probable_pitchers(_today)
    _unique_ids = {}
    for g in odds_list:
        bid = g["base_game_id"]
        if bid not in _unique_ids or g["bookmaker_key"] == "caesars":
            _unique_ids[bid] = g
    _pitcher_data = {}
    for bid, g in _unique_ids.items():
        home_name = _probable.get(g["home_team"])
        away_name = _probable.get(g["away_team"])
        _pitcher_data[bid] = {
            "home": _load_pitcher_stats(home_name) if home_name else None,
            "away": _load_pitcher_stats(away_name) if away_name else None,
        }
    st.session_state["pitcher_data"] = _pitcher_data

# ── Build model predictions, deduplicate to one entry per game (best book) ────

predictor = MLBPredictor()

game_best = {}
for g in odds_list:
    base_id = g["base_game_id"]
    if base_id not in game_best or g["bookmaker_key"] == "caesars":
        pitcher_data = st.session_state.get("pitcher_data", {})
        pd_entry = pitcher_data.get(g["base_game_id"], {})
        home_sp  = pd_entry.get("home")
        away_sp  = pd_entry.get("away")

        features = build_matchup_features(
            g["home_team"], g["away_team"], stats_df,
            is_home_game=True,
            home_pitcher=home_sp,
            away_pitcher=away_sp,
        )
        home_prob = predictor.predict_proba(features)
        away_prob = 1 - home_prob
        home_eval = evaluate_value(home_prob, g["home_implied_prob"], g["home_ml"])
        away_eval = evaluate_value(away_prob, g["away_implied_prob"], g["away_ml"])

        feat_dict = features.iloc[0].to_dict() if features is not None else {}

        game_best[base_id] = {
            **g,
            "home_model_prob": home_prob,
            "away_model_prob": away_prob,
            "home_edge": home_eval["edge"],
            "away_edge": away_eval["edge"],
            "home_rec": home_eval["recommendation"],
            "away_rec": away_eval["recommendation"],
            "home_has_value": home_eval["has_value"],
            "away_has_value": away_eval["has_value"],
            "home_kelly": home_eval["kelly_fraction"],
            "away_kelly": away_eval["kelly_fraction"],
            "features": feat_dict,
        }

all_games = list(game_best.values())

books_by_game = defaultdict(list)
for g in odds_list:
    books_by_game[g["base_game_id"]].append(g)


# ── Helper functions ───────────────────────────────────────────────────────────

def fmt_ml(odds):
    return f"+{odds}" if odds > 0 else str(odds)

def fmt_pct(p):
    return f"{p*100:.1f}%"

def calc_payout(stake, odds):
    if odds > 0:
        return round(stake * odds / 100, 2)
    else:
        return round(stake * 100 / abs(odds), 2)

def rec_badge(rec, edge):
    if edge >= 0.08:
        return f'<span class="rec-badge rec-hot">{rec}</span>'
    elif edge >= 0.04:
        return f'<span class="rec-badge rec-value">{rec}</span>'
    elif edge >= 0.01:
        return f'<span class="rec-badge rec-edge">{rec}</span>'
    else:
        return f'<span class="rec-badge rec-none">{rec}</span>'


# ── Step 1: Budget ─────────────────────────────────────────────────────────────

st.subheader("Step 1 — Set Your Daily Budget")
col_b1, col_b2 = st.columns(2)
budget = col_b1.number_input(
    "Real betting budget ($)",
    min_value=1.0, max_value=100000.0,
    value=50.0, step=5.0,
    help="Total amount you're willing to risk today across real bets."
)
paper_balance = col_b2.number_input(
    "Paper betting balance ($)",
    min_value=1.0, max_value=100000.0,
    value=100.0, step=5.0,
    help="Simulated balance for paper bets. No real money — stakes are auto-sized by edge and feed model training."
)

st.divider()


# ── Step 2: Pick Games ─────────────────────────────────────────────────────────

st.subheader("Step 2 — Select Games")

_hc1, _hc2, _hc3 = st.columns([0.5, 0.5, 5])
_hc1.caption("💰 Real")
_hc2.caption("📋 Paper")
_hc3.caption("Game · Recommendation")

_all_gids = [g["base_game_id"] for g in all_games]
_all_real_on   = all(st.session_state.get(f"sel_{gid}",   False) for gid in _all_gids)
_all_paper_on  = all(st.session_state.get(f"paper_{gid}", False) for gid in _all_gids)
_sa1, _sa2, _ = st.columns([0.5, 0.5, 5])
if _sa1.button("None ✗" if _all_real_on else "All ✓", key="sa_real", help="Toggle all real bets", use_container_width=True):
    for _gid in _all_gids:
        st.session_state[f"sel_{_gid}"] = not _all_real_on
if _sa2.button("None ✗" if _all_paper_on else "All ✓", key="sa_paper", help="Toggle all paper bets", use_container_width=True):
    for _gid in _all_gids:
        st.session_state[f"paper_{_gid}"] = not _all_paper_on

selected_game_ids = []
paper_game_ids    = []

for g in all_games:
    if g["home_edge"] >= g["away_edge"] and (g["home_has_value"] or g["away_has_value"]):
        rec_side = "home" if g["home_edge"] >= g["away_edge"] else "away"
    elif g["home_has_value"]:
        rec_side = "home"
    elif g["away_has_value"]:
        rec_side = "away"
    else:
        rec_side = "home" if g["home_edge"] >= g["away_edge"] else "away"

    try:
        dt = datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00"))
        et_offset = timedelta(hours=-4)
        dt_et = dt + et_offset
        today_et = (datetime.now(timezone.utc) + et_offset).date()
        date_tag = "Today" if dt_et.date() == today_et else dt_et.strftime("%a %b %-d")
        label = f"{g['away_team']} @ {g['home_team']} · {date_tag} {dt_et.strftime('%I:%M %p ET').lstrip('0')}"
    except Exception:
        label = f"{g['away_team']} @ {g['home_team']}"

    rec_team = g["home_team"] if rec_side == "home" else g["away_team"]
    rec_edge = g["home_edge"] if rec_side == "home" else g["away_edge"]
    rec_text = g["home_rec"]  if rec_side == "home" else g["away_rec"]

    col_check, col_paper_chk, col_info = st.columns([0.5, 0.5, 5])
    checked       = col_check.checkbox("",     key=f"sel_{g['base_game_id']}",   label_visibility="collapsed")
    paper_checked = col_paper_chk.checkbox("", key=f"paper_{g['base_game_id']}", label_visibility="collapsed")
    if checked:
        selected_game_ids.append(g["base_game_id"])
    if paper_checked:
        paper_game_ids.append(g["base_game_id"])

    with col_info:
        badge = rec_badge(rec_text, rec_edge)
        st.markdown(
            f"**{label}** &nbsp;&nbsp; "
            f"Recommended: **{rec_team}** &nbsp; {badge} &nbsp; "
            f"Edge: `{rec_edge*100:+.1f}%` &nbsp; "
            f"Book: `{g['bookmaker']}`",
            unsafe_allow_html=True
        )

if not selected_game_ids and not paper_game_ids:
    st.info("☝️ Check at least one game under Real or Paper to continue.")
    st.stop()

st.divider()


# ── Steps 3–5: Real Bet Configuration & Breakdown ─────────────────────────────

if selected_game_ids:

    st.subheader("Step 3 — Configure Each Bet")
    st.caption("Choose your side and which sportsbook's line to use. The recommended side is pre-selected.")

    bet_configs = {}

    for base_id in selected_game_ids:
        g = game_best[base_id]
        available_books = books_by_game[base_id]
        book_names = [b["bookmaker"] for b in available_books]
        book_keys  = [b["bookmaker_key"] for b in available_books]

        default_book_idx = next((i for i, k in enumerate(book_keys) if k == "caesars"), 0)

        st.markdown(f"#### ⚾ {g['away_team']} @ {g['home_team']}")

        c1, c2, c3 = st.columns([2, 2, 2])

        rec_default = 0 if g["home_edge"] >= g["away_edge"] else 1

        side_choice = c1.radio(
            "Pick your side",
            options=[g["home_team"], g["away_team"]],
            index=rec_default,
            key=f"side_{base_id}",
            captions=[
                f"Home · Edge: {g['home_edge']*100:+.1f}% {'✅ Recommended' if g['home_edge'] >= g['away_edge'] else ''}",
                f"Away · Edge: {g['away_edge']*100:+.1f}% {'✅ Recommended' if g['away_edge'] > g['home_edge'] else ''}",
            ]
        )

        book_choice = c2.selectbox(
            "Sportsbook",
            options=book_names,
            index=default_book_idx,
            key=f"book_{base_id}",
            help="Caesars selected by default when available."
        )

        selected_book = next((b for b in available_books if b["bookmaker"] == book_choice), available_books[0])

        if side_choice == g["home_team"]:
            chosen_odds  = selected_book["home_ml"]
            chosen_prob  = selected_book["home_implied_prob"]
            model_prob   = g["home_model_prob"]
            chosen_edge  = g["home_edge"]
            chosen_team  = g["home_team"]
            chosen_rec   = g["home_rec"]
        else:
            chosen_odds  = selected_book["away_ml"]
            chosen_prob  = selected_book["away_implied_prob"]
            model_prob   = g["away_model_prob"]
            chosen_edge  = g["away_edge"]
            chosen_team  = g["away_team"]
            chosen_rec   = g["away_rec"]

        with c3:
            st.markdown("**Selected Line Summary**")
            st.markdown(f"Team: **{chosen_team}**")
            st.markdown(f"Odds: `{fmt_ml(chosen_odds)}`")
            st.markdown(f"Market implied: `{fmt_pct(chosen_prob)}`")
            st.markdown(f"Model prob: `{fmt_pct(model_prob)}`")
            st.markdown(rec_badge(chosen_rec, chosen_edge), unsafe_allow_html=True)

        bet_configs[base_id] = {
            "game_label":  f"{g['away_team']} @ {g['home_team']}",
            "team":        chosen_team,
            "odds":        chosen_odds,
            "implied_prob": chosen_prob,
            "model_prob":  model_prob,
            "edge":        chosen_edge,
            "rec":         chosen_rec,
            "bookmaker":   book_choice,
        }

        st.markdown("---")


    # ── Step 4: Stake Per Bet ──────────────────────────────────────────────────

    st.subheader("Step 4 — Stake Per Bet")
    st.caption("Amounts are pre-filled using edge-proportional sizing. Type any amount to override — a warning appears if your total exceeds your budget.")

    n = len(bet_configs)
    game_ids = list(bet_configs.keys())

    # Edge-proportional suggested stakes — last item absorbs rounding so they
    # always sum exactly to budget (avoids "$0.01 unallocated" noise)
    raw_edges      = [max(bet_configs[gid]["edge"], 0.01) for gid in game_ids]
    total_edge     = sum(raw_edges)
    suggested_list = [round(budget * e / total_edge, 2) for e in raw_edges]
    suggested_list[-1] = round(budget - sum(suggested_list[:-1]), 2)
    suggested = {gid: suggested_list[i] for i, gid in enumerate(game_ids)}

    # Push suggested values directly into session state whenever the selection
    # or budget changes. This is the only reliable way to update number_input
    # displays — Streamlit ignores the `value=` kwarg once a key exists in
    # session_state, so we set the state ourselves before the widgets render.
    context_key = (tuple(sorted(game_ids)), budget)
    if st.session_state.get("_real_context") != context_key:
        st.session_state["_real_context"] = context_key
        for gid in game_ids:
            st.session_state[f"stake_{gid}"] = float(suggested[gid])

    stakes     = {}
    alloc_cols = st.columns(n if n <= 3 else 2)
    for i, gid in enumerate(game_ids):
        cfg   = bet_configs[gid]
        col   = alloc_cols[i % len(alloc_cols)]
        stake = col.number_input(
            f"{cfg['team']} · {cfg['game_label']}",
            min_value=0.0,
            step=1.0,
            format="%.2f",
            key=f"stake_{gid}",
        )
        stakes[gid] = stake

    total_staked = sum(stakes.values())
    over_budget  = total_staked > budget + 0.01

    if total_staked == 0:
        st.info("Enter a stake for at least one bet to see the breakdown.")
    elif over_budget:
        st.warning(
            f"⚠️ Total stake **${total_staked:.2f}** exceeds your budget of **${budget:.2f}** "
            f"by **${total_staked - budget:.2f}**. You can still log, but review your amounts."
        )
    elif abs(total_staked - budget) <= 0.01:
        st.success(f"✅ ${total_staked:.2f} allocated across {n} bet{'s' if n > 1 else ''}.")
    else:
        remaining = budget - total_staked
        st.info(f"💡 **${remaining:.2f}** of your budget is unallocated — you've manually set amounts below your full budget.")

    st.divider()


    # ── Step 5: Full Breakdown ─────────────────────────────────────────────────

    if total_staked > 0:
        st.subheader("Step 5 — Full Bet Breakdown")

        total_stake       = 0.0
        total_best_payout = 0.0
        rows = []

        for gid in game_ids:
            cfg    = bet_configs[gid]
            stake  = stakes[gid]
            profit = calc_payout(stake, cfg["odds"])
            pct_of_budget = (stake / budget * 100) if budget > 0 else 0

            total_stake       += stake
            total_best_payout += profit

            rows.append({
                "gid": gid, "cfg": cfg,
                "stake": stake, "net_win": profit, "net_loss": -stake,
                "pct_of_budget": pct_of_budget,
            })

        for r in rows:
            if r["stake"] == 0:
                continue
            cfg = r["cfg"]
            badge = rec_badge(cfg["rec"], cfg["edge"])
            st.markdown(f"""
            <div class="payout-row">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.5rem;">
                    <div>
                        <strong>{cfg['game_label']}</strong> &nbsp;&nbsp;
                        <span style="color:#a0aec0; font-size:0.85rem;">Bet: <strong>{cfg['team']}</strong> @ <code>{fmt_ml(cfg['odds'])}</code> via {cfg['bookmaker']}</span>
                        &nbsp;&nbsp; {badge}
                    </div>
                    <div style="font-family:'Space Mono',monospace; font-size:0.85rem; color:#a0aec0;">{r['pct_of_budget']:.0f}% of budget</div>
                </div>
                <div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:1rem;">
                    <div><div style="color:#718096; font-size:0.75rem; text-transform:uppercase;">Stake</div>
                         <div style="font-size:1.1rem; font-weight:700; color:#e2e8f0;">${r['stake']:.2f}</div></div>
                    <div><div style="color:#718096; font-size:0.75rem; text-transform:uppercase;">Net if Win</div>
                         <div style="font-size:1.1rem; font-weight:700; color:#22d47a;">+${r['net_win']:.2f}</div></div>
                    <div><div style="color:#718096; font-size:0.75rem; text-transform:uppercase;">Net if Loss</div>
                         <div style="font-size:1.1rem; font-weight:700; color:#f05252;">${r['net_loss']:.2f}</div></div>
                    <div><div style="color:#718096; font-size:0.75rem; text-transform:uppercase;">Model Win Prob</div>
                         <div style="font-size:1.1rem; font-weight:700; color:#e2e8f0;">{fmt_pct(cfg['model_prob'])}</div></div>
                </div>
            </div>
            """, unsafe_allow_html=True)

        active_rows    = [r for r in rows if r["stake"] > 0]
        expected_value = sum(
            r["stake"] * (
                bet_configs[r["gid"]]["model_prob"] * (r["net_win"] / r["stake"])
                - (1 - bet_configs[r["gid"]]["model_prob"])
            )
            for r in active_rows
        )
        worst_case = -total_stake
        best_case  = total_best_payout
        ev_color   = "#22d47a" if expected_value >= 0 else "#f05252"
        budget_remaining = budget - total_stake

        st.markdown(f"""
        <div class="total-bar">
            <div style="font-size:1rem; font-weight:800; color:#e2e8f0; margin-bottom:1rem;">📊 Day Summary — {len(active_rows)} bet{'s' if len(active_rows) != 1 else ''} · ${budget:.2f} budget</div>
            <div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:1.5rem;">
                <div><div style="color:#718096; font-size:0.75rem; text-transform:uppercase;">Total at Risk</div>
                     <div style="font-size:1.4rem; font-weight:800; color:#e2e8f0;">${total_stake:.2f}</div></div>
                <div><div style="color:#718096; font-size:0.75rem; text-transform:uppercase;">Best Case (all win)</div>
                     <div style="font-size:1.4rem; font-weight:800; color:#22d47a;">+${best_case:.2f}</div></div>
                <div><div style="color:#718096; font-size:0.75rem; text-transform:uppercase;">Worst Case (all lose)</div>
                     <div style="font-size:1.4rem; font-weight:800; color:#f05252;">${worst_case:.2f}</div></div>
                <div><div style="color:#718096; font-size:0.75rem; text-transform:uppercase;">Expected Value</div>
                     <div style="font-size:1.4rem; font-weight:800; color:{ev_color};">{'+' if expected_value >= 0 else ''}${expected_value:.2f}</div></div>
            </div>
            <div style="margin-top:1rem; padding-top:1rem; border-top:1px solid #2d3748; color:#718096; font-size:0.8rem;">
                Expected value uses model win probabilities — not a guarantee. &nbsp;
                Budget remaining: <strong style="color:{'#f05252' if budget_remaining < 0 else '#e2e8f0'};">${budget_remaining:.2f}</strong>
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.divider()
        st.subheader("Log These Bets")
        st.caption("Send all configured bets directly to the Bet Tracker page.")

        if st.button("📒 Log All Bets to Tracker", use_container_width=False, type="primary"):
            conn = get_connection()
            logged = 0
            for r in rows:
                if r["stake"] == 0:
                    continue
                cfg = r["cfg"]
                g   = game_best[r["gid"]]
                conn.execute("""
                    INSERT INTO bets (game_date, home_team, away_team, bet_on, odds, stake, model_prob, implied_prob, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(date.today()),
                    g["home_team"], g["away_team"], cfg["team"],
                    int(cfg["odds"]), r["stake"],
                    cfg["model_prob"], cfg["implied_prob"],
                    f"Via {cfg['bookmaker']} · Edge: {cfg['edge']*100:+.1f}%"
                ))
                logged += 1
            conn.commit()
            conn.close()
            st.success(f"✅ {logged} bet{'s' if logged > 1 else ''} logged to Bet Tracker!")

    st.divider()


# ── Paper Bet Breakdown ────────────────────────────────────────────────────────

if paper_game_ids:
    st.subheader("📋 Paper Bet Breakdown")
    st.caption(
        f"Auto-allocating ${paper_balance:.2f} across {len(paper_game_ids)} paper "
        f"bet{'s' if len(paper_game_ids) > 1 else ''} using edge-proportional sizing. "
        "Recommended side is auto-selected. Update outcomes in the Paper Bet Tracker to improve model training."
    )

    # Edge-proportional auto-allocation using recommended side
    raw_edges_p = []
    for gid in paper_game_ids:
        g = game_best[gid]
        rec_edge = g["home_edge"] if g["home_edge"] >= g["away_edge"] else g["away_edge"]
        raw_edges_p.append(max(rec_edge, 0.01))
    total_edge_p = sum(raw_edges_p)

    paper_rows = []
    for i, gid in enumerate(paper_game_ids):
        g        = game_best[gid]
        is_home  = g["home_edge"] >= g["away_edge"]
        rec_team = g["home_team"]        if is_home else g["away_team"]
        rec_odds = g["home_ml"]          if is_home else g["away_ml"]
        rec_prob = g["home_implied_prob"] if is_home else g["away_implied_prob"]
        model_p  = g["home_model_prob"]  if is_home else g["away_model_prob"]
        rec_edge = g["home_edge"]        if is_home else g["away_edge"]
        rec_text = g["home_rec"]         if is_home else g["away_rec"]

        stake    = round(paper_balance * raw_edges_p[i] / total_edge_p, 2)
        net_win  = round(calc_payout(stake, rec_odds), 2)

        paper_rows.append({
            "gid":        gid,
            "game_label": f"{g['away_team']} @ {g['home_team']}",
            "home_team":  g["home_team"],
            "away_team":  g["away_team"],
            "team":       rec_team,
            "odds":       rec_odds,
            "implied_prob": rec_prob,
            "model_prob": model_p,
            "edge":       rec_edge,
            "rec":        rec_text,
            "stake":      stake,
            "net_win":    net_win,
            "net_loss":   -stake,
            "features":   g.get("features", {}),
        })

    for r in paper_rows:
        badge = rec_badge(r["rec"], r["edge"])
        st.markdown(f"""
        <div class="payout-row">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.5rem;">
                <div>
                    <strong>{r['game_label']}</strong> &nbsp;&nbsp;
                    <span style="color:#a0aec0; font-size:0.85rem;">Auto-bet: <strong>{r['team']}</strong> @ <code>{fmt_ml(r['odds'])}</code></span>
                    &nbsp;&nbsp; {badge}
                </div>
                <div style="font-family:'Space Mono',monospace; font-size:0.85rem; color:#a0aec0;">{r['edge']*100:+.1f}% edge</div>
            </div>
            <div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:1rem;">
                <div><div style="color:#718096; font-size:0.75rem; text-transform:uppercase;">Auto Stake</div>
                     <div style="font-size:1.1rem; font-weight:700; color:#e2e8f0;">${r['stake']:.2f}</div></div>
                <div><div style="color:#718096; font-size:0.75rem; text-transform:uppercase;">Net if Win</div>
                     <div style="font-size:1.1rem; font-weight:700; color:#22d47a;">+${r['net_win']:.2f}</div></div>
                <div><div style="color:#718096; font-size:0.75rem; text-transform:uppercase;">Net if Loss</div>
                     <div style="font-size:1.1rem; font-weight:700; color:#f05252;">${r['net_loss']:.2f}</div></div>
                <div><div style="color:#718096; font-size:0.75rem; text-transform:uppercase;">Model Win Prob</div>
                     <div style="font-size:1.1rem; font-weight:700; color:#e2e8f0;">{fmt_pct(r['model_prob'])}</div></div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    st.divider()
    if st.button("📋 Log Paper Bets", use_container_width=False, type="primary"):
        conn = get_connection()
        logged = 0
        for r in paper_rows:
            feats = r["features"]
            conn.execute("""
                INSERT INTO paper_bets (
                    game_date, home_team, away_team, bet_on, odds, stake, model_prob, implied_prob, notes,
                    win_pct_diff, pythag_diff, run_diff_diff, rs_diff, ra_diff, home_advantage,
                    sp_era_diff, sp_whip_diff, sp_k9_diff, sp_bb9_diff
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(date.today()),
                r["home_team"], r["away_team"], r["team"],
                int(r["odds"]), r["stake"], r["model_prob"], r["implied_prob"],
                f"Edge: {r['edge']*100:+.1f}%",
                feats.get("win_pct_diff"), feats.get("pythag_diff"),
                feats.get("run_diff_diff"), feats.get("rs_diff"),
                feats.get("ra_diff"), feats.get("home_advantage"),
                feats.get("sp_era_diff"), feats.get("sp_whip_diff"),
                feats.get("sp_k9_diff"), feats.get("sp_bb9_diff"),
            ))
            logged += 1
        conn.commit()
        conn.close()
        st.success(f"✅ {logged} paper bet{'s' if logged > 1 else ''} logged! Head to Paper Bet Tracker to update outcomes.")

st.divider()
st.caption("⚠️ Model uses season-level stats only. Not financial advice. Gamble responsibly.")
