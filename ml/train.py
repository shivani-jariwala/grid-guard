"""Train P10 / P50 / P90 demand models on 2019-2021 only. Owner: P1 (P1-07).

Two-stage design so the forecast can extrapolate above the training-max peak and keep
calibrated bands:

  1. Linear base model (sklearn Ridge) on an explicit design matrix. Trees can't predict
     above the maximum demand they saw in training, so a linear term carries the peak.
  2. Residual quantile models: 3 XGBoost reg:quantileerror models (alpha 0.1/0.5/0.9)
     fit on residual = demand - base_prediction. Final pN = base_prediction + residual_pN.
  3. Conformal calibration (CQR): on the validation split, q = 80th percentile of
     max(p10 - y, y - p90). At predict time p10 -= q, p90 += q, then sort.

Splits, strictly by local date:
  train      2019-01-01 .. 2021-06-30
  validation 2021-07-01 .. 2021-12-31   (early stopping + CQR calibration)
  test       2022-01-01 .. 2022-12-31   (never used for training or tuning)

Artifacts in ml/artifacts/: base_ridge.joblib, model_p10/p50/p90.json, meta.json.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from build_features import FEATURES

DATA_PATH = Path(__file__).resolve().parent / "data" / "features.parquet"
ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"
BASE_PATH = ARTIFACT_DIR / "base_ridge.joblib"

# Local-date split boundaries (inclusive).
TRAIN_START = "2019-01-01"
TRAIN_END = "2021-06-30"
VAL_START = "2021-07-01"
VAL_END = "2021-12-31"
TEST_START = "2022-01-01"
TEST_END = "2022-12-31"

QUANTILES = {"p10": 0.1, "p50": 0.5, "p90": 0.9}
CQR_COVERAGE = 0.80  # target central coverage -> 80th percentile of the CQR score

# Heat buckets for per-bucket conformal calibration, keyed on temp_avg (degF).
#   cool: < 75, warm: 75-90, hot: > 90
CQR_BUCKETS = ("cool", "warm", "hot")
CQR_MIN_BUCKET_ROWS = 50  # below this, fall back to the overall q for that bucket


def heat_bucket(temp_avg: np.ndarray) -> np.ndarray:
    """Map temp_avg (degF) to a heat bucket label array: cool / warm / hot."""
    t = np.asarray(temp_avg, dtype=float)
    out = np.where(t < 75.0, "cool", np.where(t <= 90.0, "warm", "hot"))
    return out.astype(object)

XGB_PARAMS = dict(
    objective="reg:quantileerror",
    tree_method="hist",
    n_estimators=2000,
    learning_rate=0.05,
    max_depth=6,
    early_stopping_rounds=100,
    random_state=42,
)

# One-hot categories for the linear base design matrix.
_HOURS = list(range(24))
_DOWS = list(range(7))
_MONTHS = list(range(1, 13))


def load_splits() -> dict[str, pd.DataFrame]:
    """Load features and split by local date, dropping warm-up rows with nulls."""
    df = pd.read_parquet(DATA_PATH)

    before = len(df)
    df = df.dropna(subset=FEATURES + ["demand"]).reset_index(drop=True)
    print(f"Dropped {before - len(df)} rows with nulls in FEATURES/demand.")

    d = df["date_local"]
    train = df[(d >= TRAIN_START) & (d <= TRAIN_END)]
    val = df[(d >= VAL_START) & (d <= VAL_END)]
    test = df[(d >= TEST_START) & (d <= TEST_END)]
    return {"train": train, "val": val, "test": test}


def build_base_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    """Explicit linear design matrix for the Ridge base model.

    Columns: one-hot hour/day_of_week/month, is_holiday, cdd, cdd^2,
    cdd x (hour in 12-20 peak window), temp_avg_24h, temp_avg_72h,
    demand_lag_48h, demand_lag_168h.
    """
    cols: dict[str, np.ndarray] = {}

    hour = frame["hour"].to_numpy()
    dow = frame["day_of_week"].to_numpy()
    month = frame["month"].to_numpy()

    # One-hot (drop the first level to avoid collinearity with the intercept).
    for h in _HOURS[1:]:
        cols[f"hour_{h}"] = (hour == h).astype(float)
    for d in _DOWS[1:]:
        cols[f"dow_{d}"] = (dow == d).astype(float)
    for m in _MONTHS[1:]:
        cols[f"month_{m}"] = (month == m).astype(float)

    cdd = frame["cdd"].to_numpy()
    peak_window = ((hour >= 12) & (hour <= 20)).astype(float)

    cols["is_holiday"] = frame["is_holiday"].to_numpy(dtype=float)
    cols["cdd"] = cdd
    cols["cdd_sq"] = cdd**2
    cols["cdd_peak"] = cdd * peak_window
    cols["temp_avg_24h"] = frame["temp_avg_24h"].to_numpy(dtype=float)
    cols["temp_avg_72h"] = frame["temp_avg_72h"].to_numpy(dtype=float)
    cols["demand_lag_48h"] = frame["demand_lag_48h"].to_numpy(dtype=float)
    cols["demand_lag_168h"] = frame["demand_lag_168h"].to_numpy(dtype=float)

    return pd.DataFrame(cols, index=frame.index)


def _xy(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    return frame[FEATURES], frame["demand"]


def fit_base(train: pd.DataFrame) -> Pipeline:
    """Fit the Ridge base model (standardized features + Ridge)."""
    X = build_base_matrix(train)
    y = train["demand"].to_numpy()
    base = Pipeline(
        [("scale", StandardScaler()), ("ridge", Ridge(alpha=10.0, random_state=42))]
    )
    base.fit(X, y)
    return base


def base_predict(base: Pipeline, frame: pd.DataFrame) -> np.ndarray:
    return base.predict(build_base_matrix(frame))


def train_residual_models(
    base: Pipeline, train: pd.DataFrame, val: pd.DataFrame
) -> tuple[dict[str, xgb.XGBRegressor], dict[str, int]]:
    """Train quantile models on residual = demand - base_prediction."""
    X_train, y_train = _xy(train)
    X_val, y_val = _xy(val)

    r_train = y_train.to_numpy() - base_predict(base, train)
    r_val = y_val.to_numpy() - base_predict(base, val)

    models: dict[str, xgb.XGBRegressor] = {}
    best_iters: dict[str, int] = {}
    for name, alpha in QUANTILES.items():
        print(f"Training residual {name} (quantile_alpha={alpha}) ...")
        model = xgb.XGBRegressor(quantile_alpha=alpha, **XGB_PARAMS)
        model.fit(X_train, r_train, eval_set=[(X_val, r_val)], verbose=False)
        models[name] = model
        best_iters[name] = int(model.best_iteration)
        print(f"  {name} best_iteration: {best_iters[name]}")
    return models, best_iters


def _raw_quantiles(
    bundle: dict, frame: pd.DataFrame
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """base + residual_pN, before conformal widening. Returns (p10, p50, p90)."""
    base_pred = base_predict(bundle["base"], frame)
    X = frame[FEATURES]
    models = bundle["models"]
    p10 = base_pred + models["p10"].predict(X)
    p50 = base_pred + models["p50"].predict(X)
    p90 = base_pred + models["p90"].predict(X)
    return p10, p50, p90


def _cqr_scores(bundle: dict, frame: pd.DataFrame) -> np.ndarray:
    """CQR nonconformity scores max(p10 - y, y - p90) using the uncalibrated bundle."""
    p10, _, p90 = _raw_quantiles(bundle, frame)
    y = frame["demand"].to_numpy()
    return np.maximum(p10 - y, y - p90)


def compute_cqr_q(bundle: dict, val: pd.DataFrame) -> dict[str, float]:
    """Per-bucket CQR q by temp_avg, plus an overall q.

    Returns {"overall": q, "cool": q, "warm": q, "hot": q}. A bucket with fewer than
    CQR_MIN_BUCKET_ROWS validation rows falls back to the overall q.
    """
    scores = _cqr_scores(bundle, val)
    buckets = heat_bucket(val["temp_avg"].to_numpy())

    overall_q = float(np.quantile(scores, CQR_COVERAGE))
    qs: dict[str, float] = {"overall": overall_q}
    for b in CQR_BUCKETS:
        mask = buckets == b
        n = int(mask.sum())
        if n < CQR_MIN_BUCKET_ROWS:
            qs[b] = overall_q
        else:
            qs[b] = float(np.quantile(scores[mask], CQR_COVERAGE))
    return qs


def predict_quantiles(bundle: dict, frame: pd.DataFrame) -> dict[str, np.ndarray]:
    """Final calibrated quantiles: base + residual, widened by a per-row bucket q, sorted.

    `bundle` holds {"base": Ridge pipeline, "models": {p10,p50,p90}, "q": dict}, where
    q maps "overall"/"cool"/"warm"/"hot" -> MW. Each row is widened by the q of its
    temp_avg heat bucket. evaluate.py and export use this same path, so calibration is
    applied everywhere.
    """
    p10, p50, p90 = _raw_quantiles(bundle, frame)

    q_map = bundle.get("q", 0.0)
    if isinstance(q_map, dict):
        buckets = heat_bucket(frame["temp_avg"].to_numpy())
        overall = q_map.get("overall", 0.0)
        q = np.array([q_map.get(b, overall) for b in buckets], dtype=float)
    else:
        # Backward-compatible scalar q.
        q = float(q_map)

    p10 = p10 - q
    p90 = p90 + q
    stacked = np.column_stack([p10, p50, p90])
    stacked = np.sort(stacked, axis=1)
    return {"p10": stacked[:, 0], "p50": stacked[:, 1], "p90": stacked[:, 2]}


def load_bundle() -> dict:
    """Load base model, residual models and q from artifacts (for evaluate/export)."""
    base = joblib.load(BASE_PATH)
    models: dict[str, xgb.XGBRegressor] = {}
    for name in ("p10", "p50", "p90"):
        m = xgb.XGBRegressor()
        m.load_model(ARTIFACT_DIR / f"model_{name}.json")
        models[name] = m
    meta = json.loads((ARTIFACT_DIR / "meta.json").read_text())
    return {"base": base, "models": models, "q": dict(meta["cqr_q"])}


def mape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Mean absolute percentage error (%)."""
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    return float(np.mean(np.abs((y_true - y_pred) / y_true)) * 100.0)


def main() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    splits = load_splits()
    train, val = splits["train"], splits["val"]

    print(
        f"Split sizes -> train: {len(train)}, val: {len(val)}, "
        f"test(untouched): {len(splits['test'])}"
    )

    start = time.time()
    base = fit_base(train)
    joblib.dump(base, BASE_PATH)
    models, best_iters = train_residual_models(base, train, val)
    training_time_s = round(time.time() - start, 1)

    for name, model in models.items():
        model.save_model(ARTIFACT_DIR / f"model_{name}.json")

    # Per-bucket conformal q from validation (uses the uncalibrated bundle).
    bundle = {"base": base, "models": models, "q": 0.0}
    cqr_q = compute_cqr_q(bundle, val)
    bundle["q"] = cqr_q

    # Validation metrics with the final calibrated path.
    val_pred = predict_quantiles(bundle, val)
    y_val = val["demand"].to_numpy()
    val_mape_p50 = mape(y_val, val_pred["p50"])
    val_inside = (y_val >= val_pred["p10"]) & (y_val <= val_pred["p90"])
    val_coverage = float(np.mean(val_inside) * 100.0)

    # Per-bucket validation coverage and row counts.
    val_buckets = heat_bucket(val["temp_avg"].to_numpy())
    bucket_stats: dict[str, dict] = {}
    for b in CQR_BUCKETS:
        mask = val_buckets == b
        n = int(mask.sum())
        cov = float(np.mean(val_inside[mask]) * 100.0) if n else float("nan")
        bucket_stats[b] = {
            "rows": n,
            "q": round(cqr_q[b], 3),
            "coverage_p10_p90": round(cov, 1) if n else None,
            "used_overall_q": n < CQR_MIN_BUCKET_ROWS,
        }

    meta = {
        "features": FEATURES,
        "model_type": "ridge_base + xgb_quantile_residual + cqr",
        "best_iterations": best_iters,
        "splits": {
            "train": [TRAIN_START, TRAIN_END],
            "validation": [VAL_START, VAL_END],
            "test": [TEST_START, TEST_END],
        },
        "xgb_params": {k: v for k, v in XGB_PARAMS.items()},
        "quantiles": QUANTILES,
        "cqr_q": {k: round(v, 3) for k, v in cqr_q.items()},
        "cqr_coverage_target": CQR_COVERAGE,
        "cqr_buckets": {
            "cool": "temp_avg < 75F",
            "warm": "75F <= temp_avg <= 90F",
            "hot": "temp_avg > 90F",
        },
        "cqr_min_bucket_rows": CQR_MIN_BUCKET_ROWS,
        "cqr_bucket_stats": bucket_stats,
        "training_time_s": training_time_s,
        "val_mape_p50": round(val_mape_p50, 3),
        "val_coverage_p10_p90": round(val_coverage, 1),
    }
    (ARTIFACT_DIR / "meta.json").write_text(json.dumps(meta, indent=2))

    print("\n=== train.py summary ===")
    print(f"Artifacts: {ARTIFACT_DIR}")
    print(f"Training time: {training_time_s}s")
    print("Best iteration per residual model:")
    for name, it in best_iters.items():
        print(f"  {name}: {it}")
    print(f"Validation MAPE (p50): {val_mape_p50:.3f}%")
    print(f"Validation coverage P10-P90 (overall): {val_coverage:.1f}%")
    print(f"CQR q overall (MW): {cqr_q['overall']:.1f}")
    print("Per heat bucket (temp_avg):")
    print(f"  {'bucket':<6}{'range':<22}{'rows':>6}{'q (MW)':>10}{'coverage':>11}  note")
    ranges = {"cool": "< 75F", "warm": "75-90F", "hot": "> 90F"}
    for b in CQR_BUCKETS:
        st = bucket_stats[b]
        cov = f"{st['coverage_p10_p90']:.1f}%" if st["coverage_p10_p90"] is not None else "n/a"
        note = "fallback->overall q" if st["used_overall_q"] else ""
        print(
            f"  {b:<6}{ranges[b]:<22}{st['rows']:>6}{st['q']:>10.1f}{cov:>11}  {note}"
        )


if __name__ == "__main__":
    main()
