"""
ingestion/historical_scraper.py

Fetches historical MLB regular season game results and builds pre-game feature
vectors for model training. Uses the MLB Stats API exclusively (free, no key).

Strategy:
  - Pull full game schedule for each requested season (scores, teams, dates, game PKs)
  - For each unique game date, fetch standings AS OF the previous day to avoid
    data leakage (we only know what was true before the game started)
  - For seasons >= 2023, also fetch the starting pitcher for each game via boxscore
    and that pitcher's full-season stats (cached per pitcher+season)
  - Build 6 team-level + 4 pitcher-level feature differentials
  - Store results in the historical_games SQLite table, deduplicated by
    (game_date, home_team, away_team)
"""

import requests
import time
import pandas as pd
from datetime import datetime, timedelta

from database import get_connection

BASE = "https://statsapi.mlb.com/api/v1"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; mlb-betting-app/1.0)",
    "Accept": "application/json",
}

_PYTHAG_EXP = 1.83
MIN_GAMES_PLAYED = 10

_TEAM_NAME_MAP = {
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

# In-memory cache for pitcher season stats: (person_id, season) -> stats dict
_pitcher_stats_cache: dict = {}


# ── Public API ─────────────────────────────────────────────────────────────────

def build_and_store_season(season: int, min_games: int = MIN_GAMES_PLAYED, start_date: str = None) -> int:
    """
    Fetch completed regular season games for `season`, build feature vectors
    (including pitcher features for 2023+), and store in historical_games.
    If start_date (YYYY-MM-DD) is given, only games on or after that date are fetched.
    Returns the number of rows inserted.
    """
    games = _fetch_season_schedule(season, start_date=start_date)
    if not games:
        print(f"[HIST] No completed games found for {season}")
        return 0

    unique_dates = sorted(set(g["date"] for g in games))
    print(f"[HIST] Fetching standings for {len(unique_dates)} unique dates in {season}...")

    standings_cache: dict = {}
    for i, date_str in enumerate(unique_dates):
        standings_cache[date_str] = _fetch_standings_as_of(season, date_str)
        if (i + 1) % 30 == 0:
            print(f"[HIST]   {i + 1}/{len(unique_dates)} dates fetched")

    # For 2023+, identify which games already have pitcher data so we skip re-fetching
    existing_with_pitchers: set = set()
    if season >= 2023:
        conn = get_connection()
        rows = conn.execute(
            "SELECT game_date || '|' || home_team || '|' || away_team "
            "FROM historical_games WHERE season = ? AND home_sp_era IS NOT NULL",
            (season,),
        ).fetchall()
        conn.close()
        existing_with_pitchers = {r[0] for r in rows}

    rows_out = []
    skipped = 0

    for game in games:
        home_stats = standings_cache.get(game["date"], {}).get(game["home_team"])
        away_stats = standings_cache.get(game["date"], {}).get(game["away_team"])

        if not home_stats or not away_stats:
            skipped += 1
            continue
        if home_stats["games_played"] < min_games or away_stats["games_played"] < min_games:
            skipped += 1
            continue

        features = _build_team_features(home_stats, away_stats)

        pitcher_data = {
            "home_sp_era": None, "home_sp_whip": None,
            "home_sp_k9": None,  "home_sp_bb9": None,
            "away_sp_era": None, "away_sp_whip": None,
            "away_sp_k9": None,  "away_sp_bb9": None,
        }

        if season >= 2023 and game.get("game_pk"):
            game_key = f"{game['date']}|{game['home_team']}|{game['away_team']}"
            if game_key not in existing_with_pitchers:
                home_id, away_id = _fetch_game_starters(game["game_pk"])
                if home_id and away_id:
                    home_p = _fetch_pitcher_season_stats(home_id, season)
                    away_p = _fetch_pitcher_season_stats(away_id, season)
                    if home_p and away_p:
                        pitcher_data = {
                            "home_sp_era": home_p["era"], "home_sp_whip": home_p["whip"],
                            "home_sp_k9":  home_p["k9"],  "home_sp_bb9":  home_p["bb9"],
                            "away_sp_era": away_p["era"], "away_sp_whip": away_p["whip"],
                            "away_sp_k9":  away_p["k9"],  "away_sp_bb9":  away_p["bb9"],
                        }

        rows_out.append({**game, **features, **pitcher_data})

    print(f"[HIST] Season {season}: {len(rows_out)} games usable, {skipped} skipped")

    if rows_out:
        _save_to_db(rows_out)

    _compute_and_store_last_ten(season)

    return len(rows_out)


def backfill_pitcher_data(seasons: list) -> None:
    """
    For each season, fetch starting pitcher IDs from boxscores and their season
    stats, then UPDATE existing historical_games rows with pitcher columns.
    Designed for one-time backfill runs; prints detailed progress to console.
    """
    for season in seasons:
        print(f"\n[BACKFILL] === Season {season} ===")

        conn = get_connection()
        db_rows = conn.execute(
            "SELECT id, game_date, home_team, away_team "
            "FROM historical_games WHERE season = ? ORDER BY game_date",
            (season,),
        ).fetchall()
        conn.close()

        if not db_rows:
            print(f"[BACKFILL] No games in DB for {season} — skipping")
            continue

        total = len(db_rows)
        print(f"[BACKFILL] {total:,} games found in DB for {season}")

        print(f"[BACKFILL] Fetching {season} schedule to resolve game IDs...")
        schedule_games = _fetch_season_schedule(season)
        pk_map = {
            (g["date"], g["home_team"], g["away_team"]): g["game_pk"]
            for g in schedule_games
            if g.get("game_pk")
        }
        print(f"[BACKFILL] Schedule returned {len(pk_map):,} game IDs")

        updated = 0
        skipped_no_pk = 0
        skipped_no_pitcher = 0

        conn = get_connection()

        for i, row in enumerate(db_rows):
            key = (row["game_date"], row["home_team"], row["away_team"])
            game_pk = pk_map.get(key)

            if not game_pk:
                skipped_no_pk += 1
                _maybe_log(i, total, updated, skipped_no_pk + skipped_no_pitcher, season)
                continue

            home_id, away_id = _fetch_game_starters(game_pk)
            if not home_id or not away_id:
                skipped_no_pitcher += 1
                _maybe_log(i, total, updated, skipped_no_pk + skipped_no_pitcher, season)
                continue

            home_p = _fetch_pitcher_season_stats(home_id, season)
            away_p = _fetch_pitcher_season_stats(away_id, season)
            if not home_p or not away_p:
                skipped_no_pitcher += 1
                _maybe_log(i, total, updated, skipped_no_pk + skipped_no_pitcher, season)
                continue

            conn.execute(
                """
                UPDATE historical_games
                SET home_sp_era=?, home_sp_whip=?, home_sp_k9=?, home_sp_bb9=?,
                    away_sp_era=?, away_sp_whip=?, away_sp_k9=?, away_sp_bb9=?
                WHERE id=?
                """,
                (
                    home_p["era"], home_p["whip"], home_p["k9"], home_p["bb9"],
                    away_p["era"], away_p["whip"], away_p["k9"], away_p["bb9"],
                    row["id"],
                ),
            )
            updated += 1

            if updated % 200 == 0:
                conn.commit()

            _maybe_log(i, total, updated, skipped_no_pk + skipped_no_pitcher, season)

        conn.commit()
        conn.close()

        total_skipped = skipped_no_pk + skipped_no_pitcher
        print(
            f"[BACKFILL] Season {season} complete — "
            f"{updated:,} updated, {total_skipped:,} skipped "
            f"({skipped_no_pk} no game ID, {skipped_no_pitcher} no pitcher data)"
        )


def backfill_last_ten(seasons: list) -> None:
    """
    Compute last-10-game win counts from existing historical_games data
    and store them in home_last_ten_wins / away_last_ten_wins.
    No API calls needed — derived entirely from game results already in the DB.
    """
    for season in seasons:
        print(f"\n[BACKFILL_L10] === Season {season} ===")
        updated = _compute_and_store_last_ten(season)
        print(f"[BACKFILL_L10] Season {season}: {updated:,} games updated")


def _compute_and_store_last_ten(season: int) -> int:
    """
    For each game in a season, compute each team's wins in their last (up to 10)
    games before that game's date, using results already stored in historical_games.
    Updates home_last_ten_wins and away_last_ten_wins in place.
    Returns number of rows updated.
    """
    conn = get_connection()
    db_rows = conn.execute(
        "SELECT id, game_date, home_team, away_team, home_win "
        "FROM historical_games WHERE season = ? ORDER BY game_date",
        (season,),
    ).fetchall()
    conn.close()

    if not db_rows:
        return 0

    # Build per-team game log: {team: [(date, won), ...]}
    team_log: dict = {}
    for row in db_rows:
        home_won = int(row["home_win"])
        for team, won in [(row["home_team"], home_won), (row["away_team"], 1 - home_won)]:
            team_log.setdefault(team, []).append((row["game_date"], won))
    for team in team_log:
        team_log[team].sort()

    updated = 0
    conn = get_connection()

    for row in db_rows:
        home_lt = _last_ten_wins_before(team_log, row["home_team"], row["game_date"])
        away_lt = _last_ten_wins_before(team_log, row["away_team"], row["game_date"])

        if home_lt is None or away_lt is None:
            continue

        conn.execute(
            "UPDATE historical_games SET home_last_ten_wins=?, away_last_ten_wins=? WHERE id=?",
            (home_lt, away_lt, row["id"]),
        )
        updated += 1
        if updated % 500 == 0:
            conn.commit()

    conn.commit()
    conn.close()
    return updated


def _last_ten_wins_before(team_log: dict, team: str, game_date: str):
    """
    Wins in the last (up to) 10 games before game_date for a team.
    Returns None if fewer than 5 prior games exist (early season, insufficient data).
    """
    prior = [won for date, won in team_log.get(team, []) if date < game_date]
    if len(prior) < 5:
        return None
    return sum(prior[-10:])


def load_training_data(min_season: int = 2023) -> tuple:
    """
    Load historical games from the DB and return (X, y) ready for training.

    Only seasons >= min_season are used for training (default 2023) so that
    older data is available for backtesting without affecting the live model.
    Resolved logged bets with feature data — paper AND real, pooled across ALL
    users (never user-filtered) — are always appended since they're logged in
    real time (1.6).

    If pitcher columns are populated, returns 10 features (6 team + 4 pitcher).
    Falls back to 6 team-only features if pitcher data is absent.
    """
    conn = get_connection()
    df = pd.read_sql_query(
        "SELECT * FROM historical_games WHERE season >= ? ORDER BY game_date",
        conn,
        params=(min_season,),
    )

    # Load resolved logged bets that carry model features — BOTH paper and real,
    # pooled across ALL users (1.6). They train the model in real time alongside
    # the historical_games backfill. Real bets only have features when logged from
    # Today's Games; manually-logged bets leave them NULL and are filtered out.
    logged = pd.DataFrame()
    try:
        frames = [
            pd.read_sql_query(
                f"SELECT * FROM {tbl} WHERE outcome IN ('Win', 'Loss') AND win_pct_diff IS NOT NULL",
                conn,
            )
            for tbl in ("paper_bets", "bets")
        ]
        frames = [f for f in frames if not f.empty]
        if frames:
            logged = pd.concat(frames, ignore_index=True)
    except Exception:
        pass
    conn.close()

    if df.empty:
        print("[HIST] No historical data in DB")
        return pd.DataFrame(), pd.Series(dtype=int)

    BASE_TEAM_COLS = [
        "win_pct_diff", "pythag_diff", "run_diff_diff",
        "rs_diff", "ra_diff", "home_advantage",
    ]

    has_recent_form = (
        "home_last_ten_wins" in df.columns
        and df["home_last_ten_wins"].notna().any()
    )
    if has_recent_form:
        df["recent_form_diff"] = (
            df["home_last_ten_wins"].fillna(5) - df["away_last_ten_wins"].fillna(5)
        ) / 10
        TEAM_COLS = BASE_TEAM_COLS + ["recent_form_diff"]
    else:
        TEAM_COLS = BASE_TEAM_COLS

    PITCHER_RAW  = [
        "home_sp_era", "home_sp_whip", "home_sp_k9", "home_sp_bb9",
        "away_sp_era", "away_sp_whip", "away_sp_k9", "away_sp_bb9",
    ]
    PITCHER_DIFF = ["sp_era_diff", "sp_whip_diff", "sp_k9_diff", "sp_bb9_diff"]

    has_pitcher_schema = all(c in df.columns for c in PITCHER_RAW)
    X = None
    y = None

    if has_pitcher_schema:
        df_p = df.dropna(subset=["home_sp_era", "away_sp_era"]).copy()
        if len(df_p) >= 100:
            df_p["sp_era_diff"]  = df_p["away_sp_era"]  - df_p["home_sp_era"]
            df_p["sp_whip_diff"] = df_p["away_sp_whip"] - df_p["home_sp_whip"]
            df_p["sp_k9_diff"]   = df_p["home_sp_k9"]   - df_p["away_sp_k9"]
            df_p["sp_bb9_diff"]  = df_p["away_sp_bb9"]  - df_p["home_sp_bb9"]

            feature_cols = TEAM_COLS + PITCHER_DIFF
            X = df_p[feature_cols].copy()
            y = df_p["home_win"].astype(int)
            seasons = sorted(df_p["season"].dropna().astype(int).unique())
            print(f"[HIST] Loaded {len(X):,} samples with pitcher features | seasons: {seasons}")

    if X is None:
        # Fall back to team-only features
        X = df[TEAM_COLS].copy()
        y = df["home_win"].astype(int)
        seasons = sorted(df["season"].dropna().astype(int).unique())
        print(f"[HIST] Loaded {len(X):,} samples (team-only fallback) | seasons: {seasons}")

    # Append logged bets (paper + real) — derive home_win from bet_on + outcome,
    # use the stored feature diffs.
    if not logged.empty:
        logged["home_win"] = logged.apply(
            lambda r: (1 if r["outcome"] == "Win" else 0)
                      if r["bet_on"] == r["home_team"]
                      else (0 if r["outcome"] == "Win" else 1),
            axis=1,
        ).astype(int)

        logged_X = logged[BASE_TEAM_COLS].copy()
        if "recent_form_diff" in X.columns:
            logged_X["recent_form_diff"] = 0.0
        for col in PITCHER_DIFF:
            if col in logged.columns:
                logged_X[col] = logged[col].fillna(0.0)
            elif col in X.columns:
                logged_X[col] = 0.0

        logged_X = logged_X.reindex(columns=X.columns, fill_value=0.0)
        logged_y = logged["home_win"]

        X = pd.concat([X, logged_X], ignore_index=True)
        y = pd.concat([y, logged_y], ignore_index=True)
        print(f"[HIST] Appended {len(logged)} logged bets (total training samples: {len(X):,})")

    return X, y


def get_historical_summary() -> dict:
    """Return per-season game count from the historical_games table."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT season, COUNT(*) as games
        FROM historical_games
        GROUP BY season
        ORDER BY season
    """).fetchall()
    conn.close()
    return {row["season"]: row["games"] for row in rows}


# ── Schedule fetch ─────────────────────────────────────────────────────────────

def _fetch_season_schedule(season: int, start_date: str = None) -> list:
    """Return all Final regular season games for a season, including game_pk."""
    url = f"{BASE}/schedule"
    params = {"sportId": 1, "season": season, "gameType": "R"}
    if start_date:
        params["startDate"] = start_date

    try:
        resp = _get(url, params)
    except Exception as e:
        print(f"[HIST] Schedule fetch failed for {season}: {e}")
        return []

    games = []
    for date_entry in resp.get("dates", []):
        date_str = date_entry["date"]
        for game in date_entry.get("games", []):
            if game.get("status", {}).get("detailedState") != "Final":
                continue

            home = game["teams"]["home"]
            away = game["teams"]["away"]
            home_score = home.get("score") or 0
            away_score = away.get("score") or 0

            if home_score == away_score:
                continue

            games.append({
                "date":       date_str,
                "season":     season,
                "game_pk":    game.get("gamePk"),
                "home_team":  _normalize_team(home["team"]["name"]),
                "away_team":  _normalize_team(away["team"]["name"]),
                "home_score": int(home_score),
                "away_score": int(away_score),
                "home_win":   1 if home_score > away_score else 0,
            })

    print(f"[HIST] Season {season}: {len(games)} completed games in schedule")
    return games


# ── Standings as-of ────────────────────────────────────────────────────────────

def _fetch_standings_as_of(season: int, date_str: str) -> dict:
    """
    Fetch team standings as of the day before `date_str` to prevent data leakage.
    Returns dict: normalized_team_name -> stats dict.
    """
    prev_date = (
        datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)
    ).strftime("%Y-%m-%d")

    url = f"{BASE}/standings"
    params = {
        "leagueId": "103,104",
        "season": season,
        "standingsTypes": "regularSeason",
        "hydrate": "team,record(overallRecords)",
        "date": prev_date,
    }

    try:
        resp = _get(url, params)
    except Exception as e:
        print(f"[HIST] Standings fetch failed for {prev_date}: {e}")
        return {}

    standings = {}
    for division in resp.get("records", []):
        for entry in division.get("teamRecords", []):
            team_name = _normalize_team(entry.get("team", {}).get("name", ""))
            if not team_name:
                continue

            wins   = entry.get("wins", 0) or 0
            losses = entry.get("losses", 0) or 0
            total  = wins + losses
            win_pct = wins / total if total > 0 else 0.5

            rs = float(entry.get("runsScored") or 0)
            ra = float(entry.get("runsAllowed") or 0)

            home_win_pct = win_pct
            away_win_pct = win_pct
            for rec in entry.get("records", {}).get("overallRecords", []):
                rtype = rec.get("type", "")
                w = rec.get("wins", 0) or 0
                l = rec.get("losses", 0) or 0
                t = w + l
                pct = w / t if t > 0 else 0.5
                if rtype == "home":
                    home_win_pct = pct
                elif rtype == "away":
                    away_win_pct = pct

            standings[team_name] = {
                "games_played": total,
                "win_pct":      win_pct,
                "home_win_pct": home_win_pct,
                "away_win_pct": away_win_pct,
                "runs_scored":  rs,
                "runs_allowed": ra,
                "run_diff":     rs - ra,
                "pythag_pct":   _pythagorean_pct(rs, ra),
            }

    return standings


# ── Pitcher fetching ───────────────────────────────────────────────────────────

def _fetch_game_starters(game_pk: int):
    """
    Return (home_pitcher_id, away_pitcher_id) from a game's boxscore.
    The first entry in each team's pitchers list is the starting pitcher.
    """
    url = f"{BASE}/game/{game_pk}/boxscore"
    try:
        resp = _get(url)
        home_pitchers = resp.get("teams", {}).get("home", {}).get("pitchers", [])
        away_pitchers = resp.get("teams", {}).get("away", {}).get("pitchers", [])
        return (
            home_pitchers[0] if home_pitchers else None,
            away_pitchers[0] if away_pitchers else None,
        )
    except Exception:
        return None, None


def _fetch_pitcher_season_stats(person_id: int, season: int):
    """
    Fetch a pitcher's season ERA, WHIP, K/9, BB/9 from the MLB Stats API.
    Results are cached in memory for the lifetime of the process.
    Returns a dict or None if the player has no pitching stats for that season.
    """
    key = (person_id, season)
    if key in _pitcher_stats_cache:
        return _pitcher_stats_cache[key]

    url = f"{BASE}/people/{person_id}/stats"
    params = {"stats": "season", "group": "pitching", "season": season, "sportId": 1}

    try:
        resp = _get(url, params)
        for group in resp.get("stats", []):
            splits = group.get("splits", [])
            if not splits:
                continue
            s = splits[0].get("stat", {})

            def sf(val):
                try:
                    return float(val) if val not in (None, "", "--") else None
                except (ValueError, TypeError):
                    return None

            era  = sf(s.get("era"))
            whip = sf(s.get("whip"))
            k9   = sf(s.get("strikeoutsPer9Inn"))
            bb9  = sf(s.get("walksPer9Inn"))

            result = {
                "era":  era  if era  is not None else 4.50,
                "whip": whip if whip is not None else 1.30,
                "k9":   k9   if k9   is not None else 8.0,
                "bb9":  bb9  if bb9  is not None else 3.0,
            }
            _pitcher_stats_cache[key] = result
            return result
    except Exception:
        pass

    _pitcher_stats_cache[key] = None
    return None


# ── Feature builders ───────────────────────────────────────────────────────────

def _build_team_features(home: dict, away: dict) -> dict:
    """Build team-level feature differentials. Positive values = home team advantage."""
    return {
        "win_pct_diff":  home["home_win_pct"] - away["away_win_pct"],
        "pythag_diff":   home["pythag_pct"]   - away["pythag_pct"],
        "run_diff_diff": (home["run_diff"]    - away["run_diff"])    / 100,
        "rs_diff":       (home["runs_scored"] - away["runs_scored"]) / 100,
        "ra_diff":       (away["runs_allowed"] - home["runs_allowed"]) / 100,
        "home_advantage": 0.035,
    }


# ── DB write ───────────────────────────────────────────────────────────────────

def _save_to_db(rows: list) -> None:
    conn = get_connection()
    c = conn.cursor()
    inserted = 0

    for row in rows:
        try:
            c.execute(
                """
                INSERT INTO historical_games (
                    game_date, season, home_team, away_team,
                    home_score, away_score, home_win,
                    win_pct_diff, pythag_diff, run_diff_diff,
                    rs_diff, ra_diff, home_advantage,
                    home_sp_era, home_sp_whip, home_sp_k9, home_sp_bb9,
                    away_sp_era, away_sp_whip, away_sp_k9, away_sp_bb9
                ) VALUES (
                    :date, :season, :home_team, :away_team,
                    :home_score, :away_score, :home_win,
                    :win_pct_diff, :pythag_diff, :run_diff_diff,
                    :rs_diff, :ra_diff, :home_advantage,
                    :home_sp_era, :home_sp_whip, :home_sp_k9, :home_sp_bb9,
                    :away_sp_era, :away_sp_whip, :away_sp_k9, :away_sp_bb9
                )
                ON CONFLICT (game_date, home_team, away_team) DO NOTHING
                """,
                row,
            )
            inserted += c.rowcount
        except Exception as e:
            print(f"[HIST] DB insert error: {e}")

    conn.commit()
    conn.close()
    print(f"[HIST] Inserted {inserted} new rows into historical_games")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _maybe_log(i: int, total: int, updated: int, skipped: int, season: int) -> None:
    """Print progress every 100 games."""
    if (i + 1) % 100 == 0:
        pct = (i + 1) / total * 100
        print(
            f"[BACKFILL] {season}: {i+1:,}/{total:,} ({pct:.1f}%) "
            f"— {updated:,} updated, {skipped:,} skipped"
        )


def _pythagorean_pct(rs: float, ra: float) -> float:
    try:
        if rs <= 0 or ra <= 0:
            return 0.5
        return round((rs ** _PYTHAG_EXP) / (rs ** _PYTHAG_EXP + ra ** _PYTHAG_EXP), 4)
    except Exception:
        return 0.5


def _normalize_team(name: str) -> str:
    if name in _TEAM_NAME_MAP:
        return _TEAM_NAME_MAP[name]
    for key, val in _TEAM_NAME_MAP.items():
        if key.lower() in name.lower() or name.lower() in key.lower():
            return val
    return name


def _get(url: str, params: dict = None) -> dict:
    time.sleep(0.3)
    resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()
