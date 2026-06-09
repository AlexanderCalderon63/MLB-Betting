"""
ingestion/pitcher_scraper.py — Fetches pitcher stats from the MLB Stats API.

MLB's official API at statsapi.mlb.com is free, requires no key, and is
designed for programmatic access — no scraping, no 403s.

Flow:
  1. Search for player by name → get MLB player ID
  2. Fetch season pitching stats (ERA, WHIP, K/9, BB/9)
  3. Fetch game log → compute last 3 starts ERA + WHIP
  4. Calculate trend (recent vs season average)
"""

import requests
import time

BASE = "https://statsapi.mlb.com/api/v1"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; mlb-betting-app/1.0)",
    "Accept": "application/json",
}

NEUTRAL_PITCHER = {
    "era":         4.50,
    "whip":        1.30,
    "k9":          8.0,
    "bb9":         3.0,
    "recent_era":  4.50,
    "recent_whip": 1.30,
    "era_trend":   0.0,
    "whip_trend":  0.0,
    "starts":      0,
    "found":       False,
}


# ── Main entry point ───────────────────────────────────────────────────────────

def search_pitcher(name: str, season: int = None) -> dict:
    """
    Look up a pitcher by name via the MLB Stats API.
    Returns season stats + recent form, falls back to neutral if not found.
    """
    if season is None:
        from datetime import datetime
        season = datetime.now().year

    print(f"[PITCHER] Looking up: {name}")

    try:
        player_id = _find_player_id(name)
        if not player_id:
            print(f"[PITCHER] Could not find MLB ID for '{name}' — using neutral stats")
            return {**NEUTRAL_PITCHER, "name": name}

        season_stats = _fetch_season_stats(player_id, season)
        recent_stats = _fetch_recent_form(player_id, season)

        era_trend  = recent_stats["recent_era"]  - season_stats["era"]
        whip_trend = recent_stats["recent_whip"] - season_stats["whip"]

        result = {
            **season_stats,
            **recent_stats,
            "era_trend":  round(era_trend,  3),
            "whip_trend": round(whip_trend, 3),
            "name":  name,
            "found": True,
        }

        print(
            f"[PITCHER] {name}: ERA={result['era']} "
            f"WHIP={result['whip']} "
            f"recent_ERA={result['recent_era']} "
            f"trend={result['era_trend']:+.2f}"
        )
        return result

    except Exception as e:
        print(f"[PITCHER] Error fetching {name}: {e}")
        return {**NEUTRAL_PITCHER, "name": name}


# ── Player search ──────────────────────────────────────────────────────────────

def _find_player_id(name: str) -> int | None:
    """
    Search MLB Stats API for a player by name.
    Returns their MLB player ID (used in all subsequent calls).
    """
    url = f"{BASE}/people/search"
    params = {
        "names":        name,
        "sportId":      1,      # MLB
        "active":       True,
    }

    try:
        resp = _get(url, params)
        people = resp.get("people", [])

        if not people:
            # Try with just last name as fallback
            last = name.strip().split()[-1]
            params["names"] = last
            resp = _get(url, params)
            people = resp.get("people", [])

        if not people:
            return None

        # Filter to pitchers (primaryPosition code "1")
        pitchers = [p for p in people if p.get("primaryPosition", {}).get("code") == "1"]
        candidates = pitchers if pitchers else people

        # Pick best name match
        best = _best_match(name, candidates)
        if best:
            print(f"[PITCHER] Matched '{name}' → '{best['fullName']}' (ID: {best['id']})")
            return best["id"]

        return None

    except Exception as e:
        print(f"[PITCHER] Search error for '{name}': {e}")
        return None


def _best_match(name: str, candidates: list) -> dict | None:
    """Pick the closest name match from a list of player dicts."""
    if not candidates:
        return None

    name_lower = name.lower().strip()
    scored = []
    for p in candidates:
        full = p.get("fullName", "").lower()
        # Score: exact match > last name match > partial
        if full == name_lower:
            score = 1.0
        elif name_lower.split()[-1] in full:
            score = 0.8
        elif any(part in full for part in name_lower.split() if len(part) > 2):
            score = 0.5
        else:
            score = 0.0
        scored.append((score, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_player = scored[0]

    if best_score == 0.0:
        return None
    return best_player


# ── Season stats ───────────────────────────────────────────────────────────────

def _fetch_season_stats(player_id: int, season: int) -> dict:
    """Fetch season pitching stats for a player from the MLB Stats API."""
    url = f"{BASE}/people/{player_id}/stats"
    params = {
        "stats":   "season",
        "group":   "pitching",
        "season":  season,
        "sportId": 1,
    }

    try:
        resp = _get(url, params)
        stats_groups = resp.get("stats", [])

        for group in stats_groups:
            splits = group.get("splits", [])
            if not splits:
                continue
            s = splits[0].get("stat", {})

            era  = _safe_float(s.get("era"))
            whip = _safe_float(s.get("whip"))
            k9   = _safe_float(s.get("strikeoutsPer9Inn"))
            bb9  = _safe_float(s.get("walksPer9Inn"))
            gs   = _safe_int(s.get("gamesStarted", 0))

            return {
                "era":    era  if era  is not None else 4.50,
                "whip":   whip if whip is not None else 1.30,
                "k9":     k9   if k9   is not None else 8.0,
                "bb9":    bb9  if bb9  is not None else 3.0,
                "starts": gs,
            }

    except Exception as e:
        print(f"[PITCHER] Season stats error for ID {player_id}: {e}")

    return {"era": 4.50, "whip": 1.30, "k9": 8.0, "bb9": 3.0, "starts": 0}


# ── Recent form (last 3 starts) ────────────────────────────────────────────────

def _fetch_recent_form(player_id: int, season: int) -> dict:
    """
    Fetch game-by-game pitching log and compute ERA + WHIP over last 3 starts.
    """
    url = f"{BASE}/people/{player_id}/stats"
    params = {
        "stats":   "gameLog",
        "group":   "pitching",
        "season":  season,
        "sportId": 1,
    }

    try:
        resp = _get(url, params)
        stats_groups = resp.get("stats", [])

        all_splits = []
        for group in stats_groups:
            all_splits.extend(group.get("splits", []))

        # Filter to starts only (inningsPitched > 0, game is a start)
        starts = [
            s for s in all_splits
            if _safe_float(s.get("stat", {}).get("inningsPitched", 0) or 0) > 0
            and s.get("stat", {}).get("gamesStarted", 0) == 1
        ]

        # If no starts flagged, just use all appearances with IP > 3
        if not starts:
            starts = [
                s for s in all_splits
                if _safe_float(s.get("stat", {}).get("inningsPitched", 0) or 0) >= 3
            ]

        if not starts:
            return {"recent_era": 4.50, "recent_whip": 1.30}

        # Last 3 starts
        last3 = starts[-3:]

        total_ip = 0.0
        total_er = 0
        total_h  = 0
        total_bb = 0

        for s in last3:
            stat = s.get("stat", {})
            total_ip += _parse_ip(str(stat.get("inningsPitched", "0")))
            total_er += _safe_int(stat.get("earnedRuns", 0))
            total_h  += _safe_int(stat.get("hits", 0))
            total_bb += _safe_int(stat.get("baseOnBalls", 0))

        if total_ip <= 0:
            return {"recent_era": 4.50, "recent_whip": 1.30}

        recent_era  = round(min((total_er * 9) / total_ip, 15.0), 2)
        recent_whip = round(min((total_h + total_bb) / total_ip, 3.0), 2)

        return {"recent_era": recent_era, "recent_whip": recent_whip}

    except Exception as e:
        print(f"[PITCHER] Recent form error for ID {player_id}: {e}")
        return {"recent_era": 4.50, "recent_whip": 1.30}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get(url: str, params: dict = None) -> dict:
    """Make a GET request to the MLB Stats API with light rate limiting."""
    time.sleep(0.2)
    resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _safe_float(val) -> float | None:
    try:
        return float(val) if val not in (None, "", "--") else None
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> int:
    try:
        return int(val) if val not in (None, "", "--") else 0
    except (ValueError, TypeError):
        return 0


def _parse_ip(ip_str: str) -> float:
    """Convert MLB API IP format (e.g. '6.2' = 6⅔ innings) to decimal innings."""
    try:
        ip = float(ip_str)
        whole = int(ip)
        frac  = round(ip - whole, 1)
        return whole + (frac * 10 / 3)
    except Exception:
        return 0.0


def build_pitcher_features(home_pitcher: dict, away_pitcher: dict) -> dict:
    """Convert two pitcher stat dicts into model-ready differential features."""
    return {
        # Season stats — positive = home team advantage
        "sp_era_diff":         away_pitcher["era"]         - home_pitcher["era"],
        "sp_whip_diff":        away_pitcher["whip"]        - home_pitcher["whip"],
        "sp_k9_diff":          home_pitcher["k9"]          - away_pitcher["k9"],
        "sp_bb9_diff":         away_pitcher["bb9"]         - home_pitcher["bb9"],
        # Recent form
        "sp_recent_era_diff":  away_pitcher["recent_era"]  - home_pitcher["recent_era"],
        "sp_recent_whip_diff": away_pitcher["recent_whip"] - home_pitcher["recent_whip"],
        # Trend: negative era_trend = improving, we flip so positive = good for that team
        "home_sp_era_trend":  -home_pitcher["era_trend"],
        "away_sp_era_trend":  -away_pitcher["era_trend"],
    }


# ── Probable pitchers for today ────────────────────────────────────────────────

def get_probable_pitchers_today(date_str: str) -> dict:
    """
    Fetch today's probable starting pitchers from the MLB Stats API.
    Returns dict: normalized_team_name -> pitcher_full_name (or None if not announced).
    date_str format: 'YYYY-MM-DD'
    """
    from ingestion.stats_scraper import _normalize_team

    url = f"{BASE}/schedule"
    params = {
        "sportId": 1,
        "date": date_str,
        "gameType": "R",
        "hydrate": "probablePitcher",
    }

    try:
        resp = _get(url, params)
    except Exception as e:
        print(f"[PROBABLE] Failed to fetch probable pitchers: {e}")
        return {}

    result = {}
    for date_entry in resp.get("dates", []):
        for game in date_entry.get("games", []):
            for side in ("home", "away"):
                team_data = game.get("teams", {}).get(side, {})
                team_name = _normalize_team(team_data.get("team", {}).get("name", ""))
                pitcher = team_data.get("probablePitcher")
                if team_name:
                    result[team_name] = pitcher.get("fullName") if pitcher else None

    found = sum(1 for v in result.values() if v)
    print(f"[PROBABLE] {found}/{len(result)} probable pitchers announced for {date_str}")
    return result


# ── Roster fetching ────────────────────────────────────────────────────────────

# Cache team ID lookups so we don't hit the API repeatedly
_TEAM_ID_CACHE = {}
_ROSTER_CACHE  = {}


def get_team_pitchers(team_name: str, season: int = None) -> list[str]:
    """
    Returns a sorted list of pitcher names on a team's active roster.
    Uses MLB Stats API — no scraping needed.
    """
    if season is None:
        from datetime import datetime
        season = datetime.now().year

    cache_key = f"{team_name}_{season}"
    if cache_key in _ROSTER_CACHE:
        return _ROSTER_CACHE[cache_key]

    try:
        team_id = _get_team_id(team_name)
        if not team_id:
            print(f"[ROSTER] Could not find team ID for '{team_name}'")
            return []

        url = f"{BASE}/teams/{team_id}/roster"
        params = {
            "rosterType": "active",
            "season":     season,
        }
        resp = _get(url, params)
        roster = resp.get("roster", [])

        pitchers = []
        for player in roster:
            pos = player.get("position", {}).get("code", "")
            if pos == "1":  # position code 1 = pitcher
                name = player.get("person", {}).get("fullName", "")
                if name:
                    pitchers.append(name)

        pitchers.sort()
        _ROSTER_CACHE[cache_key] = pitchers
        print(f"[ROSTER] {team_name}: {len(pitchers)} pitchers found")
        return pitchers

    except Exception as e:
        print(f"[ROSTER] Error fetching roster for '{team_name}': {e}")
        return []


def _get_team_id(team_name: str) -> int | None:
    """Look up a team's MLB ID by name."""
    if team_name in _TEAM_ID_CACHE:
        return _TEAM_ID_CACHE[team_name]

    try:
        url = f"{BASE}/teams"
        params = {"sportId": 1, "activeStatus": "Y"}
        resp = _get(url, params)
        teams = resp.get("teams", [])

        name_lower = team_name.lower().strip()
        for team in teams:
            full = team.get("name", "").lower()
            # Handle edge cases like "Athletics" vs "Oakland Athletics"
            if full == name_lower or name_lower in full or full in name_lower:
                tid = team["id"]
                _TEAM_ID_CACHE[team_name] = tid
                return tid

        return None

    except Exception as e:
        print(f"[ROSTER] Team ID lookup failed: {e}")
        return None


# ── Roster fetcher ─────────────────────────────────────────────────────────────

# Cache team ID lookups so we don't re-fetch on every rerun
_TEAM_ID_CACHE = {}
_ROSTER_CACHE  = {}

def get_team_pitchers(team_name: str, season: int = None) -> list[str]:
    """
    Fetch all pitchers currently on an MLB team's active roster.
    Returns a sorted list of pitcher names for use in a dropdown.
    """
    if season is None:
        from datetime import datetime
        season = datetime.now().year

    cache_key = f"{team_name}_{season}"
    if cache_key in _ROSTER_CACHE:
        return _ROSTER_CACHE[cache_key]

    try:
        team_id = _get_team_id(team_name)
        if not team_id:
            return []

        url = f"{BASE}/teams/{team_id}/roster"
        params = {
            "rosterType": "active",
            "season":     season,
        }
        resp = _get(url, params)
        roster = resp.get("roster", [])

        pitchers = []
        for player in roster:
            pos = player.get("position", {}).get("code", "")
            if pos == "1":  # pitcher position code
                name = player.get("person", {}).get("fullName", "")
                if name:
                    pitchers.append(name)

        pitchers = sorted(pitchers)
        _ROSTER_CACHE[cache_key] = pitchers
        print(f"[ROSTER] {team_name}: {len(pitchers)} pitchers found")
        return pitchers

    except Exception as e:
        print(f"[ROSTER] Failed for {team_name}: {e}")
        return []


def _get_team_id(team_name: str) -> int | None:
    """Look up MLB team ID by name."""
    if team_name in _TEAM_ID_CACHE:
        return _TEAM_ID_CACHE[team_name]

    try:
        url = f"{BASE}/teams"
        params = {"sportId": 1, "activeStatus": "Yes"}
        resp = _get(url, params)
        teams = resp.get("teams", [])

        name_lower = team_name.lower()
        for t in teams:
            full = t.get("name", "").lower()
            short = t.get("teamName", "").lower()
            if full == name_lower or short in name_lower or name_lower in full:
                _TEAM_ID_CACHE[team_name] = t["id"]
                return t["id"]

        # Partial fallback
        for t in teams:
            words = t.get("name", "").lower().split()
            if any(w in name_lower for w in words if len(w) > 3):
                _TEAM_ID_CACHE[team_name] = t["id"]
                return t["id"]

    except Exception as e:
        print(f"[ROSTER] Team ID lookup failed for {team_name}: {e}")

    return None


# ── Head-to-head history ───────────────────────────────────────────────────────

def get_head_to_head(home_team: str, away_team: str, n: int = 5) -> list[dict]:
    """
    Fetch last N completed regular-season H2H games via MLB Stats API.
    Queries season-by-season (most recent first) to avoid the MLB API's
    pagination cap that cuts off recent results on wide date-range queries.
    Returns list sorted most-recent first.
    """
    from datetime import datetime

    home_id = _get_team_id(home_team)
    away_id = _get_team_id(away_team)
    if not home_id or not away_id:
        print(f"[H2H] Could not resolve IDs: {home_team}={home_id}, {away_team}={away_id}")
        return []

    current_year = datetime.now().year
    games = []

    for season in [current_year, current_year - 1, current_year - 2]:
        if len(games) >= n:
            break
        try:
            sched = _get(f"{BASE}/schedule", {
                "sportId":    1,
                "teamId":     home_id,
                "opponentId": away_id,
                "season":     season,
                "hydrate":    "linescore",
                "gameType":   "R",
            })
            for date_entry in sched.get("dates", []):
                for game in date_entry.get("games", []):
                    if game.get("status", {}).get("abstractGameState") != "Final":
                        continue
                    ls     = game.get("linescore", {})
                    home_r = ls.get("teams", {}).get("home", {}).get("runs")
                    away_r = ls.get("teams", {}).get("away", {}).get("runs")
                    if home_r is None or away_r is None:
                        continue
                    home_r, away_r = int(home_r), int(away_r)
                    g_home  = game.get("teams", {}).get("home", {}).get("team", {}).get("name", home_team)
                    g_away  = game.get("teams", {}).get("away", {}).get("team", {}).get("name", away_team)
                    g_date  = game.get("officialDate") or game.get("gameDate", "")[:10]
                    winner  = g_home if home_r > away_r else g_away
                    games.append({
                        "date":       g_date,
                        "home_name":  g_home,
                        "away_name":  g_away,
                        "home_score": home_r,
                        "away_score": away_r,
                        "winner":     winner,
                    })
        except Exception as e:
            print(f"[H2H] Error fetching season {season}: {e}")

    games.sort(key=lambda x: x["date"], reverse=True)
    print(f"[H2H] {home_team} vs {away_team}: {len(games)} games found, returning {min(n, len(games))}")
    return games[:n]
