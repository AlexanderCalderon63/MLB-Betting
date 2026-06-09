"""
scripts/backfill_pitcher_stats.py

One-time script that prepares the historical_games table for pitcher-enhanced
model training:

  1. Deletes all rows with season < 2023 (unused by the pitcher model)
  2. Runs the DB migration to add pitcher stat columns (safe to re-run)
  3. Backfills pitcher data for 2023, 2024, 2025, and 2026 games already in DB
  4. Retrains and saves the model on the updated dataset

Expected runtime: ~40-50 minutes (majority is boxscore + pitcher API calls).
Progress is logged to the console every 100 games per season.

Usage (from project root):
    python scripts/backfill_pitcher_stats.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import init_db, get_connection
from ingestion.historical_scraper import backfill_pitcher_data
from models.predictor import MLBPredictor


BACKFILL_SEASONS = [2023, 2024, 2025, 2026]


def main():
    print("=" * 60)
    print("  MLB Betting — Pitcher Feature Backfill")
    print("=" * 60)

    # Step 1: Schema migration (adds pitcher columns if missing)
    print("\n[STEP 1] Running DB migration...")
    init_db()

    # Step 2: Delete pre-2023 rows
    print("\n[STEP 2] Removing pre-2023 training data...")
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM historical_games WHERE season < 2023")
    count_before = c.fetchone()[0]
    c.execute("DELETE FROM historical_games WHERE season < 2023")
    deleted = c.rowcount
    conn.commit()
    conn.close()
    print(f"[STEP 2] Deleted {deleted:,} rows (of {count_before:,} pre-2023 rows found)")

    # Step 3: Backfill pitcher data
    print(f"\n[STEP 3] Backfilling pitcher data for seasons: {BACKFILL_SEASONS}")
    print("         (boxscore call per game + cached pitcher stats per unique pitcher)")
    print("         Expected time: ~40-50 minutes — grab a coffee.\n")
    backfill_pitcher_data(BACKFILL_SEASONS)

    # Step 4: Verify coverage
    print("\n[STEP 4] Verifying pitcher data coverage...")
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT season,
               COUNT(*) as total,
               SUM(CASE WHEN home_sp_era IS NOT NULL THEN 1 ELSE 0 END) as with_pitchers
        FROM historical_games
        GROUP BY season
        ORDER BY season
        """
    ).fetchall()
    conn.close()

    total_games = 0
    total_with_pitchers = 0
    for row in rows:
        pct = row["with_pitchers"] / row["total"] * 100 if row["total"] > 0 else 0
        print(
            f"  {row['season']}: {row['with_pitchers']:,}/{row['total']:,} games "
            f"have pitcher data ({pct:.1f}%)"
        )
        total_games += row["total"]
        total_with_pitchers += row["with_pitchers"]

    overall_pct = total_with_pitchers / total_games * 100 if total_games > 0 else 0
    print(f"\n  Overall: {total_with_pitchers:,}/{total_games:,} ({overall_pct:.1f}%) games have pitcher data")

    if total_with_pitchers < 500:
        print("\n[ERROR] Too few games with pitcher data to train meaningfully.")
        print("        Check API connectivity and try again.")
        sys.exit(1)

    # Step 5: Retrain model
    print("\n[STEP 5] Retraining model on pitcher-enhanced dataset...")
    predictor = MLBPredictor()
    success = predictor.load_and_train()

    if success:
        mode = "pitcher-enhanced" if predictor.trained_with_pitchers else "team-only"
        print(f"\n[SUCCESS] Model retrained on {predictor.n_samples:,} games ({mode} mode).")
        print("          Restart the Streamlit app to use the updated model.")
    else:
        print("\n[FAILED] Model retraining failed — check DB for data.")
        sys.exit(1)


if __name__ == "__main__":
    main()
