"""
scripts/migrate_to_supabase.py
Copies all data from the local SQLite database to Supabase (PostgreSQL).
Run once from the project root: venv\Scripts\python.exe scripts\migrate_to_supabase.py
"""

import os
import sys
import sqlite3
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

SQLITE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "mlb_betting.db")
DATABASE_URL = os.getenv("DATABASE_URL")

TABLES = [
    "bets",
    "paper_bets",
    "historical_games",
    "closing_odds_cache",
    "parlays",
    "parlay_legs",
    "odds_snapshots",
    "team_stats",
]


def migrate():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not set in .env")
        sys.exit(1)

    print(f"Source : {SQLITE_PATH}")
    print(f"Target : {DATABASE_URL[:40]}...\n")

    src = sqlite3.connect(SQLITE_PATH)
    src.row_factory = sqlite3.Row

    dst = psycopg2.connect(DATABASE_URL)
    dst.autocommit = False

    for table in TABLES:
        _migrate_table(src, dst, table)

    src.close()
    dst.commit()
    dst.close()
    print("\nMigration complete.")


def _migrate_table(src, dst, table: str):
    src_cur = src.cursor()
    src_cur.execute(f"SELECT * FROM {table}")
    rows = src_cur.fetchall()

    if not rows:
        print(f"  {table}: 0 rows — skipped")
        return

    columns = [d[0] for d in src_cur.description]
    # Exclude 'id' so PostgreSQL SERIAL generates its own
    non_id_cols = [c for c in columns if c != "id"]

    col_list   = ", ".join(non_id_cols)
    val_holders = ", ".join(["%s"] * len(non_id_cols))
    sql = f"INSERT INTO {table} ({col_list}) VALUES ({val_holders}) ON CONFLICT DO NOTHING"

    dst_cur = dst.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    inserted = 0
    for row in rows:
        values = tuple(row[c] for c in non_id_cols)
        try:
            dst_cur.execute(sql, values)
            inserted += dst_cur.rowcount
        except Exception as e:
            print(f"  WARNING [{table}] row error: {e}")
            dst.rollback()

    dst.commit()

    # Reset the SERIAL sequence so new inserts don't collide with migrated IDs
    dst_cur.execute(f"""
        SELECT setval(
            pg_get_serial_sequence('{table}', 'id'),
            COALESCE(MAX(id), 1)
        ) FROM {table}
    """)
    dst.commit()

    print(f"  {table}: {inserted}/{len(rows)} rows inserted")


if __name__ == "__main__":
    migrate()
