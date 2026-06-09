# MLB Betting Value Finder — Project Context

## Overview

This is a **local-only Python/Streamlit web application** for finding value bets on MLB moneylines. It is not deployed anywhere — it runs on `localhost:8501` via `streamlit run app.py`. The purpose is not simply to predict game winners, but to identify games where the model's estimated win probability meaningfully exceeds the sportsbook's implied probability — i.e., where the market appears to be mispricing the line. That gap is the "edge."

The app was built by a data engineer with a computer engineering background who bets through **Caesars Sportsbook** (the only online book legally available in Puerto Rico). The app supports multiple sportsbooks for comparison but Caesars is always sorted first when available.

---

## Tech Stack

- **Language:** Python 3.10+
- **UI:** Streamlit (multipage app)
- **Database:** SQLite via `sqlite3` (local file at `data/mlb_betting.db`)
- **ML:** scikit-learn `LogisticRegression` inside a `StandardScaler` pipeline
- **Data Sources:**
  - **Odds:** The Odds API (`https://api.the-odds-api.com/v4`) — requires a free API key stored in `.env` as `ODDS_API_KEY`
  - **Team Stats:** MLB Stats API (`https://statsapi.mlb.com/api/v1`) — free, no key required
  - **Pitcher Stats & Rosters:** MLB Stats API — same endpoint, no key required
- **Key Libraries:** `requests`, `pandas`, `numpy`, `scikit-learn`, `plotly`, `beautifulsoup4`, `python-dotenv`

> **Important:** Baseball Reference was the original stats source but is now blocked with HTTP 403 for all automated requests. Everything has been migrated to the MLB Stats API.

---

## Project Structure

```
mlb_betting/
├── app.py                        # Home page + Streamlit entry point
├── database.py                   # SQLite schema init (3 tables)
├── requirements.txt
├── .env                          # ODDS_API_KEY=your_key (not committed)
├── .env.example                  # Template
├── data/
│   └── mlb_betting.db            # Auto-created SQLite database
├── ingestion/
│   ├── __init__.py
│   ├── odds_client.py            # Fetches moneylines from The Odds API
│   ├── stats_scraper.py          # Fetches team standings/stats from MLB Stats API
│   └── pitcher_scraper.py        # Fetches pitcher stats + active rosters from MLB Stats API
├── models/
│   ├── __init__.py
│   └── predictor.py              # Feature builder + LogisticRegression + value calculator
└── pages/
    ├── 1_Todays_Games.py         # Main odds + predictions view
    ├── 2_Stats_Explorer.py       # Team stats charts and deep dives
    ├── 3_Bet_Tracker.py          # Manual bet log + ROI + CLV tracking
    └── 4_Bet_Sizing.py           # Budget allocator + payout breakdown
```

---

## Data Layer

### `ingestion/odds_client.py`
- Fetches MLB moneylines from The Odds API using the `h2h` (moneyline) market
- Pulls from all major US books: Caesars, DraftKings, FanDuel, BetMGM, William Hill, PointsBet
- Returns **one entry per game per bookmaker** so lines can be compared side by side
- Deduplicates raw API results using `home_team + away_team + commence_time[:13]` (hour precision) — this correctly collapses true duplicates while preserving doubleheaders
- Caesars entries are always sorted first in the output
- Converts American odds to implied probability and removes vig using normalization
- Falls back to hardcoded demo data if no API key is present

### `ingestion/stats_scraper.py`
- Fetches current season standings from the MLB Stats API (`/standings` endpoint)
- Returns per-team: W, L, win%, runs scored, runs allowed, run differential, home win%, away win%
- Computes **Pythagorean win expectancy** using the Bill James formula (exponent 1.83) — this is a better predictor of future performance than actual win%
- Falls back to randomly seeded demo stats if the API is unavailable (e.g. pre-season)

### `ingestion/pitcher_scraper.py`
- Two main functions:
  1. `search_pitcher(name, season)` — looks up a pitcher by full name, fetches season ERA/WHIP/K9/BB9, fetches game log to compute last-3-starts ERA and WHIP, calculates trend (recent vs season average). Falls back to league-average neutral stats if not found.
  2. `get_team_pitchers(team_name, season)` — fetches the active roster for a team and returns a sorted list of pitcher names only (position code "1"). Results are cached for 1 hour.
- Both functions use the MLB Stats API. Results are cached in module-level dicts to avoid redundant API calls.

### `database.py`
Three SQLite tables:
- `odds_snapshots` — stores pulled odds for historical reference
- `team_stats` — stores pulled team stats
- `bets` — the manual bet log with outcome, P&L, closing odds, and CLV fields

---

## Model Layer (`models/predictor.py`)

### Feature Engineering
`build_matchup_features()` builds a single-row feature vector for any matchup. All features are expressed as differentials (home minus away, with sign conventions so positive = home team advantage).

**Team-only features (always present):**
- `win_pct_diff` — home/away split win% differential
- `pythag_diff` — Pythagorean win% differential
- `run_diff_diff` — run differential gap (normalized /100)
- `rs_diff` — runs scored differential (normalized /100)
- `ra_diff` — runs allowed differential, flipped sign
- `home_advantage` — fixed 0.035 (3.5% historical MLB home field edge)

**Pitcher features (appended when both starters provided):**
- `sp_era_diff` — season ERA: away minus home (positive = home pitcher better)
- `sp_whip_diff` — season WHIP differential
- `sp_k9_diff` — K/9 differential (home minus away)
- `sp_bb9_diff` — BB/9 differential (away minus home, lower BB = better)
- `sp_recent_era_diff` — last 3 starts ERA differential
- `sp_recent_whip_diff` — last 3 starts WHIP differential
- `home_sp_era_trend` — flipped ERA trend for home pitcher (negative trend = improving = positive feature)
- `away_sp_era_trend` — flipped ERA trend for away pitcher

### `MLBPredictor` class
- Wraps a scikit-learn `Pipeline(StandardScaler → LogisticRegression)`
- **Currently untrained** — runs in heuristic mode because no labeled historical game outcomes have been logged yet
- Heuristic mode: weighted linear combination of features passed through a sigmoid function. Pitcher weights are based on baseball analytics research (WHIP weighted most heavily, recent form weighted 1.5x season stats)
- `train(X, y)` is scaffolded and ready — once 20+ real game outcomes are logged in the bet tracker, it can be called to fit a real model
- Known issue: in heuristic mode, probability outputs can be unrealistically extreme (e.g. 98%+) when pitcher differentials are large. This is a calibration artifact of the heuristic, not the underlying data. The direction of the signal is meaningful; the exact probability is not until the model is trained on real data.

### Value Calculation (`evaluate_value`)
- Edge = model probability − market implied probability (vig-removed)
- Value threshold = 4% minimum edge to flag a bet
- Kelly Criterion: `f* = (b*p - q) / b` where b = decimal odds − 1. Uses **¼ Kelly** for bankroll safety
- Signal tiers: 🔥 Strong Value (≥8%), ✅ Value Bet (≥4%), ⚠️ Slight Edge (≥1%), ➖ No Edge, ❌ Avoid (≤−4%)

---

## Pages

### Page 1 — Today's Games (`1_Todays_Games.py`)
The main operational page.

**Filtering:**
- Only shows games whose `commence_time` is in the future (5-minute grace window for clock drift)
- Only shows games scheduled for today based on the user's **local machine timezone**
- "Today" window runs midnight → 2 AM the following morning to cover late West Coast games
- Games already started or scheduled for tomorrow are hidden automatically
- A caption shows how many games were filtered

**Layout per game:**
- Game header shows: away @ home, date tag (Today / Wed Apr 23), local start time
- If pitchers were fetched: two pitcher cards showing team name, pitcher name, trend indicator (▼ Improving / ▲ Declining / — Stable), season stats, and last 3 starts stats
- One row per bookmaker: book badge (color-coded), away ML, home ML, market implied prob (vig-removed), model prob (color-coded by edge tier), signal + Kelly %

**Starting Pitcher Selection:**
- Collapsible expander per game with two dropdowns (home team pitcher, away team pitcher)
- Dropdowns are populated from the team's live active roster via MLB Stats API
- Rosters are cached for 1 hour
- "Fetch Pitcher Stats & Update Model" button triggers MLB Stats API lookups for selected names
- Pitcher data is stored in `st.session_state["pitcher_data"]` and shared with the Bet Sizing page automatically

**Controls:**
- Refresh button (clears cache)
- Sportsbook filter dropdown (All Books or specific book)
- Toggle to show/hide no-value games

### Page 2 — Stats Explorer (`2_Stats_Explorer.py`)
- Full league standings table with win%, Pythagorean%, run differential
- Scatter plot: actual win% vs Pythagorean win% (teams above diagonal are "lucky", below are "unlucky" — regression likely)
- Home vs Away win% grouped bar chart (top 15 teams)
- Team deep dive: select any team for individual metrics + an auto-generated insight about over/underperformance vs Pythagorean expectation

### Page 3 — Bet Tracker (`3_Bet_Tracker.py`)
- Manual bet log form: game date, home team, away team, "Bet On" dropdown (shows actual team names), odds, stake, model prob, market implied prob, notes
- Pending bets section: update outcome (Win/Loss/Push) + closing odds for each unresolved bet
- Auto-calculates P&L and **Closing Line Value (CLV)**: `closing_implied_prob − bet_implied_prob`. Positive CLV = you got better odds than the closing line = long-term edge indicator
- Performance dashboard: win rate, ROI, total P&L, average CLV
- Running P&L chart (Plotly)
- CLV distribution histogram with average line
- Full bet log table

### Page 4 — Bet Sizing (`4_Bet_Sizing.py`)
Five-step workflow:

1. **Budget input** — daily bankroll in dollars
2. **Game selection** — checkbox list of today's upcoming games, each showing the recommended side, edge %, and which book the line is from
3. **Bet configuration** — for each selected game: radio button to pick side (shows actual team names with Home/Away and edge% in captions, recommended side pre-selected), sportsbook dropdown (Caesars first if available), live summary of chosen odds/probs/signal
4. **Allocation sliders** — one slider per selected bet, defaulting to edge-proportional split, must sum to 100%
5. **Full breakdown** — stake per bet, net profit if win, net loss if loss, model win prob. Day summary: total at risk, best case (all win), worst case (all lose), expected value. "Log All Bets to Tracker" button sends everything to the Bet Tracker in one click, storing actual team names.

Pitcher data from session state is automatically applied to model predictions on this page without re-entry.

---

## Known Issues & Limitations

1. **Model calibration** — The heuristic model produces extreme probabilities (95%+) for mismatched pitcher matchups. This is expected until real outcome data is collected and `predictor.train()` is called. Treat signal direction as meaningful, not the exact probability value.

2. **Pitcher weight imbalance** — WHIP weight (4.5) in the heuristic is aggressive. Once the model is trained on real data this self-corrects. For now, games with large WHIP differentials will show very high model confidence.

3. **Early season variance** — In April, teams have played 15-20 games. Win% and run differential are noisy at this sample size. The model becomes more reliable as the season progresses toward June/July.

4. **No live odds update** — The app caches odds for 5 minutes. Lines can move significantly in the hour before first pitch. Always refresh just before making a bet decision.

5. **Caesars availability** — Caesars does not always post lines as early as DraftKings or FanDuel. If Caesars shows no lines, the filter defaults to showing all available books for comparison.

---

## Running the App

```bash
cd mlb_betting
pip install -r requirements.txt   # first time only
streamlit run app.py
```

App opens at `http://localhost:8501`. Requires Python 3.10+ due to `X | Y` union type hints.

The `.env` file must be in the same directory as `app.py`:
```
ODDS_API_KEY=your_key_here
```

Free API key at: https://the-odds-api.com (500 requests/month on free tier)

---

## Suggested Next Steps (Not Yet Built)

These were discussed but not yet implemented, roughly in priority order:

1. **Historical game results for model training** — Pull past MLB game outcomes with the associated team/pitcher stats at game time, build a labeled dataset, call `predictor.train(X, y)`. This is the most impactful improvement.
2. **Model performance dashboard** — Track accuracy, ROI by signal tier, and probability calibration over logged bets. Answers whether the model's 60% calls actually win 60% of the time.
3. **Line movement tracker** — Snapshot odds at multiple points during the day and surface significant line moves as a signal layer on top of the model.
4. **Automated CLV tracking** — Fetch closing odds automatically at game time and update bet tracker entries without manual input.
5. **Park and weather factors** — Coors Field effect, wind speed/direction at game time.
6. **Injury/lineup feed** — Alert when a key player is scratched after the model ran.
