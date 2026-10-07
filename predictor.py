"""PitchIQ predictor: calibrated Home / Draw / Away probabilities with XGBoost.

The one rule of this file: a prediction may only use information that existed
BEFORE kick-off (ratings, recent form, rest days). The final score, shots in
that match, etc. are never inputs.

    python predictor.py train                       # train, evaluate honestly, save model
    python predictor.py train --plot                # also save a calibration chart
    python predictor.py predict "Arsenal" "Chelsea" # probabilities + reasons
    python predictor.py predict Arsenal Chelsea --json   # machine-readable (for the agent)
"""
from __future__ import annotations

import argparse
import difflib
import json
import logging
import math
import os
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from scipy.optimize import minimize_scalar
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
BASE_DIR = Path(os.getenv("PITCHIQ_HOME", Path(__file__).resolve().parent))
DB_PATH = Path(os.getenv("PITCHIQ_DB", BASE_DIR / "pitchiq.db"))
MODEL_DIR = BASE_DIR / "models"
REPORT_DIR = BASE_DIR / "reports"
MODEL_FILE, META_FILE = "pitchiq_xgb.json", "model_meta.json"

CLASSES = ["A", "D", "H"]                       # explicit order: Away=0, Draw=1, Home=2
LABEL = {c: i for i, c in enumerate(CLASSES)}   # (no LabelEncoder surprises)
NAMES = {"A": "away_win", "D": "draw", "H": "home_win"}

FORM_WINDOW = 5      # "recent form" = last 5 matches
FORM_MIN_PERIODS = 3
REST_CAP_DAYS = 21   # a 3-month break is not "more rested" than 3 weeks
WARMUP_SEASONS = 1   # first season only warms up Elo/form; not used for training
FAKE_ID = "__fixture__"

log = logging.getLogger("pitchiq.predictor")


@dataclass(frozen=True)
class EloConfig:
    base: float = 1500.0
    k: float = 20.0                # how fast ratings react to results
    home_advantage: float = 65.0   # Elo points the home side gets
    season_regression: float = 0.25  # each summer, ratings drift 25% back to average


STAT_COLS = ["pts_avg", "gf_avg", "ga_avg", "sot_avg", "sota_avg", "rest_days"]
FEATURES = (
    ["elo_home", "elo_away", "elo_diff", "pts_diff", "gd_diff"]
    + [f"{side}_{c}" for side in ("home", "away") for c in STAT_COLS]
)


# --------------------------------------------------------------------------- #
# 1. LOAD
# --------------------------------------------------------------------------- #
def load_matches(db_path: Path = DB_PATH) -> pd.DataFrame:
    """Read the matches table written by etl.py."""
    if not Path(db_path).exists():
        raise FileNotFoundError(f"{db_path} not found. Run ./run_etl.sh --seasons 10 first.")
    query = """SELECT match_id, season, match_date, home_team, away_team, home_goals, away_goals,
                      result, home_shots_on_target, away_shots_on_target, odds_home, odds_draw, odds_away
               FROM matches ORDER BY match_date, match_id"""
    with closing(sqlite3.connect(db_path)) as conn:   # sqlite3's own `with` does NOT close
        df = pd.read_sql(query, conn)
    if df.empty:
        raise ValueError("The matches table is empty. Run the ETL first.")
    df["match_date"] = pd.to_datetime(df["match_date"])
    # Season label -> 0,1,2,... in chronological order (labels like '9900' sort badly as text)
    order = df.groupby("season")["match_date"].min().sort_values().index
    df["season_idx"] = df["season"].map({s: i for i, s in enumerate(order)})
    return df


# --------------------------------------------------------------------------- #
# 2. FEATURES (every value below uses only matches played BEFORE the row's date)
# --------------------------------------------------------------------------- #
def add_elo(df: pd.DataFrame, cfg: EloConfig = EloConfig()) -> pd.DataFrame:
    """Elo rating: everyone starts equal; beating a stronger team earns more points.

    Each row stores the rating BEFORE the match; only afterwards do we update it.
    Rows with no score yet (upcoming fixtures) are skipped in the update step."""
    ratings: dict[str, float] = defaultdict(lambda: cfg.base)
    elo_home, elo_away, last_season = [], [], None
    for r in df.itertuples():
        if last_season is not None and r.season_idx != last_season:
            for team in list(ratings):    # new season: pull everyone towards the mean
                ratings[team] = cfg.base + (1 - cfg.season_regression) * (ratings[team] - cfg.base)
        last_season = r.season_idx

        rh, ra = ratings[r.home_team], ratings[r.away_team]
        elo_home.append(rh)
        elo_away.append(ra)
        if pd.isna(r.home_goals) or pd.isna(r.away_goals):
            continue                       # not played yet: nothing to learn
        expected_home = 1 / (1 + 10 ** (-(rh + cfg.home_advantage - ra) / 400))
        actual = 1.0 if r.home_goals > r.away_goals else (0.5 if r.home_goals == r.away_goals else 0.0)
        gd = abs(r.home_goals - r.away_goals)
        margin = 1.0 if gd <= 1 else (1.5 if gd == 2 else (11 + gd) / 8)   # big wins count more
        delta = cfg.k * margin * (actual - expected_home)
        ratings[r.home_team] = rh + delta
        ratings[r.away_team] = ra - delta

    out = df.copy()
    out["elo_home"], out["elo_away"] = elo_home, elo_away
    out["elo_diff"] = out["elo_home"] - out["elo_away"]
    return out


def add_form(df: pd.DataFrame) -> pd.DataFrame:
    """Rolling averages over each team's previous matches (home AND away games together).

    `.shift(1)` is the anti-leakage line: it pushes every value down one row so a
    match never sees its own result, only the matches before it."""
    def side(prefix: str, opp: str, sot: str, sota: str) -> pd.DataFrame:
        return pd.DataFrame({
            "match_id": df["match_id"], "side": prefix, "match_date": df["match_date"],
            "team": df[f"{prefix}_team"],
            "gf": df[f"{prefix}_goals"], "ga": df[f"{opp}_goals"],
            "sot": df[sot], "sota": df[sota],
        })
    long = pd.concat([
        side("home", "away", "home_shots_on_target", "away_shots_on_target"),
        side("away", "home", "away_shots_on_target", "home_shots_on_target"),
    ])
    played = long["gf"].notna() & long["ga"].notna()
    long["pts"] = np.where(~played, np.nan, np.where(long["gf"] > long["ga"], 3, np.where(long["gf"] == long["ga"], 1, 0)))
    long = long.sort_values(["team", "match_date", "match_id"])
    grouped = long.groupby("team", sort=False)
    for col, name in [("pts", "pts_avg"), ("gf", "gf_avg"), ("ga", "ga_avg"), ("sot", "sot_avg"), ("sota", "sota_avg")]:
        long[name] = grouped[col].transform(
            lambda s: s.shift(1).rolling(FORM_WINDOW, min_periods=FORM_MIN_PERIODS).mean())
    long["rest_days"] = grouped["match_date"].diff().dt.days.clip(upper=REST_CAP_DAYS)

    out = df.copy()
    for prefix in ("home", "away"):
        part = long[long["side"] == prefix].set_index("match_id")[STAT_COLS].add_prefix(f"{prefix}_")
        out = out.join(part, on="match_id")
    out["pts_diff"] = out["home_pts_avg"] - out["away_pts_avg"]
    out["gd_diff"] = (out["home_gf_avg"] - out["home_ga_avg"]) - (out["away_gf_avg"] - out["away_ga_avg"])
    return out


def build_features(df: pd.DataFrame, elo_cfg: EloConfig = EloConfig()) -> pd.DataFrame:
    df = df.sort_values(["match_date", "match_id"]).reset_index(drop=True)
    return add_form(add_elo(df, elo_cfg))


# --------------------------------------------------------------------------- #
# 3. METRICS (all take y = integer labels 0/1/2 and p = probabilities, shape (n, 3))
# --------------------------------------------------------------------------- #
def log_loss_score(y: np.ndarray, p: np.ndarray) -> float:
    """Punishes confident wrong answers heavily. Lower is better."""
    return float(-np.mean(np.log(np.clip(p[np.arange(len(y)), y], 1e-15, 1))))


def brier_score(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean(np.sum((p - np.eye(3)[y]) ** 2, axis=1)))


def rps_score(y: np.ndarray, p: np.ndarray) -> float:
    """Ranked Probability Score: the football-forecasting standard. Because
    Away < Draw < Home is an ordered scale, predicting 'draw' when the truth is
    'home' is less wrong than predicting 'away'. Lower is better."""
    cum_p = np.cumsum(p, axis=1)[:, :-1]
    cum_y = np.cumsum(np.eye(3)[y], axis=1)[:, :-1]
    return float(np.mean(np.sum((cum_p - cum_y) ** 2, axis=1) / 2))


def score(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    return {"accuracy": float(np.mean(p.argmax(axis=1) == y)), "log_loss": log_loss_score(y, p),
            "brier": brier_score(y, p), "rps": rps_score(y, p)}


def calibration_table(y: np.ndarray, p: np.ndarray, n_bins: int = 10) -> tuple[pd.DataFrame, float]:
    """When the model says 30%, does it happen ~30% of the time?
    Returns the table and ECE (average gap between promise and reality; lower is better)."""
    probs, hits = p.ravel(), np.eye(3)[y].ravel()
    bins = np.minimum((probs * n_bins).astype(int), n_bins - 1)
    rows = [{"predicted": probs[bins == b].mean(), "observed": hits[bins == b].mean(), "count": int((bins == b).sum())}
            for b in range(n_bins) if (bins == b).any()]
    table = pd.DataFrame(rows)
    ece = float(np.sum(table["count"] / table["count"].sum() * (table["predicted"] - table["observed"]).abs()))
    return table, ece


def bookmaker_probs(df: pd.DataFrame) -> np.ndarray:
    """Decimal odds -> probabilities, with the bookmaker's margin removed. Order: A, D, H."""
    inverse = 1 / df[["odds_away", "odds_draw", "odds_home"]].to_numpy(dtype=float)
    return inverse / inverse.sum(axis=1, keepdims=True)


# --------------------------------------------------------------------------- #
# 4. CALIBRATION (temperature scaling)
# --------------------------------------------------------------------------- #
def apply_temperature(p: np.ndarray, t: float) -> np.ndarray:
    """t > 1 softens over-confident predictions, t < 1 sharpens timid ones."""
    logits = np.log(np.clip(p, 1e-12, 1)) / t
    logits -= logits.max(axis=1, keepdims=True)
    e = np.exp(logits)
    return e / e.sum(axis=1, keepdims=True)


def fit_temperature(y: np.ndarray, p: np.ndarray) -> float:
    result = minimize_scalar(lambda t: log_loss_score(y, apply_temperature(p, t)),
                             bounds=(0.5, 3.0), method="bounded")
    return float(result.x)


# --------------------------------------------------------------------------- #
# 5. MODEL
# --------------------------------------------------------------------------- #
def make_model(**overrides) -> xgb.XGBClassifier:
    """Shallow trees + strong regularisation: football is mostly noise, and deep
    trees would memorise it."""
    params = dict(objective="multi:softprob", eval_metric="mlogloss", n_estimators=1000,
                  learning_rate=0.03, max_depth=3, min_child_weight=5, subsample=0.8,
                  colsample_bytree=0.8, reg_lambda=5.0, early_stopping_rounds=40,
                  random_state=42, n_jobs=-1)
    params.update(overrides)
    return xgb.XGBClassifier(**params)


def split_by_season(df: pd.DataFrame, test_seasons: int, val_seasons: int):
    """Train on the past, tune on a later season, judge on the newest seasons.
    Never a random split: that would let the model train on matches from AFTER the ones it is tested on."""
    last = df["season_idx"].max()
    test = df["season_idx"] > last - test_seasons
    val = (df["season_idx"] > last - test_seasons - val_seasons) & ~test
    train = ~(test | val)
    if min(train.sum(), val.sum(), test.sum()) == 0:
        raise ValueError("Not enough seasons to split. Backfill more with ./run_etl.sh --seasons 10")
    return train, val, test


def train(db_path: Path = DB_PATH, model_dir: Path = MODEL_DIR, test_seasons: int = 2,
          val_seasons: int = 1, refit_all: bool = True, plot: bool = False) -> dict:
    elo_cfg = EloConfig()
    feats = build_features(load_matches(db_path), elo_cfg)
    feats = feats[feats["home_goals"].notna() & feats["result"].isin(CLASSES)]
    feats = feats[feats["season_idx"] >= WARMUP_SEASONS].copy()
    y_all = feats["result"].map(LABEL).to_numpy()
    tr, va, te = (m.to_numpy() for m in split_by_season(feats, test_seasons, val_seasons))
    X = feats[FEATURES]
    log.info("Matches: train %d | validation %d | test %d", tr.sum(), va.sum(), te.sum())

    model = make_model()
    model.fit(X[tr], y_all[tr], eval_set=[(X[va], y_all[va])], verbose=False)
    n_trees = int(model.best_iteration) + 1
    temperature = fit_temperature(y_all[va], model.predict_proba(X[va], iteration_range=(0, n_trees)))

    # ---- Honest evaluation on seasons the model has never seen ----
    y_te = y_all[te]
    raw = model.predict_proba(X[te], iteration_range=(0, n_trees))
    calibrated = apply_temperature(raw, temperature)

    prior = np.tile(np.bincount(y_all[tr], minlength=3) / tr.sum(), (te.sum(), 1))
    simple_cols = ["elo_diff", "pts_diff", "gd_diff"]
    logistic = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(max_iter=1000))
    logistic.fit(X.loc[tr, simple_cols], y_all[tr])
    candidates = {
        "Always predict the average": prior,
        "Logistic regression (Elo + form)": logistic.predict_proba(X.loc[te, simple_cols]),
        "XGBoost (raw)": raw,
        "XGBoost (calibrated)": calibrated,
    }
    has_odds = feats.loc[te, ["odds_home", "odds_draw", "odds_away"]].notna().all(axis=1).to_numpy()
    if has_odds.any():
        candidates["Bookmaker (Bet365, margin removed)"] = bookmaker_probs(feats.loc[te].reset_index(drop=True))
    mask = has_odds if has_odds.any() else np.ones(te.sum(), dtype=bool)   # same matches for every row
    results = {name: score(y_te[mask], p[mask]) for name, p in candidates.items()}

    table, ece = calibration_table(y_te, calibrated)
    print_report(results, int(mask.sum()), n_trees, temperature, ece)
    if plot:
        save_calibration_plot(table, ece)

    # ---- Final model: same recipe, but allowed to learn from ALL seasons ----
    final = model
    if refit_all:
        final = make_model(n_estimators=n_trees, early_stopping_rounds=None)
        final.fit(X, y_all, verbose=False)
    model_dir.mkdir(parents=True, exist_ok=True)
    final.save_model(model_dir / MODEL_FILE)
    importance = dict(sorted(zip(FEATURES, map(float, final.feature_importances_)), key=lambda kv: -kv[1]))
    meta = {
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trained_through": str(feats["match_date"].max().date()),
        "n_matches": int(len(feats)), "features": FEATURES, "classes": CLASSES,
        "n_trees": n_trees, "temperature": temperature, "elo": asdict(elo_cfg),
        "test_seasons": test_seasons, "test_matches_scored": int(mask.sum()),
        "metrics_on_test": results, "calibration_ece": ece, "feature_importance": importance,
        "refit_on_all_data": refit_all,
    }
    (model_dir / META_FILE).write_text(json.dumps(meta, indent=2))
    log.info("Saved model to %s", model_dir / MODEL_FILE)
    return meta


def print_report(results: dict, n: int, n_trees: int, temperature: float, ece: float) -> None:
    print(f"\n=== Honest evaluation on {n} unseen matches (lower is better, except accuracy) ===")
    print(f"{'Model':<36}{'Accuracy':>9}{'LogLoss':>9}{'Brier':>8}{'RPS':>8}")
    for name, m in results.items():
        print(f"{name:<36}{m['accuracy']:>9.1%}{m['log_loss']:>9.4f}{m['brier']:>8.4f}{m['rps']:>8.4f}")
    print(f"\nTrees used: {n_trees} | calibration temperature: {temperature:.2f} | calibration error (ECE): {ece:.3f}")
    print("Note: the bookmaker row is the bar to beat. Matching it is a good result; football is very random.\n")


def save_calibration_plot(table: pd.DataFrame, ece: float) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.plot([0, 1], [0, 1], "--", color="gray", label="perfect calibration")
    ax.plot(table["predicted"], table["observed"], "o-", color="#2d2e92", label="PitchIQ")
    ax.set(xlabel="Predicted probability", ylabel="How often it actually happened",
           title=f"Calibration (ECE {ece:.3f})", xlim=(0, 1), ylim=(0, 1))
    ax.legend()
    fig.tight_layout()
    fig.savefig(REPORT_DIR / "calibration.png", dpi=150)
    plt.close(fig)
    log.info("Saved %s", REPORT_DIR / "calibration.png")


# --------------------------------------------------------------------------- #
# 6. PREDICT
# --------------------------------------------------------------------------- #
def load_model(model_dir: Path = MODEL_DIR) -> tuple[xgb.XGBClassifier, dict]:
    if not (model_dir / MODEL_FILE).exists():
        raise FileNotFoundError("No trained model found. Run: python predictor.py train")
    model = xgb.XGBClassifier()
    model.load_model(model_dir / MODEL_FILE)
    return model, json.loads((model_dir / META_FILE).read_text())


def resolve_team(name: str, known: set[str]) -> str:
    """Accept 'arsenal' or 'Man Utd'; suggest the closest real name if unsure."""
    lookup = {t.lower(): t for t in known}
    if name.lower() in lookup:
        return lookup[name.lower()]
    close = difflib.get_close_matches(name.lower(), list(lookup), n=3, cutoff=0.5)
    hint = f" Did you mean: {', '.join(lookup[c] for c in close)}?" if close else ""
    raise ValueError(f"Unknown team '{name}'.{hint}")


def pretty(feature: str) -> str:
    stat = {"elo": "Elo rating", "pts_avg": "recent points per game", "gf_avg": "recent goals scored",
            "ga_avg": "recent goals conceded", "sot_avg": "recent shots on target",
            "sota_avg": "recent shots on target faced", "rest_days": "days of rest",
            "elo_diff": "rating gap (home minus away)", "pts_diff": "form gap in points",
            "gd_diff": "goal-difference form gap"}
    if feature in stat:
        return stat[feature]
    if feature in ("elo_home", "elo_away"):
        return f"{feature.split('_')[1]} team's Elo rating"
    side, _, rest = feature.partition("_")
    return f"{side} team's {stat.get(rest, rest)}"


def predict_match(home: str, away: str, match_date: str | None = None, db_path: Path = DB_PATH,
                  model_dir: Path = MODEL_DIR) -> dict:
    """Probabilities for one fixture, built by the SAME feature code used in training
    (so training and serving can never drift apart)."""
    model, meta = load_model(model_dir)
    history = load_matches(db_path)
    teams = set(history["home_team"]) | set(history["away_team"])
    home, away = resolve_team(home, teams), resolve_team(away, teams)
    if home == away:
        raise ValueError("A team cannot play itself.")

    last = history["match_date"].max()
    when = pd.Timestamp(match_date) if match_date else max(pd.Timestamp.today().normalize(), last + pd.Timedelta(days=1))
    fixture = {c: np.nan for c in history.columns}
    fixture.update(match_id=FAKE_ID, match_date=when, home_team=home, away_team=away,
                   season=history["season"].iloc[-1], season_idx=history["season_idx"].max())
    combined = pd.concat([history, pd.DataFrame([fixture])], ignore_index=True)
    row = build_features(combined, EloConfig(**meta["elo"])).query("match_id == @FAKE_ID")
    X = row[meta["features"]]

    n_trees = meta["n_trees"]
    probs = apply_temperature(model.predict_proba(X, iteration_range=(0, n_trees)), meta["temperature"])[0]
    favourite = CLASSES[int(probs.argmax())]

    contribs = model.get_booster().predict(xgb.DMatrix(X, feature_names=meta["features"]),
                                           pred_contribs=True, iteration_range=(0, n_trees))[0, int(probs.argmax()), :-1]
    why = [{"factor": pretty(meta["features"][i]), "value": None if pd.isna(X.iloc[0, i]) else round(float(X.iloc[0, i]), 2),
            "pushes": "towards" if contribs[i] > 0 else "away from", "strength": round(float(abs(contribs[i])), 3)}
           for i in np.argsort(-np.abs(contribs))[:3]]

    ctx = row.iloc[0]
    warnings = []
    gap_days = (when - last).days
    if gap_days > 14:
        warnings.append(f"Latest result in the database is {gap_days} days old. "
                        "If the season is running, refresh the data with ./run_etl.sh.")
    return {
        "home": home, "away": away, "match_date": str(when.date()),
        "probabilities": {NAMES[c]: round(float(p), 4) for c, p in zip(CLASSES, probs)},
        "most_likely": NAMES[favourite],
        "context": {"elo_home": round(float(ctx["elo_home"])), "elo_away": round(float(ctx["elo_away"])),
                    "home_points_per_game_last5": None if pd.isna(ctx["home_pts_avg"]) else round(float(ctx["home_pts_avg"]), 2),
                    "away_points_per_game_last5": None if pd.isna(ctx["away_pts_avg"]) else round(float(ctx["away_pts_avg"]), 2),
                    "home_rest_days": None if pd.isna(ctx["home_rest_days"]) else int(ctx["home_rest_days"]),
                    "away_rest_days": None if pd.isna(ctx["away_rest_days"]) else int(ctx["away_rest_days"])},
        f"why_{NAMES[favourite]}": why,
        "warnings": warnings,
        "model": {"trained_through": meta["trained_through"], "test_log_loss": round(meta["metrics_on_test"]["XGBoost (calibrated)"]["log_loss"], 4),
                  "note": "Calibrated probabilities from team ratings and recent form only. "
                          "It does not know about injuries or lineups."},
    }


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PitchIQ match predictor")
    sub = parser.add_subparsers(dest="command", required=True)
    t = sub.add_parser("train", help="train, evaluate and save the model")
    t.add_argument("--test-seasons", type=int, default=2)
    t.add_argument("--val-seasons", type=int, default=1)
    t.add_argument("--no-refit", action="store_true", help="save the train-only model instead of refitting on all data")
    t.add_argument("--plot", action="store_true", help="save reports/calibration.png")
    p = sub.add_parser("predict", help="predict one fixture")
    p.add_argument("home")
    p.add_argument("away")
    p.add_argument("--date", help="YYYY-MM-DD (default: next day after the latest match)")
    p.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

    try:
        if args.command == "train":
            train(test_seasons=args.test_seasons, val_seasons=args.val_seasons,
                  refit_all=not args.no_refit, plot=args.plot)
        else:
            out = predict_match(args.home, args.away, args.date)
            if args.json:
                print(json.dumps(out, indent=2))
            else:
                pr = out["probabilities"]
                print(f"\n{out['home']} vs {out['away']} ({out['match_date']})")
                print(f"  Home win {pr['home_win']:.0%} | Draw {pr['draw']:.0%} | Away win {pr['away_win']:.0%}")
                print(f"  Ratings: {out['context']['elo_home']} vs {out['context']['elo_away']}")
                for item in out[f"why_{out['most_likely']}"]:
                    print(f"  - {item['factor']} = {item['value']} ({item['pushes']} {out['most_likely']})")
                for w in out["warnings"]:
                    print(f"  WARNING: {w}")
                print(f"  {out['model']['note']}\n")
    except (FileNotFoundError, ValueError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())