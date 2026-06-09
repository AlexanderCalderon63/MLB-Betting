"""
models/predictor.py — Logistic Regression model for MLB moneyline value betting.
Now includes pitcher-level features when starters are provided.
"""

import os
import numpy as np
import pandas as pd
import joblib
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.model_selection import GridSearchCV
from sklearn.calibration import CalibratedClassifierCV
import warnings

warnings.filterwarnings("ignore")

MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "mlb_model.pkl")

VALUE_THRESHOLD = 0.04  # 4 percentage points edge minimum

# Base team features (season-level stats)
TEAM_FEATURES = [
    "win_pct_diff", "pythag_diff", "run_diff_diff",
    "rs_diff", "ra_diff", "home_advantage",
]

# Recent form — added when last-10 data is available
FORM_FEATURES = ["recent_form_diff"]

# Pitcher features — season-stat diffs only (matches what historical_games stores)
PITCHER_FEATURES = [
    "sp_era_diff", "sp_whip_diff", "sp_k9_diff", "sp_bb9_diff",
]

ALL_FEATURES = TEAM_FEATURES + PITCHER_FEATURES  # backwards-compat alias (no form)


def build_matchup_features(
    home_team: str,
    away_team: str,
    stats_df: pd.DataFrame,
    is_home_game: bool = True,
    home_pitcher: dict = None,
    away_pitcher: dict = None,
) -> pd.DataFrame | None:
    """
    Build a single-row feature vector for a matchup.
    If pitcher dicts are provided, pitcher features are appended.
    """
    stats_df = stats_df.copy()
    stats_df["team"] = stats_df["team"].str.strip().str.lower()
    home_key = home_team.strip().lower()
    away_key = away_team.strip().lower()

    home = stats_df[stats_df["team"] == home_key]
    away = stats_df[stats_df["team"] == away_key]

    if home.empty or away.empty:
        home = stats_df[stats_df["team"].str.contains(home_key.split()[-1], na=False)]
        away = stats_df[stats_df["team"].str.contains(away_key.split()[-1], na=False)]

    if home.empty or away.empty:
        return None

    h = home.iloc[0]
    a = away.iloc[0]

    h_win_pct = h.get("home_win_pct", h["win_pct"]) if is_home_game else h["win_pct"]
    a_win_pct = a.get("away_win_pct", a["win_pct"]) if is_home_game else a["win_pct"]

    home_lt = int(h.get("last_ten_wins", 5) or 5)
    away_lt = int(a.get("last_ten_wins", 5) or 5)

    features = {
        "win_pct_diff":     h_win_pct - a_win_pct,
        "pythag_diff":      h.get("pythag_pct", h["win_pct"]) - a.get("pythag_pct", a["win_pct"]),
        "run_diff_diff":    (h["run_diff"] - a["run_diff"]) / 100,
        "rs_diff":          (h["runs_scored"] - a["runs_scored"]) / 100,
        "ra_diff":          (a["runs_allowed"] - h["runs_allowed"]) / 100,
        "home_advantage":   0.035 if is_home_game else 0.0,
        "recent_form_diff": (home_lt - away_lt) / 10,
    }

    # Append pitcher features if both starters provided
    if home_pitcher and away_pitcher:
        from ingestion.pitcher_scraper import build_pitcher_features
        pitcher_feats = build_pitcher_features(home_pitcher, away_pitcher)
        features.update(pitcher_feats)

    return pd.DataFrame([features])


class MLBPredictor:
    """
    Logistic Regression wrapper for MLB moneyline predictions.
    Automatically uses pitcher features when available.
    Falls back to heuristic when untrained.
    """

    def __init__(self):
        self.model = Pipeline([
            ("scaler", StandardScaler()),
            ("lr", LogisticRegression(C=1.0, max_iter=1000)),
        ])
        self.is_trained = False
        self.trained_with_pitchers = False
        self.trained_with_form = False
        self.trained_feature_cols = None
        self.n_samples = 0
        self.best_C = None
        self.cv_brier = None
        self.cal_method = None
        self._try_load()

    def _try_load(self):
        """Load a previously saved model from disk if one exists."""
        try:
            if os.path.exists(MODEL_PATH):
                saved = joblib.load(MODEL_PATH)
                self.model = saved["model"]
                self.is_trained = saved["is_trained"]
                self.trained_with_pitchers = saved["trained_with_pitchers"]
                self.trained_with_form = saved.get("trained_with_form", False)
                self.trained_feature_cols = saved.get("trained_feature_cols", None)
                self.n_samples = saved.get("n_samples", 0)
                self.best_C = saved.get("best_C", None)
                self.cv_brier = saved.get("cv_brier", None)
                self.cal_method = saved.get("cal_method", None)
                brier_str = f" | Brier={self.cv_brier:.4f}" if self.cv_brier else ""
                print(f"[MODEL] Loaded trained model ({self.n_samples} samples{brier_str})")
        except Exception as e:
            print(f"[MODEL] Could not load saved model: {e}")

    def save(self):
        """Persist the trained model to disk."""
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        joblib.dump({
            "model":                self.model,
            "is_trained":           self.is_trained,
            "trained_with_pitchers": self.trained_with_pitchers,
            "trained_with_form":    self.trained_with_form,
            "trained_feature_cols": self.trained_feature_cols,
            "n_samples":            self.n_samples,
            "best_C":               self.best_C,
            "cv_brier":             self.cv_brier,
            "cal_method":           self.cal_method,
        }, MODEL_PATH)
        print(f"[MODEL] Saved to {MODEL_PATH}")

    def load_and_train(self) -> bool:
        """Load historical data from the DB, train the model, and save to disk."""
        from ingestion.historical_scraper import load_training_data
        X, y = load_training_data()
        if X.empty:
            return False
        self.train(X, y)
        if self.is_trained:
            self.save()
            return True
        return False

    def train(self, X: pd.DataFrame, y: pd.Series):
        n = len(X)
        if n < 20:
            print("[MODEL] Not enough data to train — using heuristic mode")
            return

        has_pitcher_cols = all(c in X.columns for c in PITCHER_FEATURES)
        has_form         = "recent_form_diff" in X.columns and X["recent_form_diff"].notna().any()
        base = TEAM_FEATURES + (FORM_FEATURES if has_form else [])
        cols = base + (PITCHER_FEATURES if has_pitcher_cols else [])

        # Fold count and calibration method scale with dataset size
        cv_folds   = 3 if n < 60 else 5
        cal_method = "isotonic" if n >= 200 else "sigmoid"

        # ── Step 1: Regularization tuning ─────────────────────────────────────
        # GridSearchCV tries each C value using cross-validation and picks the
        # one with the lowest Brier score (best-calibrated probabilities).
        search = GridSearchCV(
            Pipeline([
                ("scaler", StandardScaler()),
                ("lr",     LogisticRegression(max_iter=1000)),
            ]),
            param_grid={"lr__C": [0.001, 0.01, 0.1, 1.0, 10.0, 100.0]},
            cv=cv_folds,
            scoring="neg_brier_score",
            refit=True,
        )
        search.fit(X[cols], y)
        best_C    = search.best_params_["lr__C"]
        cv_brier  = -search.best_score_
        print(f"[MODEL] Tuned C={best_C} | {cv_folds}-fold CV Brier={cv_brier:.4f}")

        # ── Step 2: Calibration ────────────────────────────────────────────────
        # Wraps the best pipeline in a calibrator that corrects the raw
        # probability scale so that "60% predictions" actually win ~60% of the
        # time. Sigmoid is more stable for small samples; isotonic for larger.
        calibrated = CalibratedClassifierCV(
            Pipeline([
                ("scaler", StandardScaler()),
                ("lr",     LogisticRegression(C=best_C, max_iter=1000)),
            ]),
            method=cal_method,
            cv=cv_folds,
        )
        calibrated.fit(X[cols], y)

        self.model                = calibrated
        self.is_trained           = True
        self.trained_with_pitchers = has_pitcher_cols
        self.trained_with_form    = has_form
        self.trained_feature_cols = cols
        self.n_samples            = n
        self.best_C               = best_C
        self.cv_brier             = cv_brier
        self.cal_method           = cal_method

        form_str = "form+" if has_form else ""
        print(f"[MODEL] Trained: {n} samples | {form_str}pitchers={has_pitcher_cols} | C={best_C} | cal={cal_method}")

    def predict_proba(self, features: pd.DataFrame) -> float:
        if features is None:
            return 0.5

        has_pitcher = all(c in features.columns for c in PITCHER_FEATURES)

        if self.is_trained:
            if self.trained_feature_cols is not None:
                cols = self.trained_feature_cols
            else:
                # Backwards compat: old saved model without trained_feature_cols
                cols = ALL_FEATURES if self.trained_with_pitchers else TEAM_FEATURES
            for c in cols:
                if c not in features.columns:
                    features[c] = 0.0
            prob = self.model.predict_proba(features[cols])[0][1]
        else:
            prob = self._heuristic_predict(features, has_pitcher)

        return round(float(prob), 4)

    def _heuristic_predict(self, features: pd.DataFrame, has_pitcher: bool) -> float:
        f = features.iloc[0]

        # Base team score
        score = (
            3.5 * f["win_pct_diff"]
            + 3.0 * f["pythag_diff"]
            + 1.5 * f["run_diff_diff"]
            + 1.0 * f["rs_diff"]
            + 1.0 * f["ra_diff"]
            + 2.0 * f["home_advantage"]
            + 1.5 * f.get("recent_form_diff", 0)
        )

        if has_pitcher:
            # Pitcher adjustments — weights based on baseball analytics research
            # ERA diff: each 1.0 ERA advantage ≈ 0.5 score points
            score += 0.5  * f.get("sp_era_diff", 0)
            # WHIP diff: each 0.1 WHIP advantage ≈ 0.3 score points
            score += 3.0  * f.get("sp_whip_diff", 0)
            # K/9 diff: strikeout edge matters but less than ERA
            score += 0.15 * f.get("sp_k9_diff", 0)
            # BB/9 diff: control advantage
            score += 0.25 * f.get("sp_bb9_diff", 0)
            # Recent form: weighted more heavily than season (1.5x)
            score += 0.75 * f.get("sp_recent_era_diff", 0)
            score += 4.5  * f.get("sp_recent_whip_diff", 0)
            # Trend bonuses: improving home pitcher or declining away pitcher = positive
            score += 0.4  * f.get("home_sp_era_trend", 0)
            score -= 0.4  * f.get("away_sp_era_trend", 0)

        return _sigmoid(score + 0.1)


def _sigmoid(x: float) -> float:
    return 1 / (1 + np.exp(-x))


def evaluate_value(model_prob: float, implied_prob: float, odds: int) -> dict:
    edge = model_prob - implied_prob

    if odds > 0:
        decimal_odds = (odds / 100) + 1
    else:
        decimal_odds = (100 / abs(odds)) + 1

    b = decimal_odds - 1
    kelly = (b * model_prob - (1 - model_prob)) / b if b > 0 else 0
    quarter_kelly = max(0, kelly * 0.25)

    has_value = edge >= VALUE_THRESHOLD

    return {
        "edge": round(edge, 4),
        "kelly_fraction": round(quarter_kelly, 4),
        "has_value": has_value,
        "recommendation": _recommendation(edge, model_prob, implied_prob),
    }


def _recommendation(edge: float, model_prob: float, implied_prob: float) -> str:
    if edge >= 0.08:
        return "🔥 Strong Value"
    elif edge >= VALUE_THRESHOLD:
        return "✅ Value Bet"
    elif edge >= 0.01:
        return "⚠️ Slight Edge"
    elif edge <= -VALUE_THRESHOLD:
        return "❌ Avoid (Overpriced)"
    else:
        return "➖ No Edge"
