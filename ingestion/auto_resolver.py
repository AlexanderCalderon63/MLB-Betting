"""
ingestion/auto_resolver.py

Auto-resolves pending bets using:
  - MLB Stats API  : final scores (Win/Loss/Postponed)
  - The Odds API   : historical h2h odds for closing line / CLV
"""

import os
import requests
import time
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'), override=True)

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
MLB_BASE     = "https://statsapi.mlb.com/api/v1"
ODDS_BASE    = "https://api.the-odds-api.com/v4"
HEADERS      = {"User-Agent": "Mozilla/5.0 (compatible; mlb-betting-app/1.0)"}

# Books to try for closing odds, in preference order
PREFERRED_BOOKS = [
    "williamhill_us",   # Caesars — first choice
    "betmgm",           # BetMGM  — second choice
]


# ── Team name helpers ──────────────────────────────────────────────────────────

def _team_match(a: str, b: str) -> bool:
    a, b = a.lower().strip(), b.lower().strip()
    return a == b or a in b or b in a or a.split()[-1] == b.split()[-1]


# ── MLB Stats API ──────────────────────────────────────────────────────────────

def _fetch_mlb_schedule(game_date: str) -> list:
    """Return all regular-season games on game_date from the MLB Stats API."""
    try:
        resp = requests.get(
            f"{MLB_BASE}/schedule",
            params={"sportId": 1, "date": game_date, "gameType": "R"},
            headers=HEADERS, timeout=10,
        )
        resp.raise_for_status()
        games = []
        for d in resp.json().get("dates", []):
            games.extend(d.get("games", []))
        return games
    except Exception as e:
        print(f"[AUTO] MLB schedule fetch failed for {game_date}: {e}")
        return []


def get_game_result(game_date: str, home_team: str, away_team: str) -> dict:
    """
    Returns dict with keys:
      status      : 'Final' | 'Postponed' | 'Pending'
      home_score  : int | None
      away_score  : int | None
      commence_time: ISO string | None   (used to query closing odds)
    """
    for game in _fetch_mlb_schedule(game_date):
        h = game["teams"]["home"]
        a = game["teams"]["away"]
        if not (_team_match(h["team"]["name"], home_team) and
                _team_match(a["team"]["name"], away_team)):
            continue

        state = game.get("status", {}).get("detailedState", "")
        if "Postponed" in state or "Cancelled" in state or "Suspended" in state:
            return {"status": "Postponed", "home_score": None, "away_score": None,
                    "commence_time": game.get("gameDate")}

        if state == "Final":
            return {
                "status":       "Final",
                "home_score":   h.get("score") or 0,
                "away_score":   a.get("score") or 0,
                "commence_time": game.get("gameDate"),
            }

        return {"status": "Pending", "home_score": None, "away_score": None,
                "commence_time": game.get("gameDate")}

    return {"status": "Pending", "home_score": None, "away_score": None,
            "commence_time": None}


# ── Odds API historical ────────────────────────────────────────────────────────

def _fetch_odds_at(timestamp_iso: str) -> list:
    """Fetch historical odds from the Odds API at a specific UTC timestamp."""
    if not ODDS_API_KEY:
        print("[AUTO] No Odds API key configured.")
        return []
    try:
        resp = requests.get(
            f"{ODDS_BASE}/sports/baseball_mlb/odds-history/",
            params={
                "apiKey":      ODDS_API_KEY,
                "bookmakers":  "williamhill_us",
                "markets":     "h2h",
                "oddsFormat":  "american",
                "date":        timestamp_iso,
            },
            timeout=15,
        )
        resp.raise_for_status()
        used      = resp.headers.get("x-requests-used", "?")
        remaining = resp.headers.get("x-requests-remaining", "?")
        data = resp.json()
        events = data.get("data", data) if isinstance(data, dict) else data
        n_events = len(events)
        n_books  = len(events[0].get("bookmakers", [])) if events else 0
        print(f"[AUTO] Odds API query at {timestamp_iso} — {n_events} events × {n_books} books = {used} credits used. Remaining: {remaining}")
        return data.get("data", data) if isinstance(data, dict) else data
    except Exception as e:
        print(f"[AUTO] Odds API fetch failed at {timestamp_iso}: {e}")
        return []


def _extract_odds(games: list, home_team: str, away_team: str, bet_on: str) -> int | None:
    """Extract American odds for bet_on team from an Odds API games list."""
    for game in games:
        h = game.get("home_team", "")
        a = game.get("away_team", "")
        if not (_team_match(h, home_team) and _team_match(a, away_team)):
            continue
        bookmakers = game.get("bookmakers", [])
        ordered    = sorted(
            bookmakers,
            key=lambda b: PREFERRED_BOOKS.index(b["key"]) if b["key"] in PREFERRED_BOOKS else 99,
        )
        for book in ordered:
            for market in book.get("markets", []):
                if market["key"] != "h2h":
                    continue
                for outcome in market["outcomes"]:
                    if _team_match(outcome["name"], bet_on):
                        return int(outcome["price"])
    return None


def get_closing_odds(commence_time_iso: str, home_team: str, away_team: str, bet_on: str) -> int | None:
    """Return closing American odds for bet_on team (5 min before game start)."""
    try:
        dt        = datetime.fromisoformat(commence_time_iso.replace("Z", "+00:00"))
        close_ts  = (dt - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None
    games = _fetch_odds_at(close_ts)
    return _extract_odds(games, home_team, away_team, bet_on)


# ── Main resolver ──────────────────────────────────────────────────────────────

def calc_pnl(stake: float, odds: int, outcome: str) -> float:
    if outcome == "Win":
        return round(stake * (odds / 100) if odds > 0 else stake * (100 / abs(odds)), 2)
    if outcome == "Loss":
        return round(-stake, 2)
    return 0.0


def to_implied_prob(odds: int) -> float:
    o = int(odds)
    return 100 / (o + 100) if o > 0 else abs(o) / (abs(o) + 100)


def backfill_entry_odds(table_name: str) -> dict:
    """
    For each bet in table_name, query the Odds API at the bet's created_at
    timestamp and update the odds column to match the actual market odds at
    that moment (Caesars → BetMGM → … priority).

    Groups bets by unique created_at to minimise API calls.
    Returns {"updated": n, "unchanged": n, "not_found": n}.
    """
    from collections import defaultdict
    from database import get_connection

    conn  = get_connection()
    rows  = conn.execute(
        f"SELECT id, home_team, away_team, bet_on, odds, created_at FROM {table_name}"
    ).fetchall()
    conn.close()

    # Group by created_at — one API call per unique timestamp
    groups: dict = defaultdict(list)
    for r in rows:
        groups[r["created_at"]].append(dict(r))

    updated   = 0
    unchanged = 0
    not_found = 0

    for created_at, bets in groups.items():
        try:
            dt         = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
            ts_iso     = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            not_found += len(bets)
            continue

        games = _fetch_odds_at(ts_iso)
        if not games:
            not_found += len(bets)
            print(f"[AUTO] No Odds API data for {ts_iso} — skipping {len(bets)} bet(s)")
            continue

        conn2 = get_connection()
        for bet in bets:
            market_odds = _extract_odds(games, bet["home_team"], bet["away_team"], bet["bet_on"])
            if market_odds is None:
                not_found += 1
                print(f"[AUTO] No odds found for {bet['bet_on']} ({bet['home_team']} vs {bet['away_team']}) at {ts_iso}")
                continue
            if market_odds == int(bet["odds"]):
                unchanged += 1
                continue
            print(f"[AUTO] {table_name} id={bet['id']} {bet['bet_on']}: {bet['odds']} -> {market_odds}")
            conn2.execute(f"UPDATE {table_name} SET odds = ? WHERE id = ?", (market_odds, bet["id"]))
            updated += 1

        conn2.commit()
        conn2.close()
        time.sleep(0.3)  # be polite to the API

    return {"updated": updated, "unchanged": unchanged, "not_found": not_found}


def refresh_closing_odds(dates: list) -> dict:
    """
    For each date in dates, fetch closing odds only for games that have
    actual bets in the bets or paper_bets tables.  One Odds API call per
    unique game start time among those games (10 credits each flat fee).

    Returns {"dates_processed": n, "games_cached": n, "api_calls": n, "errors": [...]}
    """
    from collections import defaultdict
    from database import get_connection

    games_cached = 0
    api_calls    = 0
    errors       = []

    # Build set of (home, away) pairs that have bets for each date
    needed: dict = defaultdict(set)  # game_date -> {(home, away), ...}
    conn_r = get_connection()
    for table in ("bets", "paper_bets"):
        try:
            for d in dates:
                for row in conn_r.execute(
                    f"SELECT DISTINCT home_team, away_team FROM {table} WHERE game_date = ?", (d,)
                ):
                    needed[d].add((_normalize_name(row["home_team"]), _normalize_name(row["away_team"])))
        except Exception:
            pass
    conn_r.close()

    conn = get_connection()

    for game_date in dates:
        date_needed = needed.get(game_date, set())
        if not date_needed:
            print(f"[CACHE] {game_date}: no bets found — skipping Odds API calls")
            continue

        schedule = _fetch_mlb_schedule(game_date)
        if not schedule:
            errors.append(f"{game_date}: no MLB schedule data")
            continue

        # Keep only games that have bets; group by commence_time
        by_time: dict = defaultdict(list)
        for game in schedule:
            h = _normalize_name(game["teams"]["home"]["team"]["name"])
            a = _normalize_name(game["teams"]["away"]["team"]["name"])
            if (h, a) not in date_needed:
                continue
            ct = game.get("gameDate")
            if ct:
                by_time[ct].append(game)

        if not by_time:
            errors.append(f"{game_date}: none of the bet games matched the MLB schedule")
            continue

        print(f"[CACHE] {game_date}: {len(date_needed)} bet game(s) across {len(by_time)} start time(s)")

        for ct, games in by_time.items():
            try:
                dt       = datetime.fromisoformat(ct.replace("Z", "+00:00"))
                close_ts = (dt - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                continue

            odds_data = _fetch_odds_at(close_ts)
            api_calls += 1

            if not odds_data:
                errors.append(f"{game_date} {ct}: Odds API returned no data")
                continue

            for game in games:
                h_name = _normalize_name(game["teams"]["home"]["team"]["name"])
                a_name = _normalize_name(game["teams"]["away"]["team"]["name"])

                home_odds = _extract_odds(odds_data, h_name, a_name, h_name)
                away_odds = _extract_odds(odds_data, h_name, a_name, a_name)

                if home_odds is None and away_odds is None:
                    errors.append(f"{game_date}: no odds found for {a_name} @ {h_name}")
                    continue

                # Find which bookmaker was used
                bookmaker = None
                for g in odds_data:
                    gh = g.get("home_team", "")
                    ga = g.get("away_team", "")
                    if _team_match(gh, h_name) and _team_match(ga, a_name):
                        books = sorted(
                            g.get("bookmakers", []),
                            key=lambda b: PREFERRED_BOOKS.index(b["key"]) if b["key"] in PREFERRED_BOOKS else 99,
                        )
                        bookmaker = books[0]["key"] if books else None
                        break

                try:
                    conn.execute("""
                        INSERT INTO closing_odds_cache
                            (game_date, home_team, away_team, commence_time,
                             home_closing_odds, away_closing_odds, bookmaker)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(game_date, home_team, away_team)
                        DO UPDATE SET
                            commence_time      = excluded.commence_time,
                            home_closing_odds  = excluded.home_closing_odds,
                            away_closing_odds  = excluded.away_closing_odds,
                            bookmaker          = excluded.bookmaker,
                            fetched_at         = NOW()::TEXT
                    """, (game_date, h_name, a_name, ct, home_odds, away_odds, bookmaker))
                    games_cached += 1
                except Exception as e:
                    errors.append(f"{game_date} {h_name} vs {a_name}: DB error — {e}")

        conn.commit()
        time.sleep(0.2)

    conn.close()
    print(f"[CACHE] {games_cached} games cached across {len(dates)} date(s), {api_calls} API calls")
    return {
        "dates_processed": len(dates),
        "games_cached":    games_cached,
        "api_calls":       api_calls,
        "errors":          errors,
    }


def _normalize_name(name: str) -> str:
    """Consistent team name normalization for cache lookups."""
    mapping = {
        "Athletics": "Oakland Athletics",
    }
    return mapping.get(name, name)


def _lookup_closing_odds_cache(game_date: str, home_team: str, away_team: str, bet_on: str):
    """
    Return closing odds for bet_on team from the cache, or None if not found.
    Uses fuzzy matching so minor name differences don't break lookups.
    """
    from database import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT home_team, away_team, home_closing_odds, away_closing_odds "
        "FROM closing_odds_cache WHERE game_date = ?",
        (game_date,)
    ).fetchall()
    conn.close()

    for row in rows:
        if _team_match(row["home_team"], home_team) and _team_match(row["away_team"], away_team):
            bet_on_home = _team_match(bet_on, row["home_team"])
            return row["home_closing_odds"] if bet_on_home else row["away_closing_odds"]
    return None


def batch_resolve_bets(bets: list) -> list:
    """
    Resolve multiple bets with minimal API calls:
      - One MLB schedule call per unique game_date
      - One Odds API call per unique commence_time
    bets: list of dicts with keys: id, game_date, home_team, away_team, bet_on, odds, stake
    Returns: list of {id, outcome, profit_loss, closing_odds, closing_implied_prob, clv, note}
    """
    from collections import defaultdict

    # Step 1: one MLB schedule call per unique date
    dates = set(b["game_date"] for b in bets)
    schedule: dict = {gd: _fetch_mlb_schedule(gd) for gd in dates}

    # Step 2: map each unique game to its result + commence_time
    game_info: dict = {}
    commence_times: set = set()

    for bet in bets:
        key = (bet["game_date"], bet["home_team"], bet["away_team"])
        if key in game_info:
            continue
        for game in schedule.get(bet["game_date"], []):
            h = game["teams"]["home"]
            a = game["teams"]["away"]
            if not (_team_match(h["team"]["name"], bet["home_team"]) and
                    _team_match(a["team"]["name"], bet["away_team"])):
                continue
            state = game.get("status", {}).get("detailedState", "")
            ct    = game.get("gameDate")
            if "Postponed" in state or "Cancelled" in state or "Suspended" in state:
                game_info[key] = {"status": "Postponed", "commence_time": ct}
            elif state == "Final":
                game_info[key] = {
                    "status":      "Final",
                    "home_score":  h.get("score") or 0,
                    "away_score":  a.get("score") or 0,
                    "commence_time": ct,
                }
                if ct:
                    commence_times.add(ct)
            else:
                game_info[key] = {"status": "Pending", "commence_time": ct}
            break
        else:
            game_info[key] = {"status": "Pending", "commence_time": None}

    # Step 3: build per-bet results (closing odds from cache only — no API fallback)
    results = []
    for bet in bets:
        key  = (bet["game_date"], bet["home_team"], bet["away_team"])
        info = game_info.get(key, {"status": "Pending", "commence_time": None})

        if info["status"] == "Pending":
            results.append({"id": bet["id"], "outcome": "Pending",
                            "profit_loss": None, "closing_odds": None,
                            "closing_implied_prob": None, "clv": None,
                            "note": "Game not yet final."})
            continue

        if info["status"] == "Postponed":
            results.append({"id": bet["id"], "outcome": "Postponed",
                            "profit_loss": None, "closing_odds": None,
                            "closing_implied_prob": None, "clv": None,
                            "note": "Game was postponed."})
            continue

        # Closing odds must come from cache — fail if missing
        closing_odds = _lookup_closing_odds_cache(
            bet["game_date"], bet["home_team"], bet["away_team"], bet["bet_on"]
        )
        if closing_odds is None:
            results.append({"id": bet["id"], "outcome": "NoCacheData",
                            "profit_loss": None, "closing_odds": None,
                            "closing_implied_prob": None, "clv": None,
                            "note": f"Closing odds not in cache for {bet['game_date']}. "
                                    f"Run 'Refresh Closing Odds' for this date first."})
            continue

        home_won    = info["home_score"] > info["away_score"]
        bet_on_home = _team_match(bet["bet_on"], bet["home_team"])
        outcome     = "Win" if (home_won == bet_on_home) else "Loss"
        pnl         = calc_pnl(bet["stake"], bet["odds"], outcome)
        cl_prob     = to_implied_prob(closing_odds)
        bet_prob    = to_implied_prob(bet["odds"])
        clv         = round(cl_prob - bet_prob, 6)

        results.append({
            "id": bet["id"], "outcome": outcome, "profit_loss": pnl,
            "closing_odds": closing_odds, "closing_implied_prob": cl_prob,
            "clv": clv,
            "note": f"Score: {bet['home_team']} {info['home_score']}-{info['away_score']} {bet['away_team']}",
        })

    return results


def resolve_bet(game_date: str, home_team: str, away_team: str,
                bet_on: str, original_odds: int, stake: float) -> dict:
    """
    Auto-resolve a single bet. Returns:
      {
        outcome          : 'Win' | 'Loss' | 'Postponed' | 'Pending' | 'Error',
        profit_loss      : float | None,
        closing_odds     : int | None,
        closing_implied_prob: float | None,
        clv              : float | None,
        note             : str,
      }
    """
    result = get_game_result(game_date, home_team, away_team)

    if result["status"] == "Pending":
        return {"outcome": "Pending", "profit_loss": None,
                "closing_odds": None, "closing_implied_prob": None,
                "clv": None, "note": "Game not yet final."}

    if result["status"] == "Postponed":
        return {"outcome": "Postponed", "profit_loss": None,
                "closing_odds": None, "closing_implied_prob": None,
                "clv": None, "note": "Game was postponed."}

    # Determine Win/Loss
    home_won    = result["home_score"] > result["away_score"]
    bet_on_home = _team_match(bet_on, home_team)
    outcome     = "Win" if (home_won == bet_on_home) else "Loss"
    pnl         = calc_pnl(stake, original_odds, outcome)

    # Fetch closing odds
    closing_odds = None
    cl_prob      = None
    clv          = None

    if result.get("commence_time"):
        closing_odds = get_closing_odds(
            result["commence_time"], home_team, away_team, bet_on
        )

    if closing_odds is not None:
        cl_prob  = to_implied_prob(closing_odds)
        bet_prob = to_implied_prob(original_odds)
        clv      = round(cl_prob - bet_prob, 6)

    note = f"Score: {home_team} {result['home_score']}-{result['away_score']} {away_team}"
    if closing_odds is None:
        note += " · Closing odds not found in Odds API (CLV not calculated)"

    return {
        "outcome":              outcome,
        "profit_loss":          pnl,
        "closing_odds":         closing_odds,
        "closing_implied_prob": cl_prob,
        "clv":                  clv,
        "note":                 note,
    }
