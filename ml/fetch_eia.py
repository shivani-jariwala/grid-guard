"""Download CAISO hourly demand, day-ahead forecast and generation from EIA API v2. Owner: P1 (P1-03).

Pulls four CAISO (respondent=CISO) hourly series for 2019-01-01..2022-12-31 (UTC):
  - demand           -> type=D  on electricity/rto/region-data
  - caiso_df (DF)     -> type=DF on electricity/rto/region-data
  - solar (SUN)       -> fueltype=SUN on electricity/rto/fuel-type-data
  - wind  (WND)       -> fueltype=WND on electricity/rto/fuel-type-data

The API returns at most 5000 rows per call, so each series is fetched by paginating
with offset/length. `period` arrives as a UTC string 'YYYY-MM-DDTHH' and `value` as a
string; both are converted. The four series are merged into one wide table keyed on
period_utc and written to ml/data/eia_raw.parquet.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

# EIA API v2
BASE_URL = "https://api.eia.gov/v2"
REGION_DATA = f"{BASE_URL}/electricity/rto/region-data/data/"
FUEL_TYPE_DATA = f"{BASE_URL}/electricity/rto/fuel-type-data/data/"

RESPONDENT = "CISO"
FREQUENCY = "hourly"
START = "2019-01-01T00"
END = "2022-12-31T23"
PAGE_LENGTH = 5000  # API hard cap per call

OUT_PATH = Path(__file__).resolve().parent / "data" / "eia_raw.parquet"

# (output_column, endpoint_url, facet_key, facet_value)
SERIES = [
    ("demand", REGION_DATA, "type", "D"),
    ("caiso_df", REGION_DATA, "type", "DF"),
    ("solar", FUEL_TYPE_DATA, "fueltype", "SUN"),
    ("wind", FUEL_TYPE_DATA, "fueltype", "WND"),
]


def _load_api_key() -> str:
    """Load EIA_API_KEY from .env (project root) or the environment."""
    # .env lives at the repo root, one level up from ml/.
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    key = os.getenv("EIA_API_KEY")
    if not key:
        sys.exit("EIA_API_KEY not found. Set it in .env or the environment.")
    return key


def fetch_series(
    api_key: str,
    url: str,
    facet_key: str,
    facet_value: str,
    *,
    session: requests.Session,
) -> pd.DataFrame:
    """Fetch one hourly series for CISO, paginating with offset/length.

    Returns a DataFrame with columns [period_utc (datetime64, UTC-naive), value (float)].
    """
    params = {
        "api_key": api_key,
        "frequency": FREQUENCY,
        "data[0]": "value",
        "facets[respondent][]": RESPONDENT,
        f"facets[{facet_key}][]": facet_value,
        "start": START,
        "end": END,
        "sort[0][column]": "period",
        "sort[0][direction]": "asc",
        "length": PAGE_LENGTH,
    }

    rows: list[dict] = []
    offset = 0
    total: int | None = None

    while True:
        params["offset"] = offset
        resp = session.get(url, params=params, timeout=60)
        resp.raise_for_status()
        payload = resp.json().get("response", {})

        if total is None:
            # `total` is a string in the API payload.
            total = int(payload.get("total", 0))

        batch = payload.get("data", [])
        rows.extend(batch)

        got = len(batch)
        print(
            f"  {facet_key}={facet_value}: offset {offset:>6} -> +{got} rows "
            f"({len(rows)}/{total})"
        )

        offset += got
        if got == 0 or offset >= total:
            break
        time.sleep(0.2)  # be polite to the API

    if not rows:
        return pd.DataFrame(columns=["period_utc", "value"])

    df = pd.DataFrame(rows)
    # period is a UTC string 'YYYY-MM-DDTHH'; value is a string.
    df["period_utc"] = pd.to_datetime(df["period"], format="%Y-%m-%dT%H", utc=True)
    df["period_utc"] = df["period_utc"].dt.tz_localize(None)  # store UTC-naive
    df["value"] = pd.to_numeric(df["value"], errors="coerce")

    # Collapse any accidental duplicate periods (API can repeat on paging seams).
    df = (
        df[["period_utc", "value"]]
        .dropna(subset=["period_utc"])
        .groupby("period_utc", as_index=False)["value"]
        .mean()
        .sort_values("period_utc")
        .reset_index(drop=True)
    )
    return df


def missing_hours(df: pd.DataFrame, column: str) -> int:
    """Count hours in [START, END] with no (non-null) value for `column`."""
    full = pd.date_range(
        start=pd.Timestamp("2019-01-01T00:00"),
        end=pd.Timestamp("2022-12-31T23:00"),
        freq="h",
    )
    present = df.loc[df[column].notna(), "period_utc"]
    return int(len(full) - present.isin(full).sum())


def main() -> None:
    api_key = _load_api_key()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    merged: pd.DataFrame | None = None
    with requests.Session() as session:
        for column, url, facet_key, facet_value in SERIES:
            print(f"Fetching {column} ({facet_key}={facet_value}) ...")
            series = fetch_series(
                api_key, url, facet_key, facet_value, session=session
            )
            series = series.rename(columns={"value": column})
            merged = (
                series
                if merged is None
                else merged.merge(series, on="period_utc", how="outer")
            )

    assert merged is not None
    merged = merged.sort_values("period_utc").reset_index(drop=True)
    merged = merged[["period_utc", "demand", "caiso_df", "solar", "wind"]]

    merged.to_parquet(OUT_PATH, index=False)

    # Report.
    expected = pd.date_range("2019-01-01T00:00", "2022-12-31T23:00", freq="h")
    print("\n=== EIA raw pull summary ===")
    print(f"Saved: {OUT_PATH}")
    print(f"Rows in table: {len(merged)}  (expected hourly slots: {len(expected)})")
    print("Non-null row counts per series:")
    for col in ("demand", "caiso_df", "solar", "wind"):
        print(f"  {col:>9}: {int(merged[col].notna().sum())}")
    print("Missing hours per series (vs expected hourly grid):")
    for col in ("demand", "caiso_df", "solar", "wind"):
        print(f"  {col:>9}: {missing_hours(merged, col)}")


if __name__ == "__main__":
    main()
