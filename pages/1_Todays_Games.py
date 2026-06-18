"""
pages/1_Todays_Games.py — Live odds + model predictions + integrated bet slip
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'), override=True)

import streamlit as st
from collections import defaultdict
from datetime import datetime, timezone, timedelta, date

from ingestion.odds_client import fetch_mlb_odds
from ingestion.stats_scraper import get_full_team_stats
from ingestion.pitcher_scraper import search_pitcher, get_team_pitchers, get_probable_pitchers_today, get_head_to_head
from models.predictor import MLBPredictor, build_matchup_features, evaluate_value
from database import init_db, get_connection
from theme import init_theme, palette
from ingestion.park_weather import park_factor_badge, weather_badges, get_weather

init_db()

st.set_page_config(page_title="Today's Games", page_icon="⚾", layout="wide")
init_theme("#0e7490")   # cyan — today's games
c = palette()   # active theme colors — reused by inline HTML + helper functions

st.title("⚾ Today's MLB Games")
st.caption("Live moneylines · Probable starters · Integrated bet slip")

# ── Controls ───────────────────────────────────────────────────────────────────
col_refresh, col_filter, col_tog1, col_tog2 = st.columns([1, 2, 1.5, 2])
with col_refresh:
    if st.button("🔄 Refresh", use_container_width=True):
        st.cache_data.clear()
        for key in [k for k in st.session_state if k.startswith(("llm_", "insight_"))]:
            del st.session_state[key]
        for key in ["pitcher_data", "pitchers_loaded"]:
            st.session_state.pop(key, None)
        st.rerun()
with col_filter:
    filter_book = st.selectbox(
        "Filter by sportsbook",
        ["All Books", "Caesars", "BetMGM"],
        label_visibility="collapsed",
    )
with col_tog1:
    show_avoid = st.toggle("Show no-value games", value=False)
with col_tog2:
    show_all_times = st.toggle("Show started / other-day", value=False)

st.divider()

# ── Load odds + team stats ─────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_data():
    return fetch_mlb_odds(), get_full_team_stats()

with st.spinner("Fetching odds and stats..."):
    odds_list, stats_df = load_data()

if not odds_list:
    st.warning("No games found. Odds may not be posted yet.")
    st.stop()

if filter_book != "All Books":
    filtered = [g for g in odds_list if g["bookmaker"] == filter_book]
    if not filtered:
        st.warning(f"No lines posted by {filter_book} yet. Try 'All Books'.")
        st.stop()
    odds_list = filtered

# ── Filter: today only + not yet started ──────────────────────────────────────
def _is_todays_upcoming_game(commence_time: str, grace_minutes: int = 5) -> bool:
    try:
        dt_utc = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
        now_utc = datetime.now(timezone.utc)
        if dt_utc <= now_utc - timedelta(minutes=grace_minutes):
            return False
        local_tz = datetime.now().astimezone().tzinfo
        dt_local  = dt_utc.astimezone(local_tz)
        now_local = datetime.now(local_tz)
        today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end   = today_start + timedelta(hours=26)
        return today_start <= dt_local < today_end
    except Exception:
        return True

before_filter = len(set(g["base_game_id"] for g in odds_list))
if not show_all_times:
    odds_list = [g for g in odds_list if _is_todays_upcoming_game(g["commence_time"])]
after_filter = len(set(g["base_game_id"] for g in odds_list))
skipped = before_filter - after_filter

if skipped > 0 and not show_all_times:
    st.caption(f"⏱️ {skipped} game(s) hidden — already started or scheduled for another day.")

if not odds_list:
    st.warning("No upcoming games for today. Check back tomorrow morning when lines are posted.")
    st.stop()

# Deduplicate to one entry per base game (prefer Caesars)
seen = {}
for g in odds_list:
    bid = g["base_game_id"]
    if bid not in seen or g["bookmaker_key"] == "caesars":
        seen[bid] = g
unique_games = list(seen.values())

# ── Auto-load probable pitchers ────────────────────────────────────────────────
@st.cache_data(ttl=1800)
def load_probable_pitchers(date_str: str) -> dict:
    return get_probable_pitchers_today(date_str)

@st.cache_data(ttl=3600)
def load_pitcher_stats(name: str) -> dict:
    return search_pitcher(name)

@st.cache_data(ttl=3600)
def load_roster(team_name: str) -> list[str]:
    pitchers = get_team_pitchers(team_name)
    return pitchers if pitchers else []

@st.cache_data(ttl=3600)
def load_h2h(home_team: str, away_team: str) -> list[dict]:
    return get_head_to_head(home_team, away_team, n=5)

@st.cache_data(ttl=3600)
def load_weather(home_team: str) -> dict | None:
    try:
        api_key = st.secrets["weather"]["openweathermap_api_key"]
    except Exception:
        return None
    return get_weather(home_team, api_key)

today_str = datetime.now().strftime("%Y-%m-%d")

if "pitchers_loaded" not in st.session_state:
    with st.spinner("Loading probable starters..."):
        probable = load_probable_pitchers(today_str)

    pitcher_data = {}
    for g in unique_games:
        bid = g["base_game_id"]
        home_name = probable.get(g["home_team"])
        away_name = probable.get(g["away_team"])
        home_sp = load_pitcher_stats(home_name) if home_name else None
        away_sp = load_pitcher_stats(away_name) if away_name else None
        pitcher_data[bid] = {
            "home": home_sp,
            "away": away_sp,
            "home_name": home_name or "",
            "away_name": away_name or "",
        }

    st.session_state["pitcher_data"] = pitcher_data
    st.session_state["pitchers_loaded"] = True

pitcher_data = st.session_state.get("pitcher_data", {})
probable = load_probable_pitchers(today_str)

# ── Override expander ──────────────────────────────────────────────────────────
with st.expander("🔄 Override Starting Pitchers", expanded=False):
    st.caption("Probable starters are loaded automatically. Use this only if a pitcher has been scratched or changed.")

    override_inputs = {}
    for g in unique_games:
        bid = g["base_game_id"]
        current = pitcher_data.get(bid, {})

        st.markdown(f"**{g['away_team']} @ {g['home_team']}**")
        c1, c2 = st.columns(2)

        home_roster = load_roster(g["home_team"])
        away_roster = load_roster(g["away_team"])

        home_probable = probable.get(g["home_team"], "")
        away_probable = probable.get(g["away_team"], "")

        with c1:
            st.caption(f"🏠 {g['home_team']}")
            home_options = ["— Select pitcher —"] + home_roster
            home_default = home_options.index(home_probable) if home_probable in home_options else 0
            home_sel = st.selectbox(
                f"home_{bid}", options=home_options, index=home_default,
                key=f"hp_{bid}", label_visibility="collapsed",
            )

        with c2:
            st.caption(f"✈️ {g['away_team']}")
            away_options = ["— Select pitcher —"] + away_roster
            away_default = away_options.index(away_probable) if away_probable in away_options else 0
            away_sel = st.selectbox(
                f"away_{bid}", options=away_options, index=away_default,
                key=f"ap_{bid}", label_visibility="collapsed",
            )

        override_inputs[bid] = {
            "home_name": "" if home_sel == "— Select pitcher —" else home_sel,
            "away_name": "" if away_sel == "— Select pitcher —" else away_sel,
        }

    if st.button("🔍 Update with Selected Pitchers", type="primary"):
        with st.spinner("Fetching updated pitcher stats..."):
            for bid, names in override_inputs.items():
                home_sp = load_pitcher_stats(names["home_name"]) if names["home_name"] else None
                away_sp = load_pitcher_stats(names["away_name"]) if names["away_name"] else None
                st.session_state["pitcher_data"][bid] = {
                    "home": home_sp,
                    "away": away_sp,
                    "home_name": names["home_name"],
                    "away_name": names["away_name"],
                }
        pitcher_data = st.session_state["pitcher_data"]
        st.success("✅ Pitcher stats updated.")
        st.rerun()

st.divider()

# ── Build predictions ──────────────────────────────────────────────────────────
predictor = MLBPredictor()
results = []
model_cache = {}

for game in odds_list:
    bid = game["base_game_id"]
    if bid not in model_cache:
        pd_entry = pitcher_data.get(bid, {})
        home_sp  = pd_entry.get("home")
        away_sp  = pd_entry.get("away")

        features = build_matchup_features(
            game["home_team"], game["away_team"], stats_df,
            is_home_game=True,
            home_pitcher=home_sp,
            away_pitcher=away_sp,
        )
        feat_dict = features.iloc[0].to_dict() if features is not None else {}
        home_prob = predictor.predict_proba(features)
        model_cache[bid] = {
            "home_prob":    home_prob,
            "has_pitchers": home_sp is not None and away_sp is not None,
            "home_sp":      home_sp,
            "away_sp":      away_sp,
            "features":     feat_dict,
        }

    cached    = model_cache[bid]
    home_prob = cached["home_prob"]
    away_prob = 1 - home_prob

    home_eval = evaluate_value(home_prob, game["home_implied_prob"], game["home_ml"])
    away_eval = evaluate_value(away_prob, game["away_implied_prob"], game["away_ml"])

    results.append({
        **game,
        "home_model_prob": home_prob,
        "away_model_prob": away_prob,
        "home_edge":       home_eval["edge"],
        "away_edge":       away_eval["edge"],
        "home_rec":        home_eval["recommendation"],
        "away_rec":        away_eval["recommendation"],
        "home_kelly":      home_eval["kelly_fraction"],
        "away_kelly":      away_eval["kelly_fraction"],
        "home_has_value":  home_eval["has_value"],
        "away_has_value":  away_eval["has_value"],
        "has_pitchers":    cached["has_pitchers"],
        "home_sp":         cached.get("home_sp"),
        "away_sp":         cached.get("away_sp"),
        "features":        cached.get("features", {}),
    })

# Group by base game
games_grouped = defaultdict(list)
for r in results:
    games_grouped[r["base_game_id"]].append(r)

def game_best_edge(entries):
    return max(max(e["home_edge"], e["away_edge"]) for e in entries)

sorted_games = sorted(games_grouped.items(), key=lambda x: game_best_edge(x[1]), reverse=True)

# Build lookup dicts (Caesars preferred per game)
game_best = {}
books_by_game = defaultdict(list)
for base_id, entries in sorted_games:
    books_by_game[base_id] = entries
    caesars = next((e for e in entries if e["bookmaker_key"] == "caesars"), entries[0])
    game_best[base_id] = caesars

# ── Summary metrics ────────────────────────────────────────────────────────────
total_games   = len(sorted_games)
value_games   = sum(1 for _, e in sorted_games if any(x["home_has_value"] or x["away_has_value"] for x in e))
pitcher_games = sum(1 for _, e in sorted_games if e[0]["has_pitchers"])

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Games Today",         total_games)
m2.metric("Value Opportunities", value_games)
m3.metric("Pitcher-Enhanced",    f"{pitcher_games}/{total_games}")
m4.metric("Caesars Lines",       sum(1 for r in results if r["bookmaker_key"] == "caesars"))
m5.metric("Total Lines",         len(results))

st.divider()

# ── Helpers ────────────────────────────────────────────────────────────────────
def fmt_ml(o):  return f"+{o}" if o > 0 else str(o)
def fmt_pct(p): return f"{p*100:.1f}%"

def calc_payout(stake, odds):
    if odds > 0:
        return round(stake * odds / 100, 2)
    return round(stake * 100 / abs(odds), 2)

def rec_badge(rec, edge):
    if edge >= 0.08:   return f'<span class="rec-badge rec-hot">{rec}</span>'
    elif edge >= 0.04: return f'<span class="rec-badge rec-value">{rec}</span>'
    elif edge >= 0.01: return f'<span class="rec-badge rec-edge">{rec}</span>'
    return f'<span class="rec-badge rec-none">{rec}</span>'

def game_signal_class(best_edge):
    if best_edge >= 0.08: return "signal-hot"
    if best_edge >= 0.04: return "signal-value"
    if best_edge >= 0.01: return "signal-edge"
    return "signal-none"

def fmt_last_ten(team: str) -> str:
    s = stats_df.copy()
    s["team"] = s["team"].str.strip().str.lower()
    key = team.strip().lower()
    row = s[s["team"] == key]
    if row.empty:
        row = s[s["team"].str.contains(key.split()[-1], na=False)]
    if row.empty or "last_ten_wins" not in s.columns:
        return "—"
    wins = int(row.iloc[0]["last_ten_wins"])
    return f"{wins}-{10 - wins}"

def edge_class(e):
    if e >= 0.08:  return "value-hot"
    if e >= 0.04:  return "value-yes"
    if e >= 0.01:  return "value-edge"
    if e <= -0.04: return "value-avoid"
    return "value-no"

def book_badge_html(key, name):
    valid = ["caesars", "williamhill_us", "betmgm"]
    cls = f"badge-{key}" if key in valid else "badge-default"
    return f'<span class="book-badge {cls}">{name}</span>'

def _read_anthropic_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if key:
        return key
    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '.env'))
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


def _llm_justify(fav: str, dog: str, value_tier: str, pitcher_ctx: str, form_ctx: str, stat_ctx: str) -> str | None:
    import hashlib
    api_key = _read_anthropic_key()
    if not api_key:
        return None

    ck = "llm_" + hashlib.md5("|".join([fav, dog, value_tier, pitcher_ctx, form_ctx, stat_ctx]).encode()).hexdigest()
    if ck in st.session_state:
        return st.session_state[ck]

    try:
        import anthropic
    except ImportError:
        return None

    ctx_lines = [l for l in [pitcher_ctx, form_ctx, stat_ctx] if l]
    ctx = "\n".join(f"- {l}" for l in ctx_lines) or "- No standout individual factor; purely model-driven"

    VALUE_DESC = {
        "strong_fav":   f"strong value on {fav} — well above the edge threshold",
        "moderate_fav": f"solid value on {fav}",
        "slight_fav":   f"slim edge on {fav}",
        "strong_dog":   f"strong value on underdog {dog} — model disagrees clearly with the market",
        "moderate_dog": f"value on underdog {dog}",
        "none":         f"lean toward {fav} but no real market inefficiency",
    }
    position = VALUE_DESC.get(value_tier, f"lean toward {fav}")

    prompt = (
        f"You are a sharp sports betting analyst. Write exactly 2 sentences commenting on an MLB game.\n\n"
        f"Model read: {position}\n"
        f"Context:\n{ctx}\n\n"
        f"Rules:\n"
        f"- No specific numbers, percentages, or stat values — qualitative language only\n"
        f"- Sentence 1: the model's position and whether there is real value at this price\n"
        f"- Sentence 2: the key factor that supports or complicates the lean\n"
        f"- Sound like a seasoned analyst, not a fill-in-the-blank template\n"
        f"- Exactly 2 sentences, nothing else"
    )

    result = None
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        if text and len(text) > 20:
            result = text
    except Exception as e:
        print(f"[COMMENTARY] LLM error: {e}")

    st.session_state[ck] = result
    return result


def justify_prediction(g: dict, home_l10: str, away_l10: str, stats_df=None) -> str:
    import hashlib
    gid  = g.get("base_game_id", g.get("game_id", "x"))
    seed = int(hashlib.md5(gid.encode()).hexdigest(), 16)

    def pick(opts):
        return opts[seed % len(opts)]

    home = g["home_team"].split()[-1]
    away = g["away_team"].split()[-1]
    home_e, away_e = g["home_edge"], g["away_edge"]
    home_p = g["home_model_prob"]
    fav_is_home = home_p >= (1 - home_p)
    fav      = home if fav_is_home else away
    dog      = away if fav_is_home else home
    fav_e    = home_e if fav_is_home else away_e
    dog_e    = away_e if fav_is_home else home_e
    fav_has_v = g["home_has_value"] if fav_is_home else g["away_has_value"]
    dog_has_v = g["away_has_value"] if fav_is_home else g["home_has_value"]
    fav_sp   = g.get("home_sp") if fav_is_home else g.get("away_sp")
    dog_sp   = g.get("away_sp") if fav_is_home else g.get("home_sp")

    fav_stats: dict = {}
    dog_stats: dict = {}
    if stats_df is not None:
        s = stats_df.copy()
        s["_key"] = s["team"].str.strip().str.lower()
        fav_key = (g["home_team"] if fav_is_home else g["away_team"]).strip().lower()
        dog_key = (g["away_team"] if fav_is_home else g["home_team"]).strip().lower()
        fav_row = s[s["_key"] == fav_key]
        dog_row = s[s["_key"] == dog_key]
        if not fav_row.empty: fav_stats = fav_row.iloc[0].to_dict()
        if not dog_row.empty: dog_stats = dog_row.iloc[0].to_dict()

    def sp_label(sp):
        if not sp or not sp.get("found"): return None
        try:
            era   = float(sp["era"])
            trend = sp.get("era_trend", 0)
            q = ("dominant" if era < 3.0 else "sharp" if era < 3.5
                 else "solid" if era < 4.0 else "average" if era < 4.5 else "struggling")
            if trend < -0.30:  q += ", on an improving run"
            elif trend > 0.30: q += ", trending in the wrong direction"
            return q
        except (ValueError, TypeError):
            return None

    def form_label(l10):
        try:
            w = int(l10.split("-")[0])
            if w >= 8: return "on fire"
            if w >= 7: return "playing well"
            if w <= 3: return "struggling badly"
            if w <= 4: return "cold lately"
        except Exception:
            pass
        return None

    def _run_diff_note(stats, team):
        try:
            rd = float(stats.get("run_diff", 0))
            if rd > 40:  return f"{team} has been one of the better run-scoring teams in baseball this season"
            if rd > 20:  return f"{team} has been consistently outscoring opponents"
            if rd < -40: return f"{team} has been badly outscored on the season"
            if rd < -20: return f"{team} has been outscored more often than not this year"
        except Exception:
            pass
        return None

    def _pythag_note(fav_stats, dog_stats, fav, dog):
        try:
            f_wp = float(fav_stats.get("win_pct", 0.5))
            f_py = float(fav_stats.get("pythag_pct", f_wp))
            d_wp = float(dog_stats.get("win_pct", 0.5))
            d_py = float(dog_stats.get("pythag_pct", d_wp))
            if d_py > d_wp + 0.05:
                return f"{dog} has been somewhat unlucky — they're playing better than their record shows"
            if f_py < f_wp - 0.05:
                return f"{fav}'s record may be slightly overstated — their underlying numbers are a bit softer"
        except Exception:
            pass
        return None

    fav_form = form_label(home_l10 if fav_is_home else away_l10)
    dog_form = form_label(away_l10 if fav_is_home else home_l10)
    fav_ql   = sp_label(fav_sp)
    dog_ql   = sp_label(dog_sp)
    has_pitchers = g.get("has_pitchers") and fav_sp and dog_sp

    if dog_has_v and dog_e >= 0.04:   _vt = "strong_dog"
    elif dog_has_v:                    _vt = "moderate_dog"
    elif fav_has_v and fav_e >= 0.08: _vt = "strong_fav"
    elif fav_has_v and fav_e >= 0.04: _vt = "moderate_fav"
    elif fav_has_v:                    _vt = "slight_fav"
    else:                              _vt = "none"

    _pctx = ""
    if has_pitchers and fav_ql and dog_ql:
        _pctx = f"{fav}'s starter has been {fav_ql}; {dog}'s starter has been {dog_ql}"

    _fctx = ""
    if fav_form and dog_form:
        _fctx = f"{fav} is {fav_form}, {dog} is {dog_form} over the last 10 games"
    elif fav_form:
        _fctx = f"{fav} is {fav_form} over the last 10 games"
    elif dog_form:
        _fctx = f"{dog} is {dog_form} over the last 10 games"

    _sctx = (_pythag_note(fav_stats, dog_stats, fav, dog)
             or _run_diff_note(fav_stats, fav)
             or _run_diff_note(dog_stats, dog)
             or "")

    _llm = _llm_justify(fav, dog, _vt, _pctx, _fctx, _sctx)
    if _llm:
        return _llm

    # Procedural fallback
    if dog_has_v and dog_e >= 0.04:
        s1 = pick([
            f"The market is overvaluing {fav} here — the model finds genuine value on the underdog {dog}.",
            f"There's a real edge going against the grain — {dog} is underpriced as the underdog.",
            f"The model disagrees with the market's conviction on {fav} and sees {dog} as the better play at this number.",
        ])
    elif dog_has_v:
        s1 = pick([
            f"Despite being the underdog, {dog} is slightly underpriced relative to what the model expects.",
            f"The model sees {dog}'s price as a small edge — not overwhelming, but worth noting.",
            f"{dog} is a slim underdog but the market has gone slightly too far against them.",
        ])
    elif fav_has_v and fav_e >= 0.08:
        s1 = pick([
            f"The model has strong conviction on {fav} and sees the current line as a significant mispricing.",
            f"This is one of the cleaner value plays of the day — {fav} looks clearly undervalued at this price.",
            f"The model's edge on {fav} is well above noise — they're getting a meaningfully better price than they deserve.",
        ])
    elif fav_has_v and fav_e >= 0.04:
        s1 = pick([
            f"The model likes {fav} here — they look undervalued given their overall profile.",
            f"{fav} stands out as a solid value play — the market hasn't fully accounted for their edge.",
            f"There's a real lean toward {fav} — they're not getting a bad price, but it's better than it should be.",
        ])
    elif fav_has_v:
        s1 = pick([
            f"There's a slim lean toward {fav}, though the market is close to fair.",
            f"The model gives {fav} a slight nod — it's at the thinner end of what qualifies as an edge.",
            f"{fav} gets the lean, though it's more of a tiebreaker than a clear mispricing at this line.",
        ])
    else:
        s1 = pick([
            f"The model leans {fav} but sees this as a well-priced game with no real inefficiency.",
            f"No clean value here — the model prefers {fav} but the market has this one about right.",
            f"This is close to a fair game — the model leans {fav} but isn't finding a meaningful edge at this line.",
        ])

    if has_pitchers and fav_ql and dog_ql:
        fav_poor   = any(x in fav_ql for x in ("average", "struggling"))
        dog_poor   = any(x in dog_ql for x in ("average", "struggling"))
        fav_strong = any(x in fav_ql for x in ("dominant", "sharp"))
        dog_strong = any(x in dog_ql for x in ("dominant", "sharp"))
        if fav_strong and dog_poor:
            s2 = pick([
                f"{fav} has a clear pitching edge — their starter has been {fav_ql} while the opposition has been {dog_ql}.",
                f"The pitching matchup strongly favors {fav}: a {fav_ql.split(',')[0]} arm against {dog}'s {dog_ql.split(',')[0]} starter.",
            ])
        elif dog_strong and fav_poor:
            s2 = pick([
                f"{dog}'s {dog_ql.split(',')[0]} starter is the main reason for the lean — they carry a real pitching edge.",
                f"The pitching matchup is the wildcard — {dog} sends a {dog_ql.split(',')[0]} arm against {fav}'s {fav_ql.split(',')[0]} starter.",
            ])
        elif fav_strong:
            s2 = pick([
                f"{fav}'s starter has been {fav_ql}, giving them a slight pitching edge even against a solid opponent.",
                f"The pitching edge goes to {fav} — their {fav_ql.split(',')[0]} starter adds another layer to the lean.",
            ])
        elif dog_strong:
            s2 = pick([
                f"{dog} counters with a {dog_ql.split(',')[0]} arm, which tempers the edge despite the overall lean.",
                f"{dog}'s starter has been {dog_ql} — keep an eye on the pitching matchup before the lean feels clean.",
            ])
        else:
            s2 = pick([
                f"Both starters have been {fav_ql.split(',')[0]} — team-level quality is the main differentiator.",
                f"The pitching matchup is fairly even, so lineup depth and recent form carry the model's decision.",
            ])
    elif (pythag := _pythag_note(fav_stats, dog_stats, fav, dog)):
        s2 = pythag + "."
    elif fav_form or dog_form:
        if fav_form and dog_form:
            s2 = pick([
                f"{fav} has been {fav_form} while {dog} has been {dog_form}, reinforcing the model's direction.",
                f"Recent form lines up with the lean — {fav} {fav_form}, {dog} {dog_form} over the last ten games.",
            ])
        elif fav_form:
            s2 = pick([
                f"{fav} has been {fav_form} lately, which aligns with the model's lean.",
                f"The recent run from {fav} reinforces the call — they've been {fav_form}.",
            ])
        else:
            s2 = pick([
                f"{dog} has been {dog_form} — worth noting against a team the model already prefers.",
                f"The concern with the lean is {dog}'s recent stretch — they've been {dog_form}.",
            ])
    else:
        rd_note = _run_diff_note(fav_stats, fav) or _run_diff_note(dog_stats, dog)
        if rd_note:
            s2 = f"{rd_note}, which factors into the model's overall read on this game."
        else:
            s2 = pick([
                "The lean is driven by season-level team quality — no strong recent-form signal either way.",
                "It comes down to overall team strength, with no single recent factor driving the decision.",
            ])

    return f"{s1} {s2}"


_H2H_MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

def h2h_html(games: list[dict], home_team: str, away_team: str) -> str:
    if not games:
        return ""

    home_nick = home_team.split()[-1]
    away_nick = away_team.split()[-1]
    home_wins = sum(1 for g in games if g["winner"].split()[-1] == home_nick)
    away_wins = len(games) - home_wins

    rows = ""
    for g in games:
        d = g["date"]
        try:
            y, m, day = d.split("-")
            date_str = f"{_H2H_MONTHS[int(m)-1]} {int(day)}, {y}"
        except Exception:
            date_str = d

        g_away_nick = g["away_name"].split()[-1]
        g_home_nick = g["home_name"].split()[-1]
        score  = f"{g['away_score']}–{g['home_score']}"
        w_nick = g["winner"].split()[-1]

        if w_nick == home_nick:   w_color = c["green"]
        elif w_nick == away_nick: w_color = c["red"]
        else:                     w_color = c["muted"]

        rows += (
            f'<div style="display:flex;gap:12px;padding:1px 0;font-size:0.73rem;color:{c["muted"]};line-height:1.7;">'
            f'<span style="min-width:90px;flex-shrink:0;">{date_str}</span>'
            f'<span style="min-width:105px;flex-shrink:0;">{g_away_nick} @ {g_home_nick}</span>'
            f'<span style="min-width:36px;font-variant-numeric:tabular-nums;">{score}</span>'
            f'<span style="color:{w_color};font-weight:600;">{w_nick}</span>'
            f'</div>'
        )

    record = f"{away_nick} {away_wins}–{home_wins} {home_nick}"
    return (
        f'<div style="margin-top:10px;padding-top:8px;border-top:1px solid {c["border"]};">'
        f'<div style="display:flex;justify-content:space-between;font-size:0.71rem;'
        f'text-transform:uppercase;letter-spacing:0.04em;color:{c["muted"]};margin-bottom:5px;">'
        f'<span>Last {len(games)} Meetings</span><span>{record}</span></div>'
        f'{rows}'
        f'</div>'
    )

def trend_html(era_trend):
    if era_trend < -0.30:  return '<span class="trend-better">▼ Improving</span>'
    elif era_trend > 0.30: return '<span class="trend-worse">▲ Declining</span>'
    else:                  return '<span class="trend-flat">— Stable</span>'

def pitcher_card_html(sp: dict, team: str, is_home: bool) -> str:
    if not sp or not sp.get("found"):
        return (
            f'<div class="pitcher-box">'
            f'<span class="pitcher-stat">No pitcher announced — <strong>{team}</strong></span>'
            f'</div>'
        )
    trend = trend_html(sp.get("era_trend", 0))
    role  = "Home" if is_home else "Away"
    return f"""
    <div class="pitcher-box">
        <div>
            <span style="color:{c['muted']}; font-size:0.75rem; text-transform:uppercase; letter-spacing:0.05em;">{role} · {team}</span><br>
            <span class="pitcher-name">{sp.get('name','')}</span> &nbsp;·&nbsp; {trend}
        </div>
        <div class="pitcher-stat" style="margin-top:4px;">
            Season: ERA <strong>{sp['era']}</strong> &nbsp;
            WHIP <strong>{sp['whip']}</strong> &nbsp;
            K/9 <strong>{sp['k9']}</strong> &nbsp;
            BB/9 <strong>{sp['bb9']}</strong>
            &nbsp;&nbsp;|&nbsp;&nbsp;
            Last 3 Starts: ERA <strong>{sp['recent_era']}</strong> &nbsp;
            WHIP <strong>{sp['recent_whip']}</strong>
        </div>
    </div>
    """

# ── Init slip state ────────────────────────────────────────────────────────────
if "real_slip" not in st.session_state:
    st.session_state["real_slip"] = []
if "paper_slip" not in st.session_state:
    st.session_state["paper_slip"] = []

# Pre-fill stake suggestions whenever the slip or budget changes.
# Read budget from session state (set by the sidebar widgets below, which rendered
# on the *previous* run via Streamlit's top-down model).
_budget       = float(st.session_state.get("budget_real", 50.0))
_paper_bal    = float(st.session_state.get("budget_paper", 100.0))
_real_slip    = st.session_state["real_slip"]

_real_ctx = (tuple(_real_slip), _budget)
if st.session_state.get("_real_ctx") != _real_ctx and _real_slip:
    st.session_state["_real_ctx"] = _real_ctx
    valid = [b for b in _real_slip if b in game_best]
    if valid:
        raw = [max(game_best[b]["home_edge"] if game_best[b]["home_edge"] >= game_best[b]["away_edge"]
                   else game_best[b]["away_edge"], 0.01) for b in valid]
        total_e = sum(raw)
        sugg = [round(_budget * e / total_e, 2) for e in raw]
        sugg[-1] = round(_budget - sum(sugg[:-1]), 2)
        for bid, s in zip(valid, sugg):
            st.session_state[f"stake_{bid}"] = float(s)

# ── Visible games + Select All controls ────────────────────────────────────────
# Games that actually render as cards (mirrors the per-card filter below).
visible_ids = [
    base_id for base_id, entries in sorted_games
    if show_avoid
    or game_best_edge(entries) >= -0.02
    or any(e["home_has_value"] or e["away_has_value"] for e in entries)
]

def _toggle_select_all(slip_key: str):
    """Select all visible games into the slip, or deselect all if already full."""
    slip = st.session_state[slip_key]
    all_selected = bool(visible_ids) and all(b in slip for b in visible_ids)
    if all_selected:
        for b in visible_ids:
            if b in slip:
                slip.remove(b)
            if slip_key == "real_slip":
                st.session_state.pop(f"stake_{b}", None)
    else:
        for b in visible_ids:
            if b not in slip:
                slip.append(b)

_all_real  = bool(visible_ids) and all(b in st.session_state["real_slip"]  for b in visible_ids)
_all_paper = bool(visible_ids) and all(b in st.session_state["paper_slip"] for b in visible_ids)

sa1, sa2, _sa3 = st.columns([1.6, 1.6, 4])
with sa1:
    if st.button(
        "✓ Deselect All Real" if _all_real else "💰 Select All Real",
        key="select_all_real",
        type="primary" if _all_real else "secondary",
        use_container_width=True,
        disabled=not visible_ids,
    ):
        _toggle_select_all("real_slip")
        st.rerun()
with sa2:
    if st.button(
        "✓ Deselect All Paper" if _all_paper else "📋 Select All Paper",
        key="select_all_paper",
        type="primary" if _all_paper else "secondary",
        use_container_width=True,
        disabled=not visible_ids,
    ):
        _toggle_select_all("paper_slip")
        st.rerun()

# ── Game cards ─────────────────────────────────────────────────────────────────
def _l10_color(record: str) -> str:
    try:
        w = int(record.split("-")[0])
        if w >= 7: return c["green"]
        if w <= 3: return c["red"]
    except Exception:
        pass
    return c["muted"]

for base_id, entries in sorted_games:
    sample    = entries[0]
    best_edge = game_best_edge(entries)

    if not show_avoid and best_edge < -0.02 and not any(e["home_has_value"] or e["away_has_value"] for e in entries):
        continue

    signal_cls = game_signal_class(best_edge)

    try:
        dt = datetime.fromisoformat(sample["commence_time"].replace("Z", "+00:00"))
        et_offset = timedelta(hours=-4)
        dt_et     = dt + et_offset
        today_et  = (datetime.now(timezone.utc) + et_offset).date()
        if dt_et.date() == today_et:
            time_str = dt_et.strftime("%I:%M %p ET").lstrip("0") + "  ·  Today"
        else:
            time_str = dt_et.strftime("%I:%M %p ET").lstrip("0") + "  ·  " + dt_et.strftime("%a %b %-d")
    except Exception:
        time_str = ""

    pitcher_badge_html = '<span class="pitcher-badge">🎯 Pitcher-Enhanced</span>' if sample["has_pitchers"] else ""
    pf_badge      = park_factor_badge(sample["home_team"])
    wx_data       = load_weather(sample["home_team"])
    wx_badge_html = weather_badges(wx_data)
    ctx_html      = ""
    if pf_badge or wx_badge_html:
        ctx_html = f'<div class="context-row">{pf_badge} {wx_badge_html}</div>'

    away_l10       = fmt_last_ten(sample["away_team"])
    home_l10       = fmt_last_ten(sample["home_team"])
    away_l10_color = _l10_color(away_l10)
    home_l10_color = _l10_color(home_l10)

    h2h_data    = load_h2h(sample["home_team"], sample["away_team"])
    h2h_section = h2h_html(h2h_data, sample["home_team"], sample["away_team"])

    _at = sample["away_team"]
    _ht = sample["home_team"]

    st.markdown(f"""
<div class="game-block {signal_cls}">
  <div class="game-matchup">
    {_at} <span class="game-at">@</span> {_ht}
    {pitcher_badge_html}
  </div>
  <div class="game-meta">🕐 {time_str}</div>
  <div style="font-size:0.78rem;color:{c['muted']};margin-bottom:0.4rem;">
    Last 10 &nbsp;—&nbsp;<strong style="color:{c['text2']};">{_at}</strong>:&nbsp;
    <span style="color:{away_l10_color};font-weight:700;">{away_l10}</span>
    &nbsp;·&nbsp;<strong style="color:{c['text2']};">{_ht}</strong>:&nbsp;
    <span style="color:{home_l10_color};font-weight:700;">{home_l10}</span>
  </div>
  {ctx_html}
  {h2h_section}
</div>
""", unsafe_allow_html=True)

    if sample["has_pitchers"]:
        pc1, pc2 = st.columns(2)
        with pc1:
            st.markdown(pitcher_card_html(sample["home_sp"], sample["home_team"], is_home=True), unsafe_allow_html=True)
        with pc2:
            st.markdown(pitcher_card_html(sample["away_sp"], sample["away_team"], is_home=False), unsafe_allow_html=True)

    # AI insight
    _insight_key = f"insight_{base_id}"
    if _insight_key in st.session_state:
        st.markdown(
            f'<div style="font-size:0.97rem;line-height:1.65;padding:10px 16px;'
            f'border-left:3px solid {c["accent"]};margin:10px 0 6px 0;font-style:italic;opacity:0.92;">'
            f'💡 {st.session_state[_insight_key]}</div>',
            unsafe_allow_html=True,
        )
    else:
        if st.button("✨ Generate AI Insight", key=f"btn_insight_{base_id}"):
            with st.spinner("Generating insight..."):
                st.session_state[_insight_key] = justify_prediction(sample, home_l10, away_l10, stats_df)
            st.rerun()

    # Odds table
    hcols = st.columns([2, 1.4, 1.4, 1.8, 1.8, 2])
    hcols[0].markdown("**Book**")
    hcols[1].markdown(f"**{sample['away_team']}**")
    hcols[2].markdown(f"**{sample['home_team']}**")
    hcols[3].markdown("**Market Prob**")
    hcols[4].markdown("**Model Prob**")
    hcols[5].markdown("**Signal**")

    for e in entries:
        cols = st.columns([2, 1.4, 1.4, 1.8, 1.8, 2])
        cols[0].markdown(book_badge_html(e["bookmaker_key"], e["bookmaker"]), unsafe_allow_html=True)
        cols[1].markdown(f"`{fmt_ml(e['away_ml'])}`")
        cols[2].markdown(f"`{fmt_ml(e['home_ml'])}`")
        cols[3].markdown(f"{fmt_pct(e['away_implied_prob'])} / {fmt_pct(e['home_implied_prob'])}")

        away_cls = edge_class(e["away_edge"])
        home_cls = edge_class(e["home_edge"])
        cols[4].markdown(
            f'<span class="{away_cls}">{fmt_pct(e["away_model_prob"])}</span>'
            f' / <span class="{home_cls}">{fmt_pct(e["home_model_prob"])}</span>',
            unsafe_allow_html=True,
        )

        if e["away_has_value"] or e["home_has_value"]:
            if e["away_has_value"] and (not e["home_has_value"] or e["away_edge"] >= e["home_edge"]):
                sig, kelly, team = e["away_rec"], e["away_kelly"], e["away_team"]
            else:
                sig, kelly, team = e["home_rec"], e["home_kelly"], e["home_team"]
            cols[5].markdown(sig)
            cols[5].caption(f"{team} · Kelly: {kelly*100:.1f}%")
        else:
            best_sig = e["home_rec"] if e["home_edge"] >= e["away_edge"] else e["away_rec"]
            cols[5].markdown(best_sig)

    # Bet slip buttons
    real_in  = base_id in st.session_state["real_slip"]
    paper_in = base_id in st.session_state["paper_slip"]

    bc1, bc2, _ = st.columns([1.3, 1.3, 5])
    with bc1:
        if real_in:
            if st.button("✓ Real Added", key=f"rb_{base_id}", type="primary", use_container_width=True):
                st.session_state["real_slip"].remove(base_id)
                st.session_state.pop(f"stake_{base_id}", None)
                st.rerun()
        else:
            if st.button("💰 Real Bet", key=f"rb_{base_id}", use_container_width=True):
                st.session_state["real_slip"].append(base_id)
                st.rerun()

    with bc2:
        if paper_in:
            if st.button("✓ Paper Added", key=f"pb_{base_id}", type="primary", use_container_width=True):
                st.session_state["paper_slip"].remove(base_id)
                st.rerun()
        else:
            if st.button("📋 Paper Bet", key=f"pb_{base_id}", use_container_width=True):
                st.session_state["paper_slip"].append(base_id)
                st.rerun()

    st.markdown("---")

st.caption("⚠️ Model uses season-level stats + pitcher data when available. Not financial advice. Gamble responsibly.")

# ── Sidebar: Bet Slip ─────────────────────────────────────────────────────────
# This block runs after all game data is computed, so game_best is fully populated.
real_slip  = st.session_state["real_slip"]
paper_slip = st.session_state["paper_slip"]

with st.sidebar:
    total_slip = len(real_slip) + len(paper_slip)
    slip_label = f"🎰 Bet Slip" + (f" · {total_slip}" if total_slip > 0 else "")
    st.markdown(f"### {slip_label}")

    if not real_slip and not paper_slip:
        st.markdown(
            f'<p style="color:{c["muted"]};font-size:0.83rem;line-height:1.5;">'
            'Use <strong>💰 Real Bet</strong> or <strong>📋 Paper Bet</strong> '
            'buttons on any game card to add bets here.</p>',
            unsafe_allow_html=True,
        )

    # ── Real bets ──────────────────────────────────────────────────────────────
    if real_slip:
        st.markdown('<div class="slip-section">💰 Real Bets</div>', unsafe_allow_html=True)

        budget = st.number_input(
            "Budget ($)",
            min_value=1.0, max_value=100_000.0, value=50.0, step=5.0,
            key="budget_real",
        )

        real_configs = {}
        for bid in real_slip:
            if bid not in game_best:
                continue
            g     = game_best[bid]
            books = books_by_game.get(bid, [g])
            book_names = [b["bookmaker"] for b in books]

            short_away = g["away_team"].split()[-1]
            short_home = g["home_team"].split()[-1]

            lbl_col, x_col = st.columns([5, 1])
            with lbl_col:
                st.markdown(
                    f'<div class="slip-game-label">{short_away} @ {short_home}</div>',
                    unsafe_allow_html=True,
                )
            with x_col:
                if st.button("✕", key=f"rm_real_{bid}", help="Remove from slip"):
                    if bid in st.session_state["real_slip"]:
                        st.session_state["real_slip"].remove(bid)
                    for k in (f"stake_{bid}", f"side_{bid}", f"book_{bid}"):
                        st.session_state.pop(k, None)
                    st.rerun()

            rec_idx = 0 if g["home_edge"] >= g["away_edge"] else 1
            side_opts = [g["home_team"], g["away_team"]]
            side = st.radio(
                f"Side",
                side_opts,
                index=rec_idx,
                key=f"side_{bid}",
                horizontal=True,
                label_visibility="collapsed",
                format_func=lambda x: x.split()[-1],
            )

            default_book_idx = next(
                (i for i, b in enumerate(books) if b["bookmaker_key"] == "caesars"), 0
            )
            book_name = st.selectbox(
                "Book",
                book_names,
                index=default_book_idx,
                key=f"book_{bid}",
                label_visibility="collapsed",
            )
            sel_book = next((b for b in books if b["bookmaker"] == book_name), books[0])

            is_home    = (side == g["home_team"])
            ch_odds    = sel_book["home_ml"]    if is_home else sel_book["away_ml"]
            ch_edge    = g["home_edge"]         if is_home else g["away_edge"]
            ch_prob    = g["home_model_prob"]   if is_home else g["away_model_prob"]
            ch_impl    = g["home_implied_prob"] if is_home else g["away_implied_prob"]

            stake = st.number_input(
                "Stake ($)",
                min_value=0.0, step=1.0, format="%.2f",
                key=f"stake_{bid}",
            )

            payout = calc_payout(stake, ch_odds) if stake > 0 else 0.0
            e_color = c["green"] if ch_edge >= 0.04 else c["amber"] if ch_edge >= 0.01 else c["muted"]
            win_txt = f" · Win: +${payout:.2f}" if stake > 0 else ""
            st.markdown(
                f'<div class="slip-meta" style="color:{e_color};">'
                f'{fmt_ml(ch_odds)} &nbsp;·&nbsp; Edge: {ch_edge*100:+.1f}%{win_txt}</div>',
                unsafe_allow_html=True,
            )

            real_configs[bid] = {
                "game":       g,
                "team":       side,
                "odds":       ch_odds,
                "edge":       ch_edge,
                "model_prob": ch_prob,
                "impl_prob":  ch_impl,
                "stake":      stake,
                "bookmaker":  book_name,
                "features":   game_best[bid].get("features", {}),
            }

        # Real summary
        total_real  = sum(rc["stake"] for rc in real_configs.values())
        active_real = {b: rc for b, rc in real_configs.items() if rc["stake"] > 0}
        if total_real > 0:
            ev = sum(
                rc["stake"] * (rc["model_prob"] * calc_payout(1, rc["odds"]) - (1 - rc["model_prob"]))
                for rc in active_real.values()
            )
            over    = total_real > budget + 0.01
            t_color = c["red"] if over else c["text"]
            st.markdown(
                f'<div class="slip-summary">'
                f'<span style="color:{t_color};">Total: <strong>${total_real:.2f}</strong> / ${budget:.2f}</span>'
                f'<br><span style="color:{c["muted"]};font-size:0.75rem;">EV: {"+" if ev >= 0 else ""}${ev:.2f}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        if st.button("💰 Log Real Bets", type="primary", use_container_width=True, key="log_real"):
            conn = get_connection()
            logged = 0
            for bid, rc in real_configs.items():
                if rc["stake"] == 0:
                    continue
                g = rc["game"]
                conn.execute("""
                    INSERT INTO bets
                        (game_date, home_team, away_team, bet_on, odds, stake, model_prob, implied_prob, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(date.today()),
                    g["home_team"], g["away_team"], rc["team"],
                    int(rc["odds"]), rc["stake"], rc["model_prob"], rc["impl_prob"],
                    f"Via {rc['bookmaker']} · Edge: {rc['edge']*100:+.1f}%",
                ))
                logged += 1
            conn.commit()
            conn.close()
            if logged:
                st.success(f"✅ {logged} bet(s) logged!")
                for bid in list(real_configs.keys()):
                    for k in [f"stake_{bid}", f"side_{bid}", f"book_{bid}"]:
                        st.session_state.pop(k, None)
                st.session_state["real_slip"] = []
                st.rerun()

        st.divider()

    # ── Paper bets ─────────────────────────────────────────────────────────────
    if paper_slip:
        st.markdown('<div class="slip-section">📋 Paper Bets</div>', unsafe_allow_html=True)

        paper_balance = st.number_input(
            "Paper balance ($)",
            min_value=1.0, max_value=100_000.0, value=100.0, step=5.0,
            key="budget_paper",
        )

        # Build paper rows with auto-allocation
        paper_items = []
        for bid in paper_slip:
            if bid not in game_best:
                continue
            g       = game_best[bid]
            is_home = g["home_edge"] >= g["away_edge"]
            rec_edge = max(g["home_edge"] if is_home else g["away_edge"], 0.01)
            paper_items.append({
                "bid":      bid,
                "g":        g,
                "is_home":  is_home,
                "rec_edge": rec_edge,
                "rec_team": g["home_team"]        if is_home else g["away_team"],
                "rec_odds": g["home_ml"]           if is_home else g["away_ml"],
                "model_p":  g["home_model_prob"]   if is_home else g["away_model_prob"],
                "impl_p":   g["home_implied_prob"] if is_home else g["away_implied_prob"],
                "rec_text": g["home_rec"]          if is_home else g["away_rec"],
                "features": game_best[bid].get("features", {}),
            })

        if paper_items:
            total_edge_p = sum(item["rec_edge"] for item in paper_items)
            for i, item in enumerate(paper_items):
                item["stake"] = round(paper_balance * item["rec_edge"] / total_edge_p, 2)
            paper_items[-1]["stake"] = round(
                paper_balance - sum(item["stake"] for item in paper_items[:-1]), 2
            )
            for item in paper_items:
                item["payout"] = calc_payout(item["stake"], item["rec_odds"])

            total_paper = sum(item["stake"] for item in paper_items)
            for item in paper_items:
                g = item["g"]
                short_away = g["away_team"].split()[-1]
                short_home = g["home_team"].split()[-1]
                e_color = c["green"] if item["rec_edge"] >= 0.04 else c["amber"] if item["rec_edge"] >= 0.01 else c["muted"]
                card_col, x_col = st.columns([5, 1])
                with card_col:
                    st.markdown(
                        f'<div class="slip-game">'
                        f'<div class="slip-game-label">{short_away} @ {short_home}</div>'
                        f'<div class="slip-meta">Auto: {item["rec_team"].split()[-1]} {fmt_ml(item["rec_odds"])}</div>'
                        f'<div class="slip-meta" style="color:{e_color};">'
                        f'Stake: ${item["stake"]:.2f} · Edge: {item["rec_edge"]*100:+.1f}% · Win: +${item["payout"]:.2f}'
                        f'</div></div>',
                        unsafe_allow_html=True,
                    )
                with x_col:
                    if st.button("✕", key=f"rm_paper_{item['bid']}", help="Remove from slip"):
                        if item["bid"] in st.session_state["paper_slip"]:
                            st.session_state["paper_slip"].remove(item["bid"])
                        st.rerun()

            st.markdown(
                f'<div class="slip-summary">'
                f'Total: <strong>${total_paper:.2f}</strong> / ${paper_balance:.2f}'
                f'</div>',
                unsafe_allow_html=True,
            )

            if st.button("📋 Log Paper Bets", type="primary", use_container_width=True, key="log_paper"):
                conn = get_connection()
                logged = 0
                for item in paper_items:
                    feats = item["features"]
                    conn.execute("""
                        INSERT INTO paper_bets (
                            game_date, home_team, away_team, bet_on, odds, stake,
                            model_prob, implied_prob, notes,
                            win_pct_diff, pythag_diff, run_diff_diff, rs_diff, ra_diff, home_advantage,
                            sp_era_diff, sp_whip_diff, sp_k9_diff, sp_bb9_diff
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        str(date.today()),
                        item["g"]["home_team"], item["g"]["away_team"],
                        item["rec_team"], int(item["rec_odds"]),
                        item["stake"], item["model_p"], item["impl_p"],
                        f"Edge: {item['rec_edge']*100:+.1f}%",
                        feats.get("win_pct_diff"), feats.get("pythag_diff"),
                        feats.get("run_diff_diff"), feats.get("rs_diff"),
                        feats.get("ra_diff"),       feats.get("home_advantage"),
                        feats.get("sp_era_diff"),   feats.get("sp_whip_diff"),
                        feats.get("sp_k9_diff"),    feats.get("sp_bb9_diff"),
                    ))
                    logged += 1
                conn.commit()
                conn.close()
                if logged:
                    st.success(f"✅ {logged} paper bet(s) logged!")
                    st.session_state["paper_slip"] = []
                    st.rerun()
