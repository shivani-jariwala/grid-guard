"""Handler tests for GET /scenarios, GET /resources and POST /simulate, using fake API Gateway v2 events."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from shared.data import LocalStore

from conftest import BASE_SCENARIO, forecast_dict

FUNCTIONS = Path(__file__).resolve().parents[1] / "backend" / "functions"


def load_handler_module(name):
    """Each Lambda's file is called app.py, so load them under unique module names."""
    spec = importlib.util.spec_from_file_location(f"{name}_app", FUNCTIONS / name / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def data_dir(tmp_path):
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "forecasts").mkdir()
    (tmp_path / "scenarios" / "test_day.json").write_text(json.dumps(BASE_SCENARIO))
    (tmp_path / "forecasts" / "test_day.json").write_text(json.dumps(forecast_dict()))
    return tmp_path


@pytest.fixture
def call(data_dir, monkeypatch):
    """call("simulate_api", event) -> (status, body), with the module's store pointed at data_dir."""
    store = LocalStore(data_dir / "scenarios", data_dir / "forecasts")

    def _call(name, event):
        module = load_handler_module(name)
        monkeypatch.setattr(module, "get_store", lambda: store)
        response = module.handler(event, None)
        return response["statusCode"], json.loads(response["body"])

    return _call


def get(params=None):
    return {"routeKey": "GET /x", "queryStringParameters": params}


def post(body):
    return {"routeKey": "POST /simulate", "body": body if isinstance(body, str) else json.dumps(body)}


# ---------------------------------------------------------------------------
# GET /scenarios
# ---------------------------------------------------------------------------


def test_scenarios_lists_summaries(call, data_dir):
    other = dict(BASE_SCENARIO, id="another_day", name="Another day")
    (data_dir / "scenarios" / "another_day.json").write_text(json.dumps(other))
    status, body = call("scenarios_api", get())
    assert status == 200
    assert body == {
        "scenarios": [
            {"id": "another_day", "name": "Another day", "date": "2022-09-06", "margin_target_pct": 6.0},
            {"id": "test_day", "name": "Test day", "date": "2022-09-06", "margin_target_pct": 6.0},
        ]
    }


def test_scenarios_with_a_broken_file_is_a_data_error(call, data_dir):
    (data_dir / "scenarios" / "broken.json").write_text("{")
    status, body = call("scenarios_api", get())
    assert status == 500 and body["error"]["code"] == "DATA_ERROR"


# ---------------------------------------------------------------------------
# GET /resources
# ---------------------------------------------------------------------------


def test_resources_returns_the_stored_scenario(call):
    status, body = call("resources_api", get({"scenario": "test_day"}))
    assert status == 200 and body == BASE_SCENARIO


@pytest.mark.parametrize(
    "params, status, code",
    [
        (None, 400, "BAD_REQUEST"),
        ({"scenario": "../etc/passwd"}, 400, "BAD_REQUEST"),
        ({"scenario": "nope"}, 404, "SCENARIO_NOT_FOUND"),
    ],
)
def test_resources_errors(call, params, status, code):
    got_status, body = call("resources_api", get(params))
    assert (got_status, body["error"]["code"]) == (status, code)


# ---------------------------------------------------------------------------
# GET /forecast
# ---------------------------------------------------------------------------


def test_forecast_returns_the_scenarios_forecast(call):
    status, body = call("forecast_api", get({"scenario": "test_day"}))
    assert status == 200 and body == forecast_dict()


@pytest.mark.parametrize(
    "params, status, code",
    [
        (None, 400, "BAD_REQUEST"),
        ({"scenario": "../etc/passwd"}, 400, "BAD_REQUEST"),
        ({"scenario": "nope"}, 404, "SCENARIO_NOT_FOUND"),
    ],
)
def test_forecast_errors(call, params, status, code):
    got_status, body = call("forecast_api", get(params))
    assert (got_status, body["error"]["code"]) == (status, code)


def test_forecast_missing_forecast_file(call, data_dir):
    (data_dir / "forecasts" / "test_day.json").unlink()
    status, body = call("forecast_api", get({"scenario": "test_day"}))
    assert status == 404 and body["error"]["code"] == "FORECAST_NOT_FOUND"


def test_forecast_uses_scenarios_forecast_id(call, data_dir):
    # The scenario points at a different forecast file by forecast_id.
    other = dict(BASE_SCENARIO, id="other_day", forecast_id="other_forecast")
    (data_dir / "scenarios" / "other_day.json").write_text(json.dumps(other))
    (data_dir / "forecasts" / "other_forecast.json").write_text(json.dumps(forecast_dict([12000.0] * 24)))
    status, body = call("forecast_api", get({"scenario": "other_day"}))
    assert status == 200 and body["hours"][0]["p90"] == 12000.0


# ---------------------------------------------------------------------------
# POST /simulate
# ---------------------------------------------------------------------------


def test_simulate_no_action_plan(call):
    status, body = call("simulate_api", post({"scenario_id": "test_day", "plan": {}}))
    assert status == 200
    assert body["engine"] == "v1" and body["valid"] is True
    assert body["red_hours"] == [17, 18, 19, 20]


def test_simulate_target_override(call):
    status, body = call("simulate_api", post({"scenario_id": "test_day", "plan": {}, "margin_target_pct": 10}))
    assert status == 200 and body["margin_target_pct"] == 10 and 3 in body["yellow_hours"]


def test_simulate_invalid_plan_is_still_200(call):
    status, body = call("simulate_api", post({"scenario_id": "test_day", "plan": {"battery": [{"hour": 18, "mw": 99999}]}}))
    assert status == 200 and body["valid"] is False
    assert body["violations"][0]["code"] == "BATTERY_POWER_EXCEEDED"


def test_simulate_bad_plan_shape_is_still_200(call):
    status, body = call("simulate_api", post({"scenario_id": "test_day", "plan": {"batery": []}}))
    assert status == 200 and body["violations"][0]["code"] == "PLAN_SHAPE" and body["plan_hash"] is None


@pytest.mark.parametrize(
    "request_body, message",
    [
        ("not json", "valid JSON"),
        ({"plan": {}}, "'scenario_id' is required"),
        ({"scenario_id": "test_day"}, "'plan' is required"),
        ({"scenario_id": "test_day", "plan": {}, "margin_target_pct": "6"}, "must be a number"),
        ({"scenario_id": "test_day", "plan": {}, "margin_target_pct": 150}, "between 0 and 100"),
    ],
)
def test_simulate_bad_requests(call, request_body, message):
    status, body = call("simulate_api", post(request_body))
    assert status == 400 and body["error"]["code"] == "BAD_REQUEST"
    assert message in body["error"]["message"]


def test_simulate_unknown_scenario(call):
    status, body = call("simulate_api", post({"scenario_id": "nope", "plan": {}}))
    assert status == 404 and body["error"]["code"] == "SCENARIO_NOT_FOUND"


def test_simulate_missing_forecast(call, data_dir):
    (data_dir / "forecasts" / "test_day.json").unlink()
    status, body = call("simulate_api", post({"scenario_id": "test_day", "plan": {}}))
    assert status == 404 and body["error"]["code"] == "FORECAST_NOT_FOUND"


def test_simulate_impossible_data_is_a_data_error(call, data_dir):
    (data_dir / "forecasts" / "test_day.json").write_text(json.dumps(forecast_dict([100.0] * 24)))
    plan = {"demand_response": [{"name": "Program A", "hours": [17]}]}  # 100 - 400 MW < 0
    status, body = call("simulate_api", post({"scenario_id": "test_day", "plan": plan}))
    assert status == 500 and body["error"]["code"] == "DATA_ERROR"
