"""Download hourly temperatures (°F) for 5 California cities from Open-Meteo. Owner: P1 (P1-04).

Uses the Open-Meteo historical archive API (no key required):
  https://archive-api.open-meteo.com/v1/archive

Pulls hourly temperature_2m in Fahrenheit for 2019-01-01..2022-12-31 with timezone=GMT
(UTC), so the time axis lines up with ml/data/eia_raw.parquet. The five cities are merged
into one wide table keyed on period_utc and written to ml/data/weather.parquet.

period_utc is stored to match eia_raw.parquet exactly: a UTC-naive datetime64[ns] column.
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import requests

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
START = "2019-01-01"
END = "2022-12-31"

OUT_PATH = Path(__file__).resolve().parent / "data" / "weather.parquet"

# (output_column, latitude, longitude)
CITIES = [
    ("temp_la", 34.05, -118.24),
    ("temp_riverside", 33.95, -117.40),
    ("temp_fresno", 36.74, -119.79),
    ("temp_sacramento", 38.58, -121.49),
    ("temp_sanjose", 37.34, -121.89),
]


def fetch_city(
    column: str,
    latitude: float,
    longitude: float,
    *,
    session: requests.Session,
) -> pd.DataFrame:
    """Fetch hourly temperature (°F, UTC) for one city.

    Returns a DataFrame with columns [period_utc (datetime64, UTC-naive), <column> (float)].
    """
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": START,
        "end_date": END,
        "hourly": "temperature_2m",
        "temperature_unit": "fahrenheit",
        "timezone": "GMT",
    }
    resp = session.get(ARCHIVE_URL, params=params, timeout=120)
    resp.raise_for_status()
    hourly = resp.json().get("hourly", {})

    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    print(f"  {column}: {len(times)} hourly points")

    df = pd.DataFrame({"time": times, column: temps})
    # Open-Meteo returns time as 'YYYY-MM-DDTHH:MM' in the requested timezone (GMT here).
    df["period_utc"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df[column] = pd.to_numeric(df[column], errors="coerce")
    return df[["period_utc", column]]


def missing_hours(df: pd.DataFrame, column: str) -> int:
    """Count hours in [START, END] with no (non-null) value for `column`."""
    full = pd.date_range("2019-01-01T00:00", "2022-12-31T23:00", freq="h")
    present = df.loc[df[column].notna(), "period_utc"]
    return int(len(full) - present.isin(full).sum())


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    merged: pd.DataFrame | None = None
    with requests.Session() as session:
        for column, lat, lon in CITIES:
            print(f"Fetching {column} ({lat},{lon}) ...")
            city = fetch_city(column, lat, lon, session=session)
            merged = (
                city
                if merged is None
                else merged.merge(city, on="period_utc", how="outer")
            )
            time.sleep(0.2)  # be polite to the API

    assert merged is not None
    cols = ["period_utc"] + [c for c, _, _ in CITIES]
    merged = merged.sort_values("period_utc").reset_index(drop=True)[cols]

    # Match eia_raw.parquet's microsecond-resolution timestamp type exactly so the
    # two tables join cleanly on period_utc.
    merged["period_utc"] = merged["period_utc"].astype("datetime64[us]")

    merged.to_parquet(OUT_PATH, index=False)

    expected = pd.date_range("2019-01-01T00:00", "2022-12-31T23:00", freq="h")
    print("\n=== Weather pull summary ===")
    print(f"Saved: {OUT_PATH}")
    print(f"Rows in table: {len(merged)}  (expected hourly slots: {len(expected)})")
    print("Non-null row counts per city:")
    for col, _, _ in CITIES:
        print(f"  {col:>15}: {int(merged[col].notna().sum())}")
    print("Missing hours per city (vs expected hourly grid):")
    for col, _, _ in CITIES:
        print(f"  {col:>15}: {missing_hours(merged, col)}")


if __name__ == "__main__":
    main()
