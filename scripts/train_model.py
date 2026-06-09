"""
scripts/train_model.py

One-time script to fetch historical MLB game data and train the prediction model.
Run this from the project root before starting the app for the first time.

Usage:
    python scripts/train_model.py                    # fetch 2023 + 2024, then train
    python scripts/train_model.py --seasons 2022 2023 2024
    python scripts/train_model.py --skip-fetch       # train on data already in DB
"""

import argparse
import sys
import os

# Allow imports from project root regardless of where the script is called from
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import init_db
from ingestion.historical_scraper import build_and_store_season, get_historical_summary
from models.predictor import MLBPredictor

DEFAULT_SEASONS = [2023, 2024, 2025, 2026]


def main():
    parser = argparse.ArgumentParser(description="Fetch historical MLB data and train the model")
    parser.add_argument(
        "--seasons", nargs="+", type=int, default=DEFAULT_SEASONS,
        help=f"Seasons to fetch (default: {DEFAULT_SEASONS})",
    )
    parser.add_argument(
        "--skip-fetch", action="store_true",
        help="Skip API fetch and train on whatever is already in the DB",
    )
    args = parser.parse_args()

    print("=" * 50)
    print("  MLB Betting Model — Training Pipeline")
    print("=" * 50)

    init_db()

    if not args.skip_fetch:
        total = 0
        for season in args.seasons:
            print(f"\n--- Fetching {season} season ---")
            n = build_and_store_season(season)
            total += n
        print(f"\nTotal games stored: {total} across {len(args.seasons)} season(s)")
    else:
        print("\n[--skip-fetch] Using data already in DB")

    summary = get_historical_summary()
    if summary:
        print("\nDB contents:")
        for season, count in summary.items():
            print(f"  {season}: {count} games")
        print(f"  Total: {sum(summary.values())} games")
    else:
        print("\nNo data found in DB. Run without --skip-fetch first.")
        sys.exit(1)

    print("\n--- Training model ---")
    predictor = MLBPredictor()
    success = predictor.load_and_train()

    if success:
        print(f"\n[SUCCESS] Model trained on {predictor.n_samples} games and saved.")
        print("Restart the Streamlit app to use the trained model.")
    else:
        print("\n[FAILED] Training failed — not enough data or DB error.")
        sys.exit(1)


if __name__ == "__main__":
    main()
