# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
streamlit run app.py
```

App runs at `http://localhost:8501`. Requires Python 3.10+ (uses `X | Y` union type hints). Copy `.env.example` to `.env` and fill in `DATABASE_URL`, `ODDS_API_KEY`, and `ANTHROPIC_API_KEY` before running locally.

## Architecture overview

Streamlit multipage app for finding value bets on MLB moneylines. The core loop: fetch live odds → build feature vectors from team/pitcher stats → compare model win probability to market implied probability → flag games where the gap (edge) exceeds 4%.

**Entry point:** `app.py` — home dashboard showing last-30-day summary stats and today's value bets.  
**Pages:** numbered `pages/1_` through `pages/9_`, loaded automatically by Streamlit.  
**Database:** Supabase PostgreSQL, accessed via `database.py`. `.env` (local) or Streamlit secrets (deployed) provides `DATABASE_URL`.  
**Deployed:** Streamlit Community Cloud, connected to the `main` branch of the GitHub repo.

## Database layer (`database.py`)

All page code calls `get_connection()` which returns a `_PgConn` wrapper. This wrapper translates SQLite-style `?` placeholders to psycopg2 `%s` automatically, so all SQL in pages uses `?`. The connection uses a `RealDictCursor`, meaning **all rows are dicts** — access columns by name (`row["column_name"]`), never by integer index (`row[0]`).

`conn.execute()` uses `RealDictCursor` (dict rows) and auto-appends `RETURNING id` to INSERTs.  
`conn.cursor()` returns a plain cursor for `pd.read_sql()` — use this path for DataFrame queries.

`init_db()` is called at the top of every page. It is idempotent — `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ADD COLUMN IF NOT EXISTS` via migration helpers.

## Ingestion layer (`ingestion/`)

| File | Purpose |
|---|---|
| `odds_client.py` | Fetches live moneylines from The Odds API (`h2h` market). Deduplicates by `home+away+commence_time[:13]`. Caesars sorted first. Converts American odds to vig-removed implied probability. |
| `stats_scraper.py` | MLB Stats API `/standings` — returns win%, Pythagorean win% (exponent 1.83), runs scored/allowed, home/away splits. |
| `pitcher_scraper.py` | MLB Stats API — `search_pitcher()` fetches season ERA/WHIP/K9/BB9 + last-3-starts stats + trend. `get_team_pitchers()` returns active roster pitcher names, cached 1 hour. |
| `historical_scraper.py` | Builds the `historical_games` training table. Fetches past seasons from MLB Stats API using standings-as-of-previous-day to prevent data leakage. For seasons ≥ 2023 also fetches starting pitchers via boxscore. `build_and_store_season(season, start_date=)` supports incremental fetch. |
| `auto_resolver.py` | Resolves pending bets: fetches final scores from MLB Stats API, fetches closing odds from The Odds API (Caesars/BetMGM preferred), computes CLV. Uses `closing_odds_cache` table to avoid redundant API calls. |
| `park_weather.py` | Park factor badges and weather context shown on Today's Games. |

## Model layer (`models/predictor.py`)

`MLBPredictor` wraps a `Pipeline(StandardScaler → LogisticRegression)` with calibration via `CalibratedClassifierCV`. The trained model is saved to `data/mlb_model.pkl` and loaded on instantiation.

**Features** (all expressed as home-minus-away differentials):
- Team: `win_pct_diff`, `pythag_diff`, `run_diff_diff`, `rs_diff`, `ra_diff`, `home_advantage` (fixed 0.035)
- Pitcher (when available): `sp_era_diff`, `sp_whip_diff`, `sp_k9_diff`, `sp_bb9_diff`

`load_and_train()` pulls from `historical_games` (seasons ≥ 2023, rows with complete pitcher data), runs GridSearchCV over C values, calibrates, and saves. The "Fetch 2026 Games & Retrain" button on Model Performance calls this.

`evaluate_value(model_prob, implied_prob, odds)` returns edge, ¼-Kelly fraction, and signal tier (🔥 ≥8%, ✅ ≥4%, ⚠️ ≥1%, ➖ no edge, ❌ ≤−4%).

## Theme system (`theme.py`)

Every page calls `init_theme()` (renders sidebar dark-mode toggle + injects CSS) and uses `palette()` to get the active color dict for Plotly charts and inline HTML. Dark mode state lives in `st.session_state["_dark_mode"]`. CSS classes like `.stat-box`, `.game-block`, `.book-badge`, `.ctx-badge` are defined in `theme.py` and used via `unsafe_allow_html=True` throughout the pages.

## Key cross-cutting patterns

**Pitcher data shared across pages:** `st.session_state["pitcher_data"]` is set on Today's Games and consumed on Bet Sizing without re-entry.

**LLM justifications** (`1_Todays_Games.py`): `_llm_justify()` calls Claude Haiku (max 120 tokens) to explain a value bet. Results are cached in `session_state` by MD5 hash of inputs — no re-call on re-render, but cache is lost on page reload.

**Caesars = `williamhill_us`** in The Odds API's bookmaker key system. This is the only book legally available to the user (Puerto Rico).

**Team name normalization:** `_TEAM_NAME_MAP` in `historical_scraper.py` handles MLB Stats API naming quirks (e.g., "Athletics" → "Oakland Athletics"). The odds client and stats scraper use partial-match fuzzy lookup as a fallback.

**No test suite.** No linter configured.
