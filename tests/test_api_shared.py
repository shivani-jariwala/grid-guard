"""Tests for the shared API helpers (shared/api.py) and the local data store (shared/data.py)."""

from __future__ import annotations

import base64
import json

import pytest

from shared.api import ApiError, api_handler, json_body, ok, optional_number, valid_id, valid_run_id
from shared.data import DataError, LocalStore, NotFound, load_forecast, load_scenario

from conftest import BASE_SCENARIO, forecast_dict


def body(response):
    return json.loads(response["body"])


# ---------------------------------------------------------------------------
# api.py
# ---------------------------------------------------------------------------


def test_ok_builds_a_json_response():
    r = ok({"a": 1}, 201)
    assert r["statusCode"] == 201
    assert r["headers"]["Content-Type"] == "application/json"
    assert body(r) == {"a": 1}


def test_json_body_accepts_plain_and_base64():
    assert json_body({"body": '{"x": 1}'}) == {"x": 1}
    encoded = base64.b64encode(b'{"x": 2}').decode()
    assert json_body({"body": encoded, "isBase64Encoded": True}) == {"x": 2}


@pytest.mark.parametrize("raw, message", [(None, "required"), ("", "required"), ("{nope", "valid JSON"), ("[1]", "JSON object")])
def test_json_body_rejects_bad_bodies(raw, message):
    with pytest.raises(ApiError) as err:
        json_body({"body": raw})
    assert err.value.status == 400 and message in err.value.message


@pytest.mark.parametrize("value", [None, "", "Sept6", "a/b", "../etc", "a" * 65, 5])
def test_valid_id_blocks_bad_ids(value):
    with pytest.raises(ApiError):
        valid_id(value, "scenario")


def test_valid_ids_pass():
    assert valid_id("sept6_2022_plus5f", "scenario") == "sept6_2022_plus5f"
    assert valid_run_id("0f8fad5b-d9cb-469f-a165-70867728950e")
    with pytest.raises(ApiError):
        valid_run_id("not-a-uuid")


def test_optional_number():
    assert optional_number({}, "t", 0, 100) is None
    assert optional_number({"t": 6}, "t", 0, 100) == 6.0
    for bad in (True, "6", -1, 101):
        with pytest.raises(ApiError):
            optional_number({"t": bad}, "t", 0, 100)


def test_api_handler_maps_errors():
    @api_handler
    def h(event, context):
        raise event["exc"]

    cases = [
        (ApiError(400, "BAD_REQUEST", "nope"), 400, "BAD_REQUEST"),
        (NotFound("SCENARIO_NOT_FOUND", "missing"), 404, "SCENARIO_NOT_FOUND"),
        (DataError("secret path /x/y.json broke"), 500, "DATA_ERROR"),
        (RuntimeError("boom"), 500, "INTERNAL_ERROR"),
    ]
    for exc, status, code in cases:
        r = h({"exc": exc}, None)
        assert r["statusCode"] == status
        assert body(r)["error"]["code"] == code
    # internal details never reach the client
    assert "/x/y.json" not in h({"exc": DataError("secret path /x/y.json broke")}, None)["body"]
    assert "boom" not in h({"exc": RuntimeError("boom")}, None)["body"]


# ---------------------------------------------------------------------------
# data.py (LocalStore)
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "forecasts").mkdir()
    (tmp_path / "scenarios" / "test_day.json").write_text(json.dumps(BASE_SCENARIO))
    (tmp_path / "forecasts" / "test_day.json").write_text(json.dumps(forecast_dict()))
    return LocalStore(tmp_path / "scenarios", tmp_path / "forecasts")


def test_local_store_lists_and_loads(store):
    assert store.list_scenario_ids() == ["test_day"]
    raw, scenario = load_scenario(store, "test_day")
    assert raw["name"] == "Test day" and scenario.margin_target_pct == 6
    _, forecast = load_forecast(store, scenario.forecast_id)
    assert len(forecast.hours) == 24


def test_local_store_not_found_codes(store):
    with pytest.raises(NotFound) as err:
        store.get_scenario_raw("nope")
    assert err.value.code == "SCENARIO_NOT_FOUND"
    with pytest.raises(NotFound) as err:
        store.get_forecast_raw("nope")
    assert err.value.code == "FORECAST_NOT_FOUND"


def test_local_store_refuses_unsafe_ids(store):
    with pytest.raises(ValueError):
        store.get_scenario_raw("../secrets")


def test_broken_files_are_data_errors(store, tmp_path):
    (tmp_path / "scenarios" / "bad_json.json").write_text("{not json")
    with pytest.raises(DataError, match="not valid JSON"):
        load_scenario(store, "bad_json")

    broken = dict(BASE_SCENARIO, id="broken")
    del broken["battery"]
    (tmp_path / "scenarios" / "broken.json").write_text(json.dumps(broken))
    with pytest.raises(DataError, match="C2"):
        load_scenario(store, "broken")

    (tmp_path / "scenarios" / "wrong_id.json").write_text(json.dumps(BASE_SCENARIO))
    with pytest.raises(DataError, match="has id"):
        load_scenario(store, "wrong_id")


def test_missing_folders_mean_no_scenarios(tmp_path):
    assert LocalStore(tmp_path / "x", tmp_path / "y").list_scenario_ids() == []
