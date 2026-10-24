"""Where scenarios (C2) and forecasts (C1) are read from. Owner: P3.

- FORECAST_BUCKET empty -> LocalStore: `scenarios/` and `forecasts/` folders in the repo.
- FORECAST_BUCKET set   -> S3Store: s3://bucket/{SCENARIO_PREFIX}{id}.json and {FORECAST_PREFIX}{id}.json.

Scenarios sit in S3 next to forecasts, so P5 can update values by uploading a file
instead of redeploying code.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple

from .config import REPO_ROOT, Settings, get_settings
from .models import Forecast, Scenario, ShapeError

_ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")


class NotFound(Exception):
    """A scenario or forecast doesn't exist. `code` is the API error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DataError(Exception):
    """Stored data is broken (bad JSON, breaks a contract, or impossible numbers)."""


def _check_id(value: str) -> str:
    # Defense in depth: handlers validate IDs too, but the store never trusts them.
    if not _ID_RE.match(value):
        raise ValueError(f"Invalid ID {value!r}")
    return value


def _parse_json(text: str, what: str) -> Dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise DataError(f"{what} is not valid JSON: {err}") from None
    if not isinstance(data, dict):
        raise DataError(f"{what} must be a JSON object")
    return data


class Store(Protocol):
    def list_scenario_ids(self) -> List[str]: ...
    def get_scenario_raw(self, scenario_id: str) -> Dict[str, Any]: ...
    def get_forecast_raw(self, forecast_id: str) -> Dict[str, Any]: ...


class LocalStore:
    """Reads JSON files from two folders. Used for local dev and tests."""

    def __init__(self, scenarios_dir: Path, forecasts_dir: Path) -> None:
        self.scenarios_dir = Path(scenarios_dir)
        self.forecasts_dir = Path(forecasts_dir)

    def list_scenario_ids(self) -> List[str]:
        if not self.scenarios_dir.is_dir():
            return []
        return sorted(p.stem for p in self.scenarios_dir.glob("*.json") if _ID_RE.match(p.stem))

    def get_scenario_raw(self, scenario_id: str) -> Dict[str, Any]:
        return self._read(self.scenarios_dir, _check_id(scenario_id), "SCENARIO_NOT_FOUND", "scenario")

    def get_forecast_raw(self, forecast_id: str) -> Dict[str, Any]:
        return self._read(self.forecasts_dir, _check_id(forecast_id), "FORECAST_NOT_FOUND", "forecast")

    @staticmethod
    def _read(folder: Path, item_id: str, code: str, what: str) -> Dict[str, Any]:
        path = folder / f"{item_id}.json"
        if not path.is_file():
            raise NotFound(code, f"No {what} called '{item_id}'.")
        return _parse_json(path.read_text(encoding="utf-8"), f"{what} '{item_id}'")


class S3Store:
    """Reads JSON objects from one S3 bucket."""

    def __init__(self, bucket: str, scenario_prefix: str, forecast_prefix: str, client: Any = None) -> None:
        if client is None:
            import boto3  # only needed in AWS / when a bucket is configured

            client = boto3.client("s3")
        self.client = client
        self.bucket = bucket
        self.scenario_prefix = scenario_prefix
        self.forecast_prefix = forecast_prefix

    def list_scenario_ids(self) -> List[str]:
        ids = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.scenario_prefix):
            for obj in page.get("Contents", []):
                name = obj["Key"][len(self.scenario_prefix) :]
                if name.endswith(".json") and "/" not in name and _ID_RE.match(name[: -len(".json")]):
                    ids.append(name[: -len(".json")])
        return sorted(ids)

    def get_scenario_raw(self, scenario_id: str) -> Dict[str, Any]:
        return self._get(self.scenario_prefix, _check_id(scenario_id), "SCENARIO_NOT_FOUND", "scenario")

    def get_forecast_raw(self, forecast_id: str) -> Dict[str, Any]:
        return self._get(self.forecast_prefix, _check_id(forecast_id), "FORECAST_NOT_FOUND", "forecast")

    def _get(self, prefix: str, item_id: str, code: str, what: str) -> Dict[str, Any]:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=f"{prefix}{item_id}.json")
        except self.client.exceptions.NoSuchKey:
            raise NotFound(code, f"No {what} called '{item_id}'.") from None
        return _parse_json(response["Body"].read().decode("utf-8"), f"{what} '{item_id}'")


def get_store(settings: Optional[Settings] = None) -> Store:
    settings = settings or get_settings()
    if settings.forecast_bucket:
        return S3Store(settings.forecast_bucket, settings.scenario_prefix, settings.forecast_prefix)
    return LocalStore(REPO_ROOT / "scenarios", REPO_ROOT / "forecasts")


# ---------------------------------------------------------------------------
# Load + validate against the contracts
# ---------------------------------------------------------------------------


def load_scenario(store: Store, scenario_id: str) -> Tuple[Dict[str, Any], Scenario]:
    """Raw JSON (as stored, for GET /resources) plus the parsed Scenario."""
    raw = store.get_scenario_raw(scenario_id)
    try:
        scenario = Scenario.from_dict(raw)
    except ShapeError as err:
        raise DataError(f"scenario '{scenario_id}' breaks contract C2 at {err}") from None
    if scenario.id != scenario_id:
        raise DataError(f"scenario file '{scenario_id}' has id '{scenario.id}'")
    return raw, scenario


def load_forecast(store: Store, forecast_id: str) -> Tuple[Dict[str, Any], Forecast]:
    """Raw JSON (for GET /forecast) plus the parsed Forecast."""
    raw = store.get_forecast_raw(forecast_id)
    try:
        forecast = Forecast.from_dict(raw)
    except ShapeError as err:
        raise DataError(f"forecast '{forecast_id}' breaks contract C1 at {err}") from None
    return raw, forecast
