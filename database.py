"""
database.py — SQLite setup for local storage of odds, stats, and bets
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "mlb_betting.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_connection()
    c = conn.cursor()

    # Odds snapshots pulled from The Odds API
    c.execute("""
        CREATE TABLE IF NOT EXISTS odds_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            commence_time TEXT,
            home_team TEXT,
            away_team TEXT,
            home_ml INTEGER,
            away_ml INTEGER,
            home_implied_prob REAL,
            away_implied_prob REAL,
            pulled_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Team stats pulled from Baseball Reference
    c.execute("""
        CREATE TABLE IF NOT EXISTS team_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team TEXT NOT NULL,
            season INTEGER,
            wins INTEGER,
            losses INTEGER,
            win_pct REAL,
            runs_scored REAL,
            runs_allowed REAL,
            run_diff REAL,
            home_win_pct REAL,
            away_win_pct REAL,
            last_10_wins INTEGER,
            pulled_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Manual bet tracker
    c.execute("""
        CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_date TEXT,
            home_team TEXT,
            away_team TEXT,
            bet_on TEXT,
            odds INTEGER,
            stake REAL,
            model_prob REAL,
            implied_prob REAL,
            outcome TEXT,
            profit_loss REAL,
            closing_odds INTEGER,
            closing_implied_prob REAL,
            clv REAL,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Paper bet tracker — separate from real bets, feeds model training
    c.execute("""
        CREATE TABLE IF NOT EXISTS paper_bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_date TEXT,
            home_team TEXT,
            away_team TEXT,
            bet_on TEXT,
            odds INTEGER,
            stake REAL,
            model_prob REAL,
            implied_prob REAL,
            outcome TEXT,
            profit_loss REAL,
            closing_odds INTEGER,
            closing_implied_prob REAL,
            clv REAL,
            notes TEXT,
            win_pct_diff REAL,
            pythag_diff REAL,
            run_diff_diff REAL,
            rs_diff REAL,
            ra_diff REAL,
            home_advantage REAL,
            sp_era_diff REAL,
            sp_whip_diff REAL,
            sp_k9_diff REAL,
            sp_bb9_diff REAL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Historical game results + pre-game feature vectors for model training
    c.execute("""
        CREATE TABLE IF NOT EXISTS historical_games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_date TEXT NOT NULL,
            season INTEGER,
            home_team TEXT,
            away_team TEXT,
            home_score INTEGER,
            away_score INTEGER,
            home_win INTEGER NOT NULL,
            win_pct_diff REAL,
            pythag_diff REAL,
            run_diff_diff REAL,
            rs_diff REAL,
            ra_diff REAL,
            home_advantage REAL,
            pulled_at TEXT DEFAULT (datetime('now')),
            UNIQUE(game_date, home_team, away_team)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS closing_odds_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_date TEXT NOT NULL,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            commence_time TEXT,
            home_closing_odds INTEGER,
            away_closing_odds INTEGER,
            bookmaker TEXT,
            fetched_at TEXT DEFAULT (datetime('now')),
            UNIQUE(game_date, home_team, away_team)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS parlays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_date TEXT NOT NULL,
            sportsbook TEXT,
            stake REAL NOT NULL,
            legs_count INTEGER,
            parlay_odds INTEGER,
            potential_payout REAL,
            outcome TEXT,
            profit_loss REAL,
            notes TEXT,
            pulled_at TEXT DEFAULT (datetime('now'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS parlay_legs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parlay_id INTEGER NOT NULL,
            game_date TEXT NOT NULL,
            home_team TEXT,
            away_team TEXT,
            bet_on TEXT NOT NULL,
            odds INTEGER NOT NULL,
            result TEXT
        )
    """)

    _migrate_pitcher_columns(conn)
    _migrate_last_ten_columns(conn)

    conn.commit()
    conn.close()
    print(f"[DB] Initialized at {DB_PATH}")


def _migrate_last_ten_columns(conn):
    existing = {row[1] for row in conn.execute("PRAGMA table_info(historical_games)")}
    for col_def in ["home_last_ten_wins INTEGER", "away_last_ten_wins INTEGER"]:
        col_name = col_def.split()[0]
        if col_name not in existing:
            conn.execute(f"ALTER TABLE historical_games ADD COLUMN {col_def}")
    conn.commit()


def _migrate_pitcher_columns(conn):
    """Add pitcher stat columns to historical_games if they don't already exist."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(historical_games)")}
    new_cols = [
        "home_sp_era REAL", "home_sp_whip REAL", "home_sp_k9 REAL", "home_sp_bb9 REAL",
        "away_sp_era REAL", "away_sp_whip REAL", "away_sp_k9 REAL", "away_sp_bb9 REAL",
    ]
    for col_def in new_cols:
        col_name = col_def.split()[0]
        if col_name not in existing:
            conn.execute(f"ALTER TABLE historical_games ADD COLUMN {col_def}")
    conn.commit()


if __name__ == "__main__":
    init_db()
