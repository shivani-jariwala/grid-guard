"""Build the model feature table from raw EIA + weather parquet. Owner: P1 (P1-05, P1-06).

Pipeline:
  1. Load ml/data/eia_raw.parquet and ml/data/weather.parquet.
  2. Merge onto a complete hourly UTC grid.
  3. Fill small gaps (<= 3 consecutive hours) by linear interpolation; drop rows where
     demand is still missing.
  4. Add calendar features from America/Los_Angeles local time (DST-aware).
  5. Add weather features (per-city temps + avg/max + 24h/72h heat build-up + CDD).
  6. Add demand lags on the continuous UTC grid: 48h and 168h (7 days). No lag < 48h,
     because the last 24h aren't known when a day-ahead forecast is made.

The core logic lives in build_features(eia_df, weather_df, temp_offset_f=0.0) so the
+5 degF scenario can reuse it by shifting all city temps before the temp features are
computed.

Column roles:
  FEATURES      -> columns the model may train on.
  NOT_FEATURES  -> kept for evaluation/export only. caiso_df must NEVER be a model feature.
"""

from __future__ import annotations

from pathlib import Path

import holidays
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent / "data"
EIA_PATH = DATA_DIR / "eia_raw.parquet"
WEATHER_PATH = DATA_DIR / "weather.parquet"
OUT_PATH = DATA_DIR / "features.parquet"

LOCAL_TZ = "America/Los_Angeles"
GRID_START = "2019-01-01T00:00"
GRID_END = "2022-12-31T23:00"
MAX_GAP_HOURS = 3  # longest run of missing hours we linearly interpolate

CITY_TEMP_COLS = [
    "temp_la",
    "temp_riverside",
    "temp_fresno",
    "temp_sacramento",
    "temp_sanjose",
]

# Columns kept for evaluation/export but never used as model inputs.
# caiso_df is the CAISO day-ahead forecast: it is a baseline to beat, NOT a feature.
NOT_FEATURES = ["demand", "caiso_df", "solar", "wind"]

# Columns the model is allowed to train on.
FEATURES = [
    # calendar
    "hour",
    "day_of_week",
    "month",
    "is_holiday",
    "is_weekend",
    # weather (current)
    *CITY_TEMP_COLS,
    "temp_avg",
    "temp_max",
    "temp_avg_24h",
    "temp_avg_72h",
    "cdd",
    # demand history (no lag shorter than 48h)
    "demand_lag_48h",
    "demand_lag_168h",
]


def _hourly_utc_grid() -> pd.DatetimeIndex:
    return pd.date_range(GRID_START, GRID_END, freq="h")


def build_features(
    eia_df: pd.DataFrame,
    weather_df: pd.DataFrame,
    temp_offset_f: float = 0.0,
) -> pd.DataFrame:
    """Build the wide feature table.

    Args:
        eia_df: raw EIA table with period_utc, demand, caiso_df, solar, wind.
        weather_df: raw weather table with period_utc + the 5 city temps (degF).
        temp_offset_f: added to every city temperature before temp features are
            computed. Used by the +5 degF heat-wave scenario.

    Returns:
        A DataFrame indexed 0..N with period_utc, the NOT_FEATURES columns, the
        calendar/weather/lag FEATURES, plus local_time and date_local.
    """
    grid = pd.DataFrame({"period_utc": _hourly_utc_grid()})

    df = grid.merge(eia_df, on="period_utc", how="left")
    df = df.merge(weather_df, on="period_utc", how="left")
    df = df.sort_values("period_utc").reset_index(drop=True)

    # --- 3. Fill small gaps on the continuous grid, then drop rows with no demand. ---
    value_cols = ["demand", "caiso_df", "solar", "wind", *CITY_TEMP_COLS]
    for col in value_cols:
        df[col] = df[col].interpolate(
            method="linear", limit=MAX_GAP_HOURS, limit_area="inside"
        )

    # Apply the scenario temperature offset before any temp feature is derived.
    if temp_offset_f:
        for col in CITY_TEMP_COLS:
            df[col] = df[col] + temp_offset_f

    # --- 5. Weather features (computed on the continuous UTC grid). ---
    df["temp_avg"] = df[CITY_TEMP_COLS].mean(axis=1)
    df["temp_max"] = df[CITY_TEMP_COLS].max(axis=1)
    df["temp_avg_24h"] = df["temp_avg"].rolling(window=24, min_periods=24).mean()
    df["temp_avg_72h"] = df["temp_avg"].rolling(window=72, min_periods=72).mean()
    df["cdd"] = (df["temp_avg"] - 65.0).clip(lower=0.0)

    # --- 6. Demand lags on the continuous UTC grid (one row per hour, no gaps). ---
    df["demand_lag_48h"] = df["demand"].shift(48)
    df["demand_lag_168h"] = df["demand"].shift(168)

    # --- 4. Calendar features from DST-aware local time. ---
    local = (
        df["period_utc"]
        .dt.tz_localize("UTC")
        .dt.tz_convert(LOCAL_TZ)
    )
    df["local_time"] = local.dt.tz_localize(None)
    df["date_local"] = df["local_time"].dt.normalize()
    df["hour"] = local.dt.hour
    df["day_of_week"] = local.dt.day_of_week  # Monday=0
    df["month"] = local.dt.month
    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)

    years = range(df["date_local"].dt.year.min(), df["date_local"].dt.year.max() + 1)
    us_ca = holidays.country_holidays("US", subdiv="CA", years=years)
    df["is_holiday"] = (
        df["date_local"].dt.date.map(lambda d: d in us_ca).astype(int)
    )

    # Drop rows where demand is still missing after interpolation (the model target).
    df = df.dropna(subset=["demand"]).reset_index(drop=True)

    ordered = (
        ["period_utc", "local_time", "date_local"]
        + NOT_FEATURES
        + [c for c in FEATURES if c not in NOT_FEATURES]
    )
    return df[ordered]


def main() -> None:
    eia_df = pd.read_parquet(EIA_PATH)
    weather_df = pd.read_parquet(WEATHER_PATH)

    features = build_features(eia_df, weather_df)
    features.to_parquet(OUT_PATH, index=False)

    print("=== build_features summary ===")
    print(f"Saved: {OUT_PATH}")
    print(f"Shape: {features.shape}")
    print(
        f"Local date range: {features['date_local'].min().date()} "
        f"-> {features['date_local'].max().date()}"
    )
    print(f"UTC range: {features['period_utc'].min()} -> {features['period_utc'].max()}")

    print("\nNull counts per column:")
    nulls = features.isna().sum()
    for col in features.columns:
        print(f"  {col:>16}: {int(nulls[col])}")

    print("\nFEATURES (model inputs):")
    print(f"  {FEATURES}")
    print("NOT_FEATURES (kept for eval/export, never model inputs):")
    print(f"  {NOT_FEATURES}")
    assert "caiso_df" not in FEATURES, "caiso_df must never be a feature"

    print("\n2022-09-06 local (hour, demand, temp_avg):")
    day = features[features["date_local"] == pd.Timestamp("2022-09-06")]
    day = day.sort_values("hour")
    print(day[["hour", "demand", "temp_avg"]].to_string(index=False))
    print(f"  rows for 2022-09-06 local: {len(day)}")


if __name__ == "__main__":
    main()
