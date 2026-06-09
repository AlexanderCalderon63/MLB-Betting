"""
ingestion/odds_client.py — Pulls MLB moneylines from The Odds API (all major US books)
Docs: https://the-odds-api.com/liveapi/guides/v4/
"""

import os
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'), override=True)

API_KEY = os.getenv("ODDS_API_KEY", "")
BASE_URL = "https://api.the-odds-api.com/v4"
SPORT = "baseball_mlb"
REGIONS = "us"
MARKETS = "h2h"  # h2h = moneyline

# All major US sportsbooks — app will show which book each line is from
# Caesars will appear when available; others fill in when it hasn't posted yet
BOOKMAKERS = "caesars,williamhill_us,betmgm"

# Human-readable display names for bookmaker keys
BOOKMAKER_DISPLAY = {
    "caesars":        "Caesars",
    "williamhill_us": "Caesars",
    "betmgm":         "BetMGM",
}


def american_to_implied_prob(odds: int) -> float:
    """Convert American odds to implied probability (removes vig)."""
    if odds > 0:
        return 100 / (odds + 100)
    else:
        return abs(odds) / (abs(odds) + 100)


def remove_vig(home_prob: float, away_prob: float) -> tuple[float, float]:
    """Normalize probabilities to remove the bookmaker's vig (overround)."""
    total = home_prob + away_prob
    return home_prob / total, away_prob / total


def fetch_mlb_odds() -> list[dict]:
    """
    Fetch today's MLB moneylines from all available US books via The Odds API.
    Returns one entry per game per bookmaker so lines can be compared.
    Caesars is sorted first when available.
    """
    if not API_KEY or API_KEY == "your_api_key_here":
        print("[ODDS] No API key found — returning demo data")
        return _demo_odds()

    url = f"{BASE_URL}/sports/{SPORT}/odds"
    params = {
        "apiKey": API_KEY,
        "regions": REGIONS,
        "markets": MARKETS,
        "bookmakers": BOOKMAKERS,
        "oddsFormat": "american",
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        raw = resp.json()
        remaining = resp.headers.get("x-requests-remaining", "?")
        print(f"[ODDS] Fetched {len(raw)} games. Requests remaining: {remaining}")
        return _parse_odds(raw)
    except requests.RequestException as e:
        print(f"[ODDS] API error: {e}")
        return _demo_odds()


def _parse_odds(raw: list) -> list[dict]:
    """
    Parse API response into one record per game per bookmaker.
    Each game will have multiple entries — one per book that has posted lines.
    """
    entries = []
    for game in raw:
        home = game.get("home_team")
        away = game.get("away_team")
        commence = game.get("commence_time", "")
        game_id = game.get("id")

        for bookmaker in game.get("bookmakers", []):
            book_key = bookmaker.get("key", "")
            book_name = BOOKMAKER_DISPLAY.get(book_key, book_key.replace("_", " ").title())

            home_ml = away_ml = None
            for market in bookmaker.get("markets", []):
                if market["key"] == "h2h":
                    for outcome in market["outcomes"]:
                        if outcome["name"] == home:
                            home_ml = outcome["price"]
                        elif outcome["name"] == away:
                            away_ml = outcome["price"]

            if home_ml is None or away_ml is None:
                continue

            raw_home = american_to_implied_prob(home_ml)
            raw_away = american_to_implied_prob(away_ml)
            home_prob, away_prob = remove_vig(raw_home, raw_away)

            entries.append({
                "game_id": f"{game_id}_{book_key}",
                "base_game_id": game_id,
                "commence_time": commence,
                "home_team": home,
                "away_team": away,
                "bookmaker_key": book_key,
                "bookmaker": book_name,
                "home_ml": home_ml,
                "away_ml": away_ml,
                "home_implied_prob": round(home_prob, 4),
                "away_implied_prob": round(away_prob, 4),
            })

    # Sort: Caesars first, then BetMGM
    entries.sort(key=lambda x: (0 if x["bookmaker_key"] in ("caesars", "williamhill_us") else 1, x["bookmaker"]))
    return entries


def _demo_odds() -> list[dict]:
    """Demo data showing multiple books per game."""
    demo_games = [
        ("New York Yankees", "Boston Red Sox", [
            ("caesars", -150, 130),
            ("betmgm", -148, 128),
        ]),
        ("Los Angeles Dodgers", "San Francisco Giants", [
            ("caesars", -180, 155),
            ("betmgm", -178, 152),
        ]),
        ("Houston Astros", "Texas Rangers", [
            ("caesars", -120, 102),
            ("betmgm", -118, 100),
        ]),
        ("Atlanta Braves", "New York Mets", [
            ("caesars", -135, 115),
            ("betmgm", -133, 112),
        ]),
    ]

    entries = []
    for i, (home, away, books) in enumerate(demo_games):
        for book_key, home_ml, away_ml in books:
            raw_home = american_to_implied_prob(home_ml)
            raw_away = american_to_implied_prob(away_ml)
            home_prob, away_prob = remove_vig(raw_home, raw_away)
            entries.append({
                "game_id": f"demo_{i}_{book_key}",
                "base_game_id": f"demo_{i}",
                "commence_time": datetime.now().isoformat(),
                "home_team": home,
                "away_team": away,
                "bookmaker_key": book_key,
                "bookmaker": BOOKMAKER_DISPLAY.get(book_key, book_key),
                "home_ml": home_ml,
                "away_ml": away_ml,
                "home_implied_prob": round(home_prob, 4),
                "away_implied_prob": round(away_prob, 4),
            })

    entries.sort(key=lambda x: (0 if x["bookmaker_key"] == "caesars" else 1, x["bookmaker"]))
    return entries
