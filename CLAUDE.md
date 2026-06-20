# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
streamlit run app.py
```

App runs at `http://localhost:8501`. Requires Python 3.10+ (uses `X | Y` union type hints). Copy `.env.example` to `.env` and fill in `DATABASE_URL`, `ODDS_API_KEY`, and `ANTHROPIC_API_KEY` before running locally.

**First launch is multi-user (see `auth.py`):** every page is gated behind a login. The very first run (empty `users` table) shows a one-time "Create admin account" screen — that account becomes the admin and adopts all pre-existing betting data. After a schema change, restart the app so `init_db()` re-runs (the `_DB_INITIALIZED` guard skips it otherwise).

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

`init_db()` is called at the top of every page. It is idempotent — `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ADD COLUMN IF NOT EXISTS` via migration helpers. It also creates the `users` table and the per-user `bankroll` table, and runs `_migrate_user_id_columns()` (adds nullable `user_id` to `bets`, `paper_bets`, `parlays`, `bankroll`) and `_migrate_bet_feature_columns()` (mirrors `paper_bets`' model-feature columns onto `bets` so resolved real bets can train the model). See `auth.py` and `bankroll.py`.

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

`load_and_train()` pulls from `historical_games` (seasons ≥ 2023, rows with complete pitcher data), runs GridSearchCV over C values, calibrates, and saves. The "Fetch 2026 Games & Retrain" button on Model Performance calls this. `load_training_data()` (in `historical_scraper.py`) also appends resolved **logged bets — both `paper_bets` and `bets`, pooled across all users (never user-filtered)** — using their stored feature columns. Real bets only carry features when logged from Today's Games; manually-logged bets have NULL features and are filtered out.

`evaluate_value(model_prob, implied_prob, odds)` returns edge, ¼-Kelly fraction, and signal tier (🔥 ≥8%, ✅ ≥4%, ⚠️ ≥1%, ➖ no edge, ❌ ≤−4%).

## Authentication & multi-user (`auth.py`)

Self-contained auth, same shape as `bankroll.py` (storage + gate + UI in one file). The `users` table stores `username`, a salted one-way `password_hash` (`hashlib.pbkdf2_hmac` — stdlib, never plaintext/reversible), `role` (`admin`|`user`), `email`, a `security_question` + hashed `security_answer_hash` for self-service reset, and `is_active`.

- **`require_login()`** — called right after `init_theme()` on every page. Renders the themed sign-in / register / forgot-password screen (or a one-time "Create admin account" bootstrap when the table is empty) and `st.stop()`s until authenticated. Once logged in it's a pure `session_state` read (no DB cost per rerun, like `require_balance()`), plus the sidebar account card (logout lives here) and role CSS. **10-minute idle timeout** (`IDLE_SECONDS`) checked on each rerun via `_auth_last_active`.
- **First user is the admin** and adopts all pre-existing rows (`create_user` backfills `user_id IS NULL → admin id`, satisfying the "assign existing data to the first user" rule). Registration is open at the app level — access is gated by Streamlit Community Cloud's private-app share list, not an app-level invite code.
- **Per-user scoping:** `selected_user_id()` returns the user id to filter by (regular users → themselves; admin → a sidebar "Viewing data for" picker defaulting to self, or "All users" → `None`). `user_clause(uid, has_where=)` builds the `WHERE/AND user_id = ?` fragment (id bound as a param — injection-safe); `owner_clause()` adds `AND user_id = ?` to UPDATE/DELETE-by-id for regular users so they can't touch another user's row. The admin-only **Model Performance** page is hidden from the nav for members (CSS on the nav href) and hard-stops non-admins.
- `current_user_id()`, `is_admin()`, `logout()`. Run `python auth.py` for the security-path self-check.

## Bankroll & daily budget (`bankroll.py`)

Self-contained money-management feature. The `bankroll` table holds **one row per user** — their starting balance, written once via a startup prompt. **Current balance is derived, never stored:** `initial + realized P&L from that user's resolved real bets` (paper bets excluded). `get_balance_state(user_id)` returns `{initial, current, delta}` in a single query.

`require_balance()` gates the app per logged-in user: called right after `require_login()` on `app.py` and `3_Bet_Tracker.py`, it renders a one-time themed setup prompt and `st.stop()`s until a balance is entered. The `_bankroll_ok` flag is cleared on login so each user is gated on their own balance. It caches `st.session_state["_bankroll_ok"]` after the first read, so a returning user pays no DB cost on later navigations (perf requirement). `streamlit`/`theme` are imported lazily inside the UI functions so `recommend_daily_budget()` stays importable for the self-check.

`recommend_daily_budget(kelly_fractions, balance, risk)` sizes a daily budget from today's value bets: sum each value bet's ¼-Kelly fraction (from `evaluate_value`), scale by the risk level, cap total exposure as a share of bankroll. `RISK_LEVELS` = Conservative (⅛-Kelly / 5% cap) · Moderate (¼-Kelly / 10%) · Aggressive (⅜-Kelly / 20%). On `1_Todays_Games.py` this auto-fills the Real bet slip **Budget** (`st.session_state["budget_real"]`), re-assigned whenever the slate, bankroll, or risk changes; the existing edge-weighted stake pre-fill then splits it across picks. The risk selector lives on the Real bet slip. Real bets only — paper betting is untouched.

`render_balance_card()` is the bankroll hero on Bet Tracker (current balance, green `+`/red `−` vs. initial, like the ROI metric). Run `python bankroll.py` for the money-math self-check.

## Theme system (`theme.py`)

Every page calls `init_theme()` (renders sidebar dark-mode toggle + injects CSS) and uses `palette()` to get the active color dict for Plotly charts and inline HTML. Dark mode state lives in `st.session_state["_dark_mode"]`. CSS classes like `.stat-box`, `.game-block`, `.book-badge`, `.ctx-badge`, `.balance-hero` are defined in `theme.py` and used via `unsafe_allow_html=True` throughout the pages.

## Key cross-cutting patterns

**Pitcher data shared across pages:** `st.session_state["pitcher_data"]` is set on Today's Games and consumed on Bet Sizing without re-entry.

**LLM justifications** (`1_Todays_Games.py`): `_llm_justify()` calls Claude Haiku (max 120 tokens) to explain a value bet. Results are cached in `session_state` by MD5 hash of inputs — no re-call on re-render, but cache is lost on page reload.

**Per-user data scoping:** every page that reads `bets`/`paper_bets`/`parlays` calls `selected_user_id()` and composes `user_clause(...)` into the query; every INSERT carries `user_id = current_user_id()`; every UPDATE/DELETE-by-id appends `owner_clause()`. SQL text only ever interpolates the fixed clause string — user ids are bound as `?` params (1.7, no injection). The model-training query is the deliberate exception: it pools all users.

**Caesars = `williamhill_us`** in The Odds API's bookmaker key system. This is the only book legally available to the user (Puerto Rico).

**Team name normalization:** `_TEAM_NAME_MAP` in `historical_scraper.py` handles MLB Stats API naming quirks (e.g., "Athletics" → "Oakland Athletics"). The odds client and stats scraper use partial-match fuzzy lookup as a fallback.

**No test suite.** No linter configured.
