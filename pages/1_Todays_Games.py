"""
pages/1_Todays_Games.py — Live odds + model predictions with auto pitcher detection
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'), override=True)

import streamlit as st

# # DEBUG — remove once confirmed working
# if "llm_debug" not in st.session_state:
#     try:
#         import anthropic as _ac
#         _client = _ac.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
#         _msg = _client.messages.create(
#             model="claude-haiku-4-5-20251001",
#             max_tokens=20,
#             messages=[{"role": "user", "content": "Say: ok"}],
#         )
#         st.session_state["llm_debug"] = f"✅ {_msg.content[0].text}"
#     except Exception as _e:
#         st.session_state["llm_debug"] = f"❌ {_e}"
# st.sidebar.write(st.session_state["llm_debug"])
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from ingestion.odds_client import fetch_mlb_odds
from ingestion.stats_scraper import get_full_team_stats
from ingestion.pitcher_scraper import search_pitcher, get_team_pitchers, get_probable_pitchers_today, get_head_to_head
from models.predictor import MLBPredictor, build_matchup_features, evaluate_value
from database import init_db
from theme import init_theme
from ingestion.park_weather import park_factor_badge, weather_badges, get_weather

init_db()

st.set_page_config(page_title="Today's Games", page_icon="⚾", layout="wide")

init_theme()

st.title("⚾ Today's MLB Games")
st.caption("Moneylines by sportsbook · Probable starters loaded automatically · Override in the expander if needed")

# ── Controls ───────────────────────────────────────────────────────────────────
col_refresh, col_filter = st.columns([1, 3])
with col_refresh:
    if st.button("🔄 Refresh Odds", use_container_width=True):
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
    )

tog1, tog2 = st.columns(2)
show_avoid     = tog1.toggle("Show games with no value", value=False)
show_all_times = tog2.toggle("Show started / other-day games", value=False)

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

# Deduplicate to one entry per base game
seen = {}
for g in odds_list:
    bid = g["base_game_id"]
    if bid not in seen or g["bookmaker_key"] == "caesars":
        seen[bid] = g
unique_games = list(seen.values())

# ── Auto-load probable pitchers ────────────────────────────────────────────────
@st.cache_data(ttl=1800)
def load_probable_pitchers(date_str: str) -> dict:
    """Fetch probable starters from MLB Stats API. Cached 30 min."""
    return get_probable_pitchers_today(date_str)

@st.cache_data(ttl=3600)
def load_pitcher_stats(name: str) -> dict:
    """Fetch season + recent stats for a pitcher. Cached 1 hour."""
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

# Auto-fetch probable pitchers once per session (or after refresh)
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
                f"home_{bid}",
                options=home_options,
                index=home_default,
                key=f"hp_{bid}",
                label_visibility="collapsed",
            )

        with c2:
            st.caption(f"✈️ {g['away_team']}")
            away_options = ["— Select pitcher —"] + away_roster
            away_default = away_options.index(away_probable) if away_probable in away_options else 0
            away_sel = st.selectbox(
                f"away_{bid}",
                options=away_options,
                index=away_default,
                key=f"ap_{bid}",
                label_visibility="collapsed",
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
        home_prob = predictor.predict_proba(features)
        model_cache[bid] = {
            "home_prob":    home_prob,
            "has_pitchers": home_sp is not None and away_sp is not None,
            "home_sp":      home_sp,
            "away_sp":      away_sp,
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
    })

# Group by base game
games_grouped = defaultdict(list)
for r in results:
    games_grouped[r["base_game_id"]].append(r)

def game_best_edge(entries):
    return max(max(e["home_edge"], e["away_edge"]) for e in entries)

sorted_games = sorted(games_grouped.items(), key=lambda x: game_best_edge(x[1]), reverse=True)

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
    """Read ANTHROPIC_API_KEY directly from .env file, falling back to os.environ."""
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

    # ── LLM commentary (Haiku) — falls back to procedural if key missing ──────
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
    # ─────────────────────────────────────────────────────────────────────────

    # Sentence 1 — value read (phrase pool seeded by game_id)
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

    # Sentence 2 — pitcher > pythag > form > run_diff > generic
    if has_pitchers and fav_ql and dog_ql:
        fav_poor   = any(x in fav_ql for x in ("average", "struggling"))
        dog_poor   = any(x in dog_ql for x in ("average", "struggling"))
        fav_strong = any(x in fav_ql for x in ("dominant", "sharp"))
        dog_strong = any(x in dog_ql for x in ("dominant", "sharp"))
        if fav_strong and dog_poor:
            s2 = pick([
                f"{fav} has a clear pitching edge — their starter has been {fav_ql} while the opposition has been {dog_ql}.",
                f"The pitching matchup strongly favors {fav}: a {fav_ql.split(',')[0]} arm against {dog}'s {dog_ql.split(',')[0]} starter.",
                f"{fav}'s starter has been {fav_ql}, and that advantage on the mound is a key part of the model's read.",
            ])
        elif dog_strong and fav_poor:
            s2 = pick([
                f"{dog}'s {dog_ql.split(',')[0]} starter is the main reason for the lean — they carry a real pitching edge.",
                f"The pitching matchup is the wildcard — {dog} sends a {dog_ql.split(',')[0]} arm against {fav}'s {fav_ql.split(',')[0]} starter.",
                f"Don't sleep on {dog}'s starter — {dog_ql}, which complicates the edge despite {fav}'s overall advantage.",
            ])
        elif fav_strong:
            s2 = pick([
                f"{fav}'s starter has been {fav_ql}, giving them a slight pitching edge even against a solid opponent.",
                f"The pitching edge goes to {fav} — their {fav_ql.split(',')[0]} starter adds another layer to the lean.",
                f"{fav} also gets the nod on the mound — their starter has been {fav_ql}.",
            ])
        elif dog_strong:
            s2 = pick([
                f"{dog} counters with a {dog_ql.split(',')[0]} arm, which tempers the edge despite the overall lean.",
                f"{dog}'s starter has been {dog_ql} — keep an eye on the pitching matchup before the lean feels clean.",
                f"The mound edge goes to {dog} in this one, which is the main reason this isn't a stronger call.",
            ])
        else:
            s2 = pick([
                f"Both starters have been {fav_ql.split(',')[0]} — team-level quality is the main differentiator.",
                f"The pitching matchup is fairly even, so lineup depth and recent form carry the model's decision.",
                f"With comparable arms on both sides, the edge comes from what these teams have shown over the full season.",
            ])
    elif (pythag := _pythag_note(fav_stats, dog_stats, fav, dog)):
        s2 = pythag + "."
    elif fav_form or dog_form:
        if fav_form and dog_form:
            s2 = pick([
                f"{fav} has been {fav_form} while {dog} has been {dog_form}, reinforcing the model's direction.",
                f"Recent form lines up with the lean — {fav} {fav_form}, {dog} {dog_form} over the last ten games.",
                f"The form picture backs the model: {fav} {fav_form} and {dog} {dog_form} lately.",
            ])
        elif fav_form:
            s2 = pick([
                f"{fav} has been {fav_form} lately, which aligns with the model's lean.",
                f"The recent run from {fav} reinforces the call — they've been {fav_form}.",
                f"{fav} comes in {fav_form}, adding weight to a model that already prefers them.",
            ])
        else:
            s2 = pick([
                f"{dog} has been {dog_form} — worth noting against a team the model already prefers.",
                f"The concern with the lean is {dog}'s recent stretch — they've been {dog_form}.",
                f"{dog}'s form is the thing to watch — they've been {dog_form}, which complicates the edge.",
            ])
    else:
        rd_note = _run_diff_note(fav_stats, fav) or _run_diff_note(dog_stats, dog)
        if rd_note:
            s2 = f"{rd_note}, which factors into the model's overall read on this game."
        else:
            s2 = pick([
                "The lean is driven by season-level team quality — no strong recent-form signal either way.",
                "It comes down to overall team strength, with no single recent factor driving the decision.",
                "The model is reading from longer-term trends here, with no clear recent catalyst in either direction.",
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

        if w_nick == home_nick:   w_color = "#68d391"
        elif w_nick == away_nick: w_color = "#fc8181"
        else:                     w_color = "#a0aec0"

        rows += (
            f'<div style="display:flex;gap:12px;padding:1px 0;font-size:0.73rem;color:#a0aec0;line-height:1.7;">'
            f'<span style="min-width:90px;flex-shrink:0;">{date_str}</span>'
            f'<span style="min-width:105px;flex-shrink:0;">{g_away_nick} @ {g_home_nick}</span>'
            f'<span style="min-width:36px;font-variant-numeric:tabular-nums;">{score}</span>'
            f'<span style="color:{w_color};font-weight:600;">{w_nick}</span>'
            f'</div>'
        )

    record = f"{away_nick} {away_wins}–{home_wins} {home_nick}"
    return (
        f'<div style="margin-top:10px;padding-top:8px;border-top:1px solid #2d3748;">'
        f'<div style="display:flex;justify-content:space-between;font-size:0.71rem;'
        f'text-transform:uppercase;letter-spacing:0.04em;color:#718096;margin-bottom:5px;">'
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
            <span style="color:#718096; font-size:0.75rem; text-transform:uppercase; letter-spacing:0.05em;">{role} · {team}</span><br>
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

# ── Game cards ─────────────────────────────────────────────────────────────────
for base_id, entries in sorted_games:
    sample    = entries[0]
    best_edge = game_best_edge(entries)

    if not show_avoid and best_edge < -0.02 and not any(e["home_has_value"] or e["away_has_value"] for e in entries):
        continue

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

    pitcher_badge = " 🎯 Pitcher-Enhanced" if sample["has_pitchers"] else ""

    pf_badge       = park_factor_badge(sample["home_team"])
    wx_data        = load_weather(sample["home_team"])
    wx_badge_html  = weather_badges(wx_data)
    ctx_html       = ""
    if pf_badge or wx_badge_html:
        ctx_html = f'<div class="context-row">{pf_badge} {wx_badge_html}</div>'

    away_l10 = fmt_last_ten(sample["away_team"])
    home_l10 = fmt_last_ten(sample["home_team"])

    def _l10_color(record: str) -> str:
        try:
            w = int(record.split("-")[0])
            if w >= 7: return "#68d391"
            if w <= 3: return "#fc8181"
        except Exception:
            pass
        return "#a0aec0"

    away_l10_color = _l10_color(away_l10)
    home_l10_color = _l10_color(home_l10)

    h2h_data    = load_h2h(sample["home_team"], sample["away_team"])
    h2h_section = h2h_html(h2h_data, sample["home_team"], sample["away_team"])

    _at  = sample["away_team"]
    _ht  = sample["home_team"]
    game_html = "".join([
        '<div class="game-block">',
        f'<div style="font-size:1.1rem;font-weight:800;color:#e2e8f0;">',
        f'⚾ {_at} <span style="color:#4a5568">@</span> {_ht}',
        f'<span style="font-size:0.75rem;color:#68d391;font-weight:400;">{pitcher_badge}</span>',
        '</div>',
        f'<div style="font-size:0.8rem;color:#718096;margin-bottom:0.2rem;">🕐 {time_str}</div>',
        '<div style="font-size:0.78rem;color:#a0aec0;margin-bottom:0.4rem;">',
        f'Last 10 &nbsp;—&nbsp;<strong>{_at}</strong>: ',
        f'<span style="color:{away_l10_color};font-weight:700;">{away_l10}</span>',
        f'&nbsp;·&nbsp;<strong>{_ht}</strong>: ',
        f'<span style="color:{home_l10_color};font-weight:700;">{home_l10}</span>',
        '</div>',
        ctx_html,
        h2h_section,
        '</div>',
    ])
    st.markdown(game_html, unsafe_allow_html=True)

    if sample["has_pitchers"]:
        pc1, pc2 = st.columns(2)
        with pc1:
            st.markdown(pitcher_card_html(sample["home_sp"], sample["home_team"], is_home=True), unsafe_allow_html=True)
        with pc2:
            st.markdown(pitcher_card_html(sample["away_sp"], sample["away_team"], is_home=False), unsafe_allow_html=True)

    _insight_key = f"insight_{base_id}"
    if _insight_key in st.session_state:
        st.markdown(
            f'<div style="font-size:0.97rem;line-height:1.65;padding:10px 16px;'
            f'border-left:3px solid #4a9eff;margin:10px 0 6px 0;font-style:italic;opacity:0.92;">'
            f'💡 {st.session_state[_insight_key]}</div>',
            unsafe_allow_html=True,
        )
    else:
        if st.button("✨ Generate AI Insight", key=f"btn_insight_{base_id}"):
            with st.spinner("Generating insight..."):
                st.session_state[_insight_key] = justify_prediction(sample, home_l10, away_l10, stats_df)
            st.rerun()

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

    st.markdown("---")

st.caption("⚠️ Model uses season-level stats + pitcher data when available. Not financial advice. Gamble responsibly.")
