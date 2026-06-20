"""
pages/6_Live_Scores.py — Live box scores for today's MLB games
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import streamlit as st
import requests
from datetime import datetime, timezone, timedelta
from database import get_connection, init_db
from theme import init_theme, palette
from auth import require_login, selected_user_id, user_clause

init_db()

BASE = "https://statsapi.mlb.com/api/v1"

st.set_page_config(page_title="Live Scores", page_icon="📺", layout="wide")
init_theme("#be185d")   # pink — live scores
require_login()
c = palette()   # active theme colors for inline HTML

st.title("📺 Live Box Scores")
st.caption("Inning-by-inning scores for today's MLB games · Press refresh to update")

# Live Scores tags games you bet on — keep it to the viewed user's bets, never a
# mix across users (1.5). Admin → sidebar picker (default self).
view_uid = selected_user_id()

# ── Controls ───────────────────────────────────────────────────────────────────
col_btn, col_toggle, col_time = st.columns([1, 2, 2])
with col_btn:
    do_refresh = st.button("🔄 Refresh Scores", use_container_width=True)
with col_toggle:
    show_all = st.toggle("Show all today's games", value=False)
with col_time:
    st.caption(f"Last loaded: {datetime.now().strftime('%I:%M:%S %p')}")

if do_refresh:
    st.cache_data.clear()
    st.rerun()

st.divider()

# ── Data fetching ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def fetch_todays_schedule(date_str: str) -> list:
    url = f"{BASE}/schedule"
    params = {"sportId": 1, "date": date_str, "hydrate": "linescore,probablePitchers"}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
    except Exception:
        return []
    games = []
    for date_entry in resp.json().get("dates", []):
        for game in date_entry.get("games", []):
            games.append(game)
    return games


def load_todays_bets(date_str: str, uid: int | None) -> list[dict]:
    conn = get_connection()
    uclause, uparams = user_clause(uid, has_where=True)
    rows = conn.execute(
        f"SELECT home_team, away_team, bet_on, odds, outcome FROM bets WHERE game_date = ?{uclause}",
        (date_str, *uparams),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _normalize(name: str) -> str:
    return name.strip().lower()


def _teams_match(api_name: str, bet_name: str) -> bool:
    a, b = _normalize(api_name), _normalize(bet_name)
    if a == b:
        return True
    return a.split()[-1] == b.split()[-1]


def _get_bet(game: dict, bets: list[dict]) -> dict | None:
    home = game.get("teams", {}).get("home", {}).get("team", {}).get("name", "")
    away = game.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
    for bet in bets:
        if _teams_match(home, bet["home_team"]) and _teams_match(away, bet["away_team"]):
            return bet
    return None


# ── Status helpers ─────────────────────────────────────────────────────────────
def _game_time_et(game: dict) -> str:
    try:
        dt_utc = datetime.fromisoformat(game["gameDate"].replace("Z", "+00:00"))
        dt_et  = dt_utc.astimezone(timezone(timedelta(hours=-4)))
        return dt_et.strftime("%-I:%M %p ET")
    except Exception:
        return ""


def _status_html(game: dict) -> str:
    state    = game.get("status", {}).get("abstractGameState", "")
    detailed = game.get("status", {}).get("detailedState", "")
    ls       = game.get("linescore", {})

    if state == "Final":
        innings = ls.get("innings", [])
        suffix  = f"/{len(innings)}" if len(innings) > 9 else ""
        return f'<span class="status-final">✅ Final{suffix}</span>'

    if state == "Live":
        inning  = ls.get("currentInningOrdinal", "")
        inn_st  = ls.get("inningState", "")
        outs    = ls.get("outs", 0)
        out_str = f"{outs} out{'s' if outs != 1 else ''}"
        arrow   = "▲" if inn_st in ("Top", "Middle") else "▼"
        return f'<span class="status-live">🔴 {arrow} {inning} · {out_str}</span>'

    if detailed in ("Postponed", "Cancelled", "Suspended"):
        return f'<span class="status-delayed">⚠️ {detailed}</span>'

    # Pre-game / Scheduled
    return f'<span class="status-pre">⏰ {_game_time_et(game)}</span>'


def _bet_pill_html(bet: dict | None, home: str, away: str) -> str:
    if not bet:
        return ""
    outcome   = (bet.get("outcome") or "").strip()
    bet_on    = bet.get("bet_on", "")
    odds      = bet.get("odds", "")
    odds_str  = f"+{odds}" if isinstance(odds, int) and odds > 0 else str(odds)
    label     = f"💰 {bet_on} ({odds_str})"

    if outcome == "Win":
        return f'<span class="bet-pill bet-win">✅ {bet_on} · W</span>'
    if outcome == "Loss":
        return f'<span class="bet-pill bet-loss">❌ {bet_on} · L</span>'
    if outcome == "Push":
        return f'<span class="bet-pill bet-push">➖ {bet_on} · Push</span>'
    return f'<span class="bet-pill bet-active">{label}</span>'


# ── Box score renderer ─────────────────────────────────────────────────────────
def _render_boxscore(game: dict, away_name: str, home_name: str) -> str:
    ls      = game.get("linescore", {})
    innings = ls.get("innings", [])
    state   = game.get("status", {}).get("abstractGameState", "")

    inn_map: dict[int, dict] = {i["num"]: i for i in innings}
    current = ls.get("currentInning", 0) if state == "Live" else 0
    inn_st  = ls.get("inningState", "")
    max_inn = max(len(innings), 9)

    home_totals = ls.get("teams", {}).get("home", {})
    away_totals = ls.get("teams", {}).get("away", {})

    # Header
    headers = ["Team"] + [str(n) for n in range(1, max_inn + 1)] + ["R", "H", "E"]
    header_html = "".join(
        f'<th class="sep">{h}</th>' if h in ("R", "H", "E") else f"<th>{h}</th>"
        for h in headers
    )

    def _row(team_name: str, side: str) -> str:
        cells = [f"<td>{team_name}</td>"]
        for n in range(1, max_inn + 1):
            inn = inn_map.get(n, {})
            val = inn.get(side, {}).get("runs")

            if val is not None:
                # Bottom of last inning: "x" if home already wins (walk-off)
                if (
                    side == "home"
                    and n == max_inn
                    and state == "Final"
                    and inn.get("home", {}).get("isX", False)
                ):
                    cells.append('<td class="dim">x</td>')
                else:
                    cls = "current-inn" if (n == current) else ""
                    cells.append(f'<td class="{cls}">{val}</td>')
            elif n == current and side == "away" and inn_st in ("Top", "Middle"):
                cells.append('<td class="current-inn">—</td>')
            elif n == current and side == "home" and inn_st in ("Bottom",):
                cells.append('<td class="current-inn">—</td>')
            elif n > (current if state == "Live" else max_inn + 1):
                cells.append('<td class="dim">-</td>')
            else:
                cells.append('<td class="dim">-</td>')

        r = away_totals.get("runs", "-") if side == "away" else home_totals.get("runs", "-")
        h = away_totals.get("hits", "-") if side == "away" else home_totals.get("hits", "-")
        e = away_totals.get("errors", "-") if side == "away" else home_totals.get("errors", "-")

        if r == "-":
            cells += ['<td class="sep dim">-</td>', '<td class="dim">-</td>', '<td class="dim">-</td>']
        else:
            cells += [
                f'<td class="sep totals">{r}</td>',
                f'<td class="totals">{h}</td>',
                f'<td class="totals">{e}</td>',
            ]
        return "<tr>" + "".join(cells) + "</tr>"

    body = _row(away_name, "away") + _row(home_name, "home")
    return f"""
    <table class="boxscore-table">
        <thead><tr>{header_html}</tr></thead>
        <tbody>{body}</tbody>
    </table>
    """


def _base_diamond_html(ls: dict, state: str) -> str:
    if state != "Live":
        return ""
    offense   = ls.get("offense", {})
    on_first  = bool(offense.get("first"))
    on_second = bool(offense.get("second"))
    on_third  = bool(offense.get("third"))

    active = "#f6c90e"  # yellow — occupied
    empty  = "#2d3748"  # dark gray — empty
    line_c = "#4a5568"

    c1 = active if on_first  else empty
    c2 = active if on_second else empty
    c3 = active if on_third  else empty

    s = 9  # half-width of each base square

    svg = (
        f'<svg width="72" height="72" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">'
        # base path lines: 3rd→2nd→1st only
        f'<line x1="17" y1="50" x2="50" y2="17" stroke="{line_c}" stroke-width="2"/>'
        f'<line x1="50" y1="17" x2="83" y2="50" stroke="{line_c}" stroke-width="2"/>'
        # 2nd base (top)
        f'<rect x="{50-s}" y="{17-s}" width="{2*s}" height="{2*s}" rx="2" fill="{c2}" transform="rotate(45,50,17)"/>'
        # 1st base (right)
        f'<rect x="{83-s}" y="{50-s}" width="{2*s}" height="{2*s}" rx="2" fill="{c1}" transform="rotate(45,83,50)"/>'
        # 3rd base (left)
        f'<rect x="{17-s}" y="{50-s}" width="{2*s}" height="{2*s}" rx="2" fill="{c3}" transform="rotate(45,17,50)"/>'
        # home plate omitted intentionally
        f'</svg>'
    )
    return f'<div style="display:inline-block; vertical-align:middle; margin-left:12px;">{svg}</div>'


def _probable_pitcher(game: dict, side: str) -> str:
    p = game.get("teams", {}).get(side, {}).get("probablePitcher")
    if p:
        return p.get("fullName", "TBD")
    return "TBD"


# ── Main ───────────────────────────────────────────────────────────────────────
today_str = datetime.now().strftime("%Y-%m-%d")

with st.spinner("Loading today's schedule..."):
    games    = fetch_todays_schedule(today_str)
    bets     = load_todays_bets(today_str, view_uid)

if not games:
    st.warning("No games found for today via MLB Stats API.")
    st.stop()

# Sort: Live first, then by game time
def _sort_key(g):
    state = g.get("status", {}).get("abstractGameState", "")
    order = {"Live": 0, "Preview": 1, "Final": 2}.get(state, 3)
    return (order, g.get("gameDate", ""))

games = sorted(games, key=_sort_key)

# Filter
if show_all:
    display_games = games
else:
    display_games = [g for g in games if _get_bet(g, bets)]

# Stats line
live_count  = sum(1 for g in games if g.get("status", {}).get("abstractGameState") == "Live")
final_count = sum(1 for g in games if g.get("status", {}).get("abstractGameState") == "Final")
pre_count   = len(games) - live_count - final_count

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total Games", len(games))
m2.metric("🔴 Live",     live_count)
m3.metric("✅ Final",    final_count)
m4.metric("⏰ Upcoming", pre_count)

st.divider()

if not display_games:
    if bets:
        st.info("None of today's tracked bets have matched games yet. Toggle 'Show all today's games' to see the full schedule.")
    else:
        st.info("No bets logged for today. Toggle 'Show all today's games' to see the full schedule, or log a bet in the Bet Tracker.")
    st.stop()

# ── Game cards ─────────────────────────────────────────────────────────────────
for game in display_games:
    home_name = game.get("teams", {}).get("home", {}).get("team", {}).get("name", "Home")
    away_name = game.get("teams", {}).get("away", {}).get("team", {}).get("name", "Away")
    bet       = _get_bet(game, bets)

    state         = game.get("status", {}).get("abstractGameState", "")
    ls            = game.get("linescore", {})
    status_html   = _status_html(game)
    diamond_html  = _base_diamond_html(ls, state)
    bet_pill      = _bet_pill_html(bet, home_name, away_name)
    boxscore_html = _render_boxscore(game, away_name, home_name)
    home_sp       = _probable_pitcher(game, "home")
    away_sp       = _probable_pitcher(game, "away")

    with st.container(border=True):
        # Title row
        st.markdown(
            f'<div style="display:flex; align-items:baseline; gap:8px;">'
            f'<span style="font-size:1.1rem; font-weight:800; color:{c["text"]};">'
            f'⚾ {away_name} <span style="color:{c["muted"]}">@</span> {home_name}'
            f'</span>{bet_pill}</div>',
            unsafe_allow_html=True,
        )
        # Status + base diamond inline
        st.markdown(
            f'<div style="display:flex; align-items:center; gap:4px;">'
            f'{status_html}{diamond_html}</div>',
            unsafe_allow_html=True,
        )
        # Probable pitchers
        st.markdown(
            f'<div class="pitcher-line">'
            f'🏠 <strong>{home_name}</strong>: {home_sp}'
            f' &nbsp;·&nbsp; '
            f'✈️ <strong>{away_name}</strong>: {away_sp}'
            f'</div>',
            unsafe_allow_html=True,
        )
        # Box score table
        st.markdown(boxscore_html, unsafe_allow_html=True)

st.caption("Data via MLB Stats API · Probable pitchers may change before first pitch · Refresh to update scores")
