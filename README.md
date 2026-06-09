# ⚾ MLB Betting Value Finder

A local Streamlit app for finding moneyline value bets on MLB games using Caesars odds and Baseball Reference stats.

---

## What It Does

| Page | What You Can Do |
|---|---|
| **Today's Games** | See every MLB game with Caesars moneylines, the market's implied probability, and the model's predicted win probability side by side. Value bets (edge ≥ 4%) are flagged with Kelly sizing. |
| **Stats Explorer** | Browse season-level team stats: win%, Pythagorean expectation, run differential, home/away splits. See which teams are over/underperforming their expected record. |
| **Bet Tracker** | Log bets manually, record outcomes, and track your running ROI and Closing Line Value (CLV) over time. |

---

## Setup

### 1. Install dependencies

```bash
cd mlb_betting
pip install -r requirements.txt
```

### 2. Get a free Odds API key

Sign up at https://the-odds-api.com — free tier gives 500 requests/month.

### 3. Configure your API key

```bash
cp .env.example .env
# Edit .env and replace your_api_key_here with your actual key
```

### 4. Run the app

```bash
streamlit run app.py
```

The app will open at http://localhost:8501

> **No API key?** The app runs in demo mode with hardcoded example games so you can explore all features before going live.

---

## Project Structure

```
mlb_betting/
├── app.py                    # Home page + entry point
├── database.py               # SQLite setup
├── requirements.txt
├── .env.example              # Copy to .env and add your API key
├── data/
│   └── mlb_betting.db        # Auto-created local database
├── ingestion/
│   ├── odds_client.py        # The Odds API (Caesars moneylines)
│   └── stats_scraper.py      # Baseball Reference stats
├── models/
│   └── predictor.py          # Logistic regression + value calculation
└── pages/
    ├── 1_Todays_Games.py
    ├── 2_Stats_Explorer.py
    └── 3_Bet_Tracker.py
```

---

## How the Model Works

**Features used:**
- Win% differential (home vs away, using H/A splits)
- Pythagorean win% differential (run-based expected record)
- Run differential
- Runs scored / allowed differential
- Home field advantage (~3.5% historical MLB edge)

**Value calculation:**
- Convert American odds → implied probability (vig removed)
- Compare model probability vs. market probability
- Edge ≥ 4% → flagged as value bet
- Kelly Criterion (¼ Kelly) → suggested stake as % of bankroll

**Closing Line Value (CLV):**
The most important metric. If you consistently bet at better odds than the closing line, you have a demonstrable edge regardless of short-term variance.

---

## Roadmap (Next Steps)

- [ ] Add pitcher-level features (ERA, WHIP, recent form)
- [ ] Pull game-level historical data for model training on real outcomes
- [ ] Add run line and totals markets
- [ ] Weather and park factors
- [ ] Automated daily odds refresh (cron job)
- [ ] Multi-sportsbook line comparison

---

## Disclaimer

This tool is for personal, educational use only. It is not financial advice.
Sports betting involves significant risk. Gamble responsibly.
