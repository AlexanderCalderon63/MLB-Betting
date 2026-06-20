"""
database.py — PostgreSQL setup via Supabase.
Connection string is read from DATABASE_URL env var (local: .env, cloud: Streamlit secrets).
"""

import os
import re
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


class _CursorWrapper:
    """
    Wraps a psycopg2 RealDictCursor to provide a sqlite3-compatible interface.
    Converts ? positional and :name named placeholders to psycopg2 %s / %(name)s
    transparently, so all existing page/ingestion code works without changes.
    """

    def __init__(self, cur):
        self._cur = cur
        self.lastrowid = None

    @staticmethod
    def _adapt_sql(sql: str) -> str:
        sql = sql.replace("?", "%s")
        # Convert :name → %(name)s but skip :: (PostgreSQL cast operator)
        sql = re.sub(r"(?<!:):(\w+)", r"%(\1)s", sql)
        return sql

    def execute(self, sql, params=None):
        self._cur.execute(self._adapt_sql(sql), params)
        return self

    def fetchall(self):
        return self._cur.fetchall()

    def fetchone(self):
        return self._cur.fetchone()

    def fetchmany(self, size=None):
        return self._cur.fetchmany(size)

    def close(self):
        self._cur.close()

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description

    def __iter__(self):
        return iter(self._cur)


class _PgConn:
    """
    Thin psycopg2 wrapper that mimics the sqlite3 connection interface
    used throughout the app, so no page code needs to change.
    """

    def __init__(self):
        self._conn = psycopg2.connect(DATABASE_URL)

    def cursor(self):
        """DBAPI2-compliant cursor for pd.read_sql — returns tuple rows so
        pandas can construct DataFrames correctly using cursor.description."""
        raw = self._conn.cursor()
        return _CursorWrapper(raw)

    def execute(self, sql: str, params=None):
        sql = _CursorWrapper._adapt_sql(sql)
        is_insert = sql.strip().upper().startswith("INSERT")

        exec_sql = sql
        if is_insert and "RETURNING" not in sql.upper():
            exec_sql = sql.rstrip().rstrip(";") + " RETURNING id"

        raw = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        raw.execute(exec_sql, params)

        wrapper = _CursorWrapper(raw)
        if is_insert:
            try:
                row = raw.fetchone()
                wrapper.lastrowid = row["id"] if row else None
            except Exception:
                pass
        return wrapper

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def get_connection() -> _PgConn:
    return _PgConn()


# Schema is idempotent and rarely changes, but `init_db()` is called at the top of
# every page — i.e. on every Streamlit rerun (every button click). Running its ~12
# round-trips to Supabase each time added noticeable latency to every interaction.
# This guard makes repeat calls a no-op for the life of the process. Pass force=True
# (or restart the app) after changing the schema.
_DB_INITIALIZED = False


def _col_names(conn: _PgConn, table: str) -> set:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
        (table,),
    ).fetchall()
    return {row["column_name"] for row in rows}


def init_db(force: bool = False):
    global _DB_INITIALIZED
    if _DB_INITIALIZED and not force:
        return

    conn = get_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS odds_snapshots (
            id SERIAL PRIMARY KEY,
            game_id TEXT NOT NULL,
            commence_time TEXT,
            home_team TEXT,
            away_team TEXT,
            home_ml INTEGER,
            away_ml INTEGER,
            home_implied_prob REAL,
            away_implied_prob REAL,
            pulled_at TEXT DEFAULT NOW()::TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS team_stats (
            id SERIAL PRIMARY KEY,
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
            pulled_at TEXT DEFAULT NOW()::TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bets (
            id SERIAL PRIMARY KEY,
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
            created_at TEXT DEFAULT NOW()::TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_bets (
            id SERIAL PRIMARY KEY,
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
            created_at TEXT DEFAULT NOW()::TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS historical_games (
            id SERIAL PRIMARY KEY,
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
            pulled_at TEXT DEFAULT NOW()::TEXT,
            UNIQUE(game_date, home_team, away_team)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS closing_odds_cache (
            id SERIAL PRIMARY KEY,
            game_date TEXT NOT NULL,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            commence_time TEXT,
            home_closing_odds INTEGER,
            away_closing_odds INTEGER,
            bookmaker TEXT,
            fetched_at TEXT DEFAULT NOW()::TEXT,
            UNIQUE(game_date, home_team, away_team)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS parlays (
            id SERIAL PRIMARY KEY,
            created_date TEXT NOT NULL,
            sportsbook TEXT,
            stake REAL NOT NULL,
            legs_count INTEGER,
            parlay_odds INTEGER,
            potential_payout REAL,
            outcome TEXT,
            profit_loss REAL,
            notes TEXT,
            pulled_at TEXT DEFAULT NOW()::TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS parlay_legs (
            id SERIAL PRIMARY KEY,
            parlay_id INTEGER NOT NULL,
            game_date TEXT NOT NULL,
            home_team TEXT,
            away_team TEXT,
            bet_on TEXT NOT NULL,
            odds INTEGER NOT NULL,
            result TEXT
        )
    """)

    # One row per user: their starting bankroll. Current balance is derived
    # (initial + realized real-bet P&L) — see bankroll.py.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bankroll (
            id SERIAL PRIMARY KEY,
            initial_balance REAL NOT NULL,
            created_at TEXT DEFAULT NOW()::TEXT
        )
    """)

    # User accounts. Passwords + security answers are stored as salted one-way
    # hashes (see auth.py) — never plaintext, never reversible. Role gates access:
    # 'admin' sees every user's data + all pages; 'user' sees only their own.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            email TEXT,
            security_question TEXT,
            security_answer_hash TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TEXT DEFAULT NOW()::TEXT
        )
    """)

    _migrate_pitcher_columns(conn)
    _migrate_last_ten_columns(conn)
    _migrate_user_id_columns(conn)
    _migrate_bet_feature_columns(conn)

    conn.commit()
    conn.close()
    _DB_INITIALIZED = True
    print("[DB] Initialized at Supabase")


def _migrate_last_ten_columns(conn: _PgConn):
    existing = _col_names(conn, "historical_games")
    for col_def in ["home_last_ten_wins INTEGER", "away_last_ten_wins INTEGER"]:
        col_name = col_def.split()[0]
        if col_name not in existing:
            conn.execute(f"ALTER TABLE historical_games ADD COLUMN {col_def}")
    conn.commit()


def _migrate_user_id_columns(conn: _PgConn):
    """Add user_id to every table holding per-user data. New column is NULL —
    existing rows are adopted by the first (admin) user at account-creation time
    (auth.create_user), per 1.3.7."""
    for table in ("bets", "paper_bets", "parlays", "bankroll"):
        if "user_id" not in _col_names(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER")
    conn.commit()


def _migrate_bet_feature_columns(conn: _PgConn):
    """Mirror paper_bets' model-feature columns onto bets so resolved REAL bets
    can also train the model (1.6). Populated when logging a real bet from
    Today's Games; manual logs leave them NULL and simply don't feed training."""
    existing = _col_names(conn, "bets")
    feature_cols = [
        "win_pct_diff REAL", "pythag_diff REAL", "run_diff_diff REAL",
        "rs_diff REAL", "ra_diff REAL", "home_advantage REAL",
        "sp_era_diff REAL", "sp_whip_diff REAL", "sp_k9_diff REAL", "sp_bb9_diff REAL",
    ]
    for col_def in feature_cols:
        if col_def.split()[0] not in existing:
            conn.execute(f"ALTER TABLE bets ADD COLUMN {col_def}")
    conn.commit()


def _migrate_pitcher_columns(conn: _PgConn):
    existing = _col_names(conn, "historical_games")
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
