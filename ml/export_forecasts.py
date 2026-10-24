"""Export forecasts/{scenario}.json (contract C1) for upload to S3. Owner: P1 (P1-09, P1-15).

Builds C1 forecast files from the trained model. For a given local date we rebuild the
24 feature rows with build_features(temp_offset_f=...), run predict_quantiles (base Ridge
+ XGB quantile residuals + bucketed CQR), and assemble a C1 document that validates with
backend.shared.models.Forecast.from_dict.

Files written to forecasts/:
  sept6_2022.json          date 2022-09-06, temp_offset_f 0, real actual/caiso_forecast.
  sept6_2022_plus5f.json   same day, temp_offset_f 5, actual and caiso_forecast null.
  normal_summer_day.json   the July 2022 weekday (non-holiday) whose daily peak demand is
                           closest to the median July 2022 weekday peak.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from build_features import build_features
from train import load_bundle, predict_quantiles

# Repo root on sys.path so `backend.shared.models` imports.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.shared.models import Forecast  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"
EIA_PATH = DATA_DIR / "eia_raw.parquet"
WEATHER_PATH = DATA_DIR / "weather.parquet"
OUT_DIR = REPO_ROOT / "forecasts"

MODEL_VERSION = "xgb-quantile-ridge-cqr-v1"
TIMEZONE = "America/Los_Angeles"


def _frame_for_date(
    eia_df: pd.DataFrame, weather_df: pd.DataFrame, date: str, temp_offset_f: float
) -> pd.DataFrame:
    """The 24 feature rows for one local date, with the temperature offset applied."""
    feats = build_features(eia_df, weather_df, temp_offset_f=temp_offset_f)
    day = feats[feats["date_local"] == pd.Timestamp(date)].sort_values("hour")
    if len(day) != 24:
        raise ValueError(f"{date}: expected 24 hourly rows, got {len(day)}")
    return day.reset_index(drop=True)


def build_c1(
    eia_df: pd.DataFrame,
    weather_df: pd.DataFrame,
    *,
    forecast_id: str,
    date: str,
    temp_offset_f: float,
    include_truth: bool,
    bundle: dict,
) -> dict:
    """Assemble one C1 forecast document."""
    day = _frame_for_date(eia_df, weather_df, date, temp_offset_f)
    preds = predict_quantiles(bundle, day)

    # predict_quantiles already sorts per row, so p10 <= p50 <= p90 holds.
    p10 = np.rint(preds["p10"]).astype(int)
    p50 = np.rint(preds["p50"]).astype(int)
    p90 = np.rint(preds["p90"]).astype(int)

    hours = []
    for i in range(24):
        actual = round(float(day.loc[i, "demand"]), 0) if include_truth else None
        caiso = round(float(day.loc[i, "caiso_df"]), 0) if include_truth else None
        hours.append(
            {
                "hour": int(day.loc[i, "hour"]),
                "p10": int(p10[i]),
                "p50": int(p50[i]),
                "p90": int(p90[i]),
                "actual": None if actual is None else int(actual),
                "caiso_forecast": None if caiso is None else int(caiso),
                "temp_avg": round(float(day.loc[i, "temp_avg"]), 1),
            }
        )

    return {
        "forecast_id": forecast_id,
        "date": date,
        "timezone": TIMEZONE,
        "temp_offset_f": temp_offset_f,
        "model_version": MODEL_VERSION,
        "hours": hours,
    }


def pick_normal_summer_day(eia_df: pd.DataFrame, weather_df: pd.DataFrame) -> str:
    """July 2022 weekday (non-holiday) with daily peak demand closest to the median peak."""
    feats = build_features(eia_df, weather_df, temp_offset_f=0.0)
    july = feats[
        (feats["date_local"].dt.year == 2022)
        & (feats["date_local"].dt.month == 7)
        & (feats["is_weekend"] == 0)
        & (feats["is_holiday"] == 0)
    ]
    # Only keep full 24-hour days.
    peaks = july.groupby("date_local").agg(hours=("demand", "size"), peak=("demand", "max"))
    peaks = peaks[peaks["hours"] == 24]
    median_peak = peaks["peak"].median()
    chosen = (peaks["peak"] - median_peak).abs().idxmin()
    return pd.Timestamp(chosen).strftime("%Y-%m-%d")


def write_and_validate(doc: dict, filename: str) -> Forecast:
    """Write the C1 doc and validate it with Forecast.from_dict."""
    path = OUT_DIR / filename
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    forecast = Forecast.from_dict(json.loads(path.read_text(encoding="utf-8")))
    return forecast


def _report(name: str, forecast: Forecast) -> None:
    peak = max(forecast.hours, key=lambda fh: fh.p50)
    print(
        f"  {name}: peak p50 {int(peak.p50)} MW, p90 {int(peak.p90)} MW "
        f"at hour {peak.hour}"
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    eia_df = pd.read_parquet(EIA_PATH)
    weather_df = pd.read_parquet(WEATHER_PATH)
    bundle = load_bundle()

    normal_date = pick_normal_summer_day(eia_df, weather_df)
    print(f"Chosen normal summer day: {normal_date}")

    jobs = [
        dict(
            filename="sept6_2022.json",
            forecast_id="sept6_2022",
            date="2022-09-06",
            temp_offset_f=0.0,
            include_truth=True,
        ),
        dict(
            filename="sept6_2022_plus5f.json",
            forecast_id="sept6_2022_plus5f",
            date="2022-09-06",
            temp_offset_f=5.0,
            include_truth=False,
        ),
        dict(
            filename="normal_summer_day.json",
            forecast_id="normal_summer_day",
            date=normal_date,
            temp_offset_f=0.0,
            include_truth=True,
        ),
    ]

    print("\n=== export_forecasts summary ===")
    for job in jobs:
        doc = build_c1(
            eia_df,
            weather_df,
            forecast_id=job["forecast_id"],
            date=job["date"],
            temp_offset_f=job["temp_offset_f"],
            include_truth=job["include_truth"],
            bundle=bundle,
        )
        forecast = write_and_validate(doc, job["filename"])
        print(f"{job['filename']}  (date {job['date']}, temp_offset_f {job['temp_offset_f']:g})")
        _report(job["filename"], forecast)
    print(f"\nValidated {len(jobs)} files against Forecast.from_dict in {OUT_DIR}")


if __name__ == "__main__":
    main()
