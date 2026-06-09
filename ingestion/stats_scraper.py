"""
ingestion/stats_scraper.py — Fetches MLB team stats from the MLB Stats API.

Replaces Baseball Reference scraping (blocked with 403) with MLB's official
free API at statsapi.mlb.com. No key required.

Fetches:
  - Season standings: W, L, win%, runs scored, runs allowed, run diff
  - Home/away splits
  - Pythagorean win expectancy
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime
import time

BASE = "https://statsapi.mlb.com/api/v1"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; mlb-betting-app/1.0)",
    "Accept": "application/json",
}

# MLB league IDs
LEAGUE_IDS = {
    "AL": 103,
    "NL": 104,
}

# Map MLB API team names → normalized names consistent with odds data
TEAM_NAME_MAP = {
    "Arizona Diamondbacks":  "Arizona Diamondbacks",
    "Atlanta Braves":        "Atlanta Braves",
    "Baltimore Orioles":     "Baltimore Orioles",
    "Boston Red Sox":        "Boston Red Sox",
    "Chicago Cubs":          "Chicago Cubs",
    "Chicago White Sox":     "Chicago White Sox",
    "Cincinnati Reds":       "Cincinnati Reds",
    "Cleveland Guardians":   "Cleveland Guardians",
    "Colorado Rockies":      "Colorado Rockies",
    "Detroit Tigers":        "Detroit Tigers",
    "Houston Astros":        "Houston Astros",
    "Kansas City Royals":    "Kansas City Royals",
    "Los Angeles Angels":    "Los Angeles Angels",
    "Los Angeles Dodgers":   "Los Angeles Dodgers",
    "Miami Marlins":         "Miami Marlins",
    "Milwaukee Brewers":     "Milwaukee Brewers",
    "Minnesota Twins":       "Minnesota Twins",
    "New York Mets":         "New York Mets",
    "New York Yankees":      "New York Yankees",
    "Athletics":             "Oakland Athletics",
    "Oakland Athletics":     "Oakland Athletics",
    "Philadelphia Phillies": "Philadelphia Phillies",
    "Pittsburgh Pirates":    "Pittsburgh Pirates",
    "San Diego Padres":      "San Diego Padres",
    "San Francisco Giants":  "San Francisco Giants",
    "Seattle Mariners":      "Seattle Mariners",
    "St. Louis Cardinals":   "St. Louis Cardinals",
    "Tampa Bay Rays":        "Tampa Bay Rays",
    "Texas Rangers":         "Texas Rangers",
    "Toronto Blue Jays":     "Toronto Blue Jays",
    "Washington Nationals":  "Washington Nationals",
}


# ── Main entry point ───────────────────────────────────────────────────────────

def get_full_team_stats(season: int = None) -> pd.DataFrame:
    """
    Main entry point. Returns merged team stats ready for the model.
    Tries MLB Stats API first, falls back to demo data if unavailable.
    """
    if season is None:
        season = datetime.now().year

    try:
        standings = _fetch_standings(season)
        if standings.empty:
            raise ValueError("Empty standings returned")

        splits = _fetch_home_away_splits(season)
        if not splits.empty:
            df = standings.merge(splits, on="team", how="left")
        else:
            df = standings.copy()
            df["home_win_pct"] = df["win_pct"]
            df["away_win_pct"] = df["win_pct"]

        df["pythag_pct"] = df.apply(
            lambda r: _pythagorean_pct(r["runs_scored"], r["runs_allowed"]), axis=1
        )

        lt = _fetch_last_ten_standings(season)
        if not lt.empty:
            df = df.merge(lt, on="team", how="left")
            df["last_ten_wins"] = df["last_ten_wins"].fillna(5).astype(int)
        else:
            df["last_ten_wins"] = 5

        print(f"[STATS] Loaded stats for {len(df)} teams via MLB Stats API")
        return df.reset_index(drop=True)

    except Exception as e:
        print(f"[STATS] MLB API failed: {e} — using demo stats")
        return _demo_stats(season)


# ── Standings ──────────────────────────────────────────────────────────────────

def _fetch_standings(season: int) -> pd.DataFrame:
    """Fetch W/L/win%/runs from MLB standings API."""
    url = f"{BASE}/standings"
    params = {
        "leagueId": "103,104",   # AL + NL
        "season":   season,
        "standingsTypes": "regularSeason",
        "hydrate": "team,record",
    }

    resp = _get(url, params)
    records = resp.get("records", [])

    rows = []
    for division in records:
        for entry in division.get("teamRecords", []):
            team_name = entry.get("team", {}).get("name", "")
            team_name = _normalize_team(team_name)

            wins   = entry.get("wins", 0)
            losses = entry.get("losses", 0)
            total  = wins + losses
            win_pct = wins / total if total > 0 else 0.5

            # Runs scored/allowed from runsScored / runsAllowed fields
            rs = entry.get("runsScored", 0) or 0
            ra = entry.get("runsAllowed", 0) or 0

            # Fallback: some seasons use leagueRecord structure
            if rs == 0:
                rs = entry.get("runDifferential", 0) + ra

            rows.append({
                "team":         team_name,
                "season":       season,
                "wins":         wins,
                "losses":       losses,
                "win_pct":      round(win_pct, 4),
                "runs_scored":  float(rs),
                "runs_allowed": float(ra),
                "run_diff":     float(rs - ra),
            })

    df = pd.DataFrame(rows)
    df = df[df["team"].notna()].drop_duplicates(subset="team")
    return df


# ── Last-10 from schedule ──────────────────────────────────────────────────────

def _fetch_last_ten_standings(season: int) -> pd.DataFrame:
    """
    Compute each team's wins in their last 10 completed games by fetching
    the recent schedule. More reliable than standingsTypes=lastTen which
    the MLB Stats API does not support for historical or current dates.
    """
    from datetime import timedelta
    end_date   = datetime.now().date()
    start_date = end_date - timedelta(days=22)  # buffer for off-days / doubleheaders

    url = f"{BASE}/schedule"
    params = {
        "sportId":   1,
        "season":    season,
        "gameType":  "R",
        "startDate": start_date.strftime("%Y-%m-%d"),
        "endDate":   end_date.strftime("%Y-%m-%d"),
    }
    try:
        resp = _get(url, params)
    except Exception as e:
        print(f"[STATS] Last-10 schedule fetch failed: {e}")
        return pd.DataFrame()

    team_results: dict = {}
    for date_entry in resp.get("dates", []):
        game_date = date_entry["date"]
        for game in date_entry.get("games", []):
            if game.get("status", {}).get("detailedState") != "Final":
                continue
            home = game["teams"]["home"]
            away = game["teams"]["away"]
            home_score = home.get("score") or 0
            away_score = away.get("score") or 0
            if home_score == away_score:
                continue
            home_name = _normalize_team(home["team"]["name"])
            away_name = _normalize_team(away["team"]["name"])
            home_won  = 1 if home_score > away_score else 0
            team_results.setdefault(home_name, []).append((game_date, home_won))
            team_results.setdefault(away_name, []).append((game_date, 1 - home_won))

    rows = []
    for team, results in team_results.items():
        results.sort()
        wins = sum(w for _, w in results[-10:])
        rows.append({"team": team, "last_ten_wins": wins})

    df = pd.DataFrame(rows)
    return df[df["team"].notna()].drop_duplicates(subset="team") if not df.empty else pd.DataFrame()


# ── Home/Away splits ───────────────────────────────────────────────────────────

def _fetch_home_away_splits(season: int) -> pd.DataFrame:
    """Fetch home and away win% for each team."""
    url = f"{BASE}/standings"
    params = {
        "leagueId": "103,104",
        "season":   season,
        "standingsTypes": "regularSeason",
        "hydrate": "team,record(overallRecords)",
    }

    try:
        resp = _get(url, params)
        records = resp.get("records", [])

        rows = []
        for division in records:
            for entry in division.get("teamRecords", []):
                team_name = _normalize_team(entry.get("team", {}).get("name", ""))

                home_rec = away_rec = None
                for rec in entry.get("records", {}).get("overallRecords", []):
                    rtype = rec.get("type", "")
                    w = rec.get("wins", 0)
                    l = rec.get("losses", 0)
                    total = w + l
                    pct = w / total if total > 0 else 0.5
                    if rtype == "home":
                        home_rec = pct
                    elif rtype == "away":
                        away_rec = pct

                if home_rec is not None and away_rec is not None:
                    rows.append({
                        "team":         team_name,
                        "home_win_pct": round(home_rec, 4),
                        "away_win_pct": round(away_rec, 4),
                    })

        df = pd.DataFrame(rows)
        return df[df["team"].notna()].drop_duplicates(subset="team") if not df.empty else pd.DataFrame()

    except Exception as e:
        print(f"[STATS] Home/away splits failed: {e}")
        return pd.DataFrame()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get(url: str, params: dict = None) -> dict:
    time.sleep(0.2)
    resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _normalize_team(name: str) -> str:
    """Map API team name to standardized name used across the app."""
    if name in TEAM_NAME_MAP:
        return TEAM_NAME_MAP[name]
    # Partial match fallback
    for key, val in TEAM_NAME_MAP.items():
        if key.lower() in name.lower() or name.lower() in key.lower():
            return val
    return name


def _pythagorean_pct(rs: float, ra: float, exp: float = 1.83) -> float:
    """Bill James Pythagorean expectation — better predictor than actual W%."""
    try:
        if rs <= 0 or ra <= 0:
            return 0.5
        return round((rs ** exp) / (rs ** exp + ra ** exp), 4)
    except Exception:
        return 0.5


def _demo_stats(season: int) -> pd.DataFrame:
    """Fallback demo stats when API is unavailable (e.g. pre-season)."""
    teams = list(set(TEAM_NAME_MAP.values()))
    np.random.seed(42)
    n = len(teams)
    wins = np.random.randint(30, 90, n)
    games = 120
    losses = games - wins
    runs_scored = np.random.uniform(3.8, 5.5, n) * games
    runs_allowed = np.random.uniform(3.5, 5.2, n) * games

    df = pd.DataFrame({
        "team":         teams,
        "season":       season,
        "wins":         wins,
        "losses":       losses,
        "win_pct":      (wins / games).round(4),
        "runs_scored":  runs_scored.round(0),
        "runs_allowed": runs_allowed.round(0),
        "run_diff":     (runs_scored - runs_allowed).round(0),
        "home_win_pct": (wins / games + np.random.uniform(-0.05, 0.08, n)).clip(0.2, 0.8).round(4),
        "away_win_pct":   (wins / games + np.random.uniform(-0.08, 0.05, n)).clip(0.2, 0.8).round(4),
        "pythag_pct":     (wins / games + np.random.uniform(-0.03, 0.03, n)).round(4),
        "last_ten_wins":  np.random.randint(3, 8, n),
    })
    print(f"[STATS] Using demo stats for {n} teams")
    return df


# ── Keep these for backward compatibility with Stats Explorer page ─────────────

def fetch_standings(season: int = None) -> pd.DataFrame:
    return get_full_team_stats(season)

def fetch_home_away_splits(season: int = None) -> pd.DataFrame:
    return _fetch_home_away_splits(season or datetime.now().year)

