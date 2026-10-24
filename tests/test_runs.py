"""Run routes: run record + daily cap (shared/runs.py), POST /runs, GET /runs/{id}, and the stub worker.

DynamoDB is faked with moto, so no AWS calls are made. The Lambda invoke is a fake function.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

import boto3
import pytest
from moto import mock_aws

from shared.config import load_settings
from shared.data import LocalStore
from shared.runs import (
    DailyCounter,
    DailyLimitReached,
    RunStore,
    add_simulation,
    check_submit,
    from_dynamo,
    new_run,
    pacific_day,
    summary_of,
    to_dynamo,
)
from shared.simulator import simulate_dict

from conftest import BASE_SCENARIO, forecast_dict
from test_handlers import load_handler_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def aws(monkeypatch):
    """Fake AWS (moto) with the Runs and Counters tables."""
    for key in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        db = boto3.resource("dynamodb", region_name="us-east-1")
        runs = db.create_table(
            TableName="runs",
            KeySchema=[{"AttributeName": "run_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "run_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        counters = db.create_table(
            TableName="counters",
            KeySchema=[{"AttributeName": "day", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "day", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield RunStore(runs), DailyCounter(counters)


@pytest.fixture
def store(tmp_path):
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "forecasts").mkdir()
    (tmp_path / "scenarios" / "test_day.json").write_text(json.dumps(BASE_SCENARIO))
    (tmp_path / "forecasts" / "test_day.json").write_text(json.dumps(forecast_dict()))
    return LocalStore(tmp_path / "scenarios", tmp_path / "forecasts")


def body_of(response):
    return json.loads(response["body"])


# ---------------------------------------------------------------------------
# shared/runs.py
# ---------------------------------------------------------------------------


def test_dynamo_number_round_trip():
    data = {"a": 1.25, "b": 6.0, "c": [0.1, 2], "d": {"e": None, "f": True}}
    assert from_dynamo(to_dynamo(data)) == {"a": 1.25, "b": 6, "c": [0.1, 2], "d": {"e": None, "f": True}}


def test_pacific_day_rolls_over_at_california_midnight():
    assert pacific_day(datetime(2026, 10, 24, 3, 0, tzinfo=timezone.utc)) == "2026-10-23"  # 8 p.m. PDT
    assert pacific_day(datetime(2026, 10, 24, 8, 0, tzinfo=timezone.utc)) == "2026-10-24"  # 1 a.m. PDT


def test_daily_counter_stops_at_the_cap(aws):
    _, counter = aws
    assert counter.take("2026-10-23", cap=2) == 1
    assert counter.take("2026-10-23", cap=2) == 2
    with pytest.raises(DailyLimitReached):
        counter.take("2026-10-23", cap=2)
    assert counter.take("2026-10-24", cap=2) == 1  # a new day starts fresh


def test_run_store_round_trip_and_no_overwrite_on_create(aws):
    runs, _ = aws
    run = new_run("test_day", 6.5, "stub-agent")
    runs.create(run)
    loaded = runs.get(run["run_id"])
    assert loaded["margin_target_pct"] == 6.5 and loaded["status"] == "running"
    assert loaded["counts"] == {"simulate_calls": 0, "tool_calls": 0}
    with pytest.raises(Exception):
        runs.create(run)  # same run_id twice is refused
    assert runs.get("00000000-0000-4000-8000-000000000000") is None


def test_check_submit_gate(scenario, forecast):
    run = new_run("test_day", 6, "stub-agent")
    red = {}
    green_plan = {
        "battery": [{"hour": 17, "mw": 660}, {"hour": 18, "mw": 660}, {"hour": 19, "mw": 660}, {"hour": 20, "mw": 850}],
        "demand_response": [{"name": "Program A", "hours": [17, 18, 19]}, {"name": "Program B", "hours": [20]}],
    }
    bad = {"battery": [{"hour": 18, "mw": 99999}]}
    for plan in (red, green_plan, bad):
        add_simulation(run, plan, simulate_dict(scenario, forecast, plan))

    assert check_submit(run, green_plan, "ALL_GREEN") is None
    assert check_submit(run, red, "CANNOT_MEET_TARGET") is None
    assert "doesn't match" in check_submit(run, red, "ALL_GREEN")
    assert "invalid" in check_submit(run, bad, "CANNOT_MEET_TARGET")
    assert "never simulated" in check_submit(run, {"battery": [{"hour": 18, "mw": 1}]}, "CANNOT_MEET_TARGET")
    assert "status must be" in check_submit(run, red, "MAYBE")
    # the same plan written differently still matches (hash is recomputed from the plan)
    reordered = {"demand_response": list(reversed(green_plan["demand_response"])), "battery": green_plan["battery"][::-1]}
    assert check_submit(run, reordered, "ALL_GREEN") is None


def test_summary_of(scenario, forecast):
    assert summary_of(simulate_dict(scenario, forecast, {})).startswith("RED at 17, 18, 19, 20; lowest margin -3.64%")
    assert summary_of(simulate_dict(scenario, forecast, {"battery": [{"hour": 1, "mw": 99999}]})).startswith(
        "Invalid plan: BATTERY_ENERGY_EXCEEDED, BATTERY_POWER_EXCEEDED"
    )


# ---------------------------------------------------------------------------
# POST /runs
# ---------------------------------------------------------------------------


@pytest.fixture
def start(aws, store):
    runs, counter = aws
    module = load_handler_module("agent_start")
    invoked = []

    def _start(body, cap=100, invoke=None):
        settings = load_settings({"DAILY_RUN_CAP": str(cap)})
        event = {"routeKey": "POST /runs", "body": body if isinstance(body, str) else json.dumps(body)}
        fn = invoke or invoked.append
        return module.api_handler(lambda e, c: module.start_run(e, store, runs, counter, fn, settings))(event, None)

    _start.invoked = invoked
    _start.runs = runs
    return _start


def test_start_run_saves_and_starts_the_worker(start):
    r = start({"scenario_id": "test_day"})
    assert r["statusCode"] == 202
    run_id = body_of(r)["run_id"]
    assert body_of(r)["status"] == "running"
    assert start.invoked == [run_id]
    saved = start.runs.get(run_id)
    assert saved["scenario_id"] == "test_day" and saved["margin_target_pct"] == 6 and saved["model_id"] == "stub-agent"


def test_start_run_target_override(start):
    run_id = body_of(start({"scenario_id": "test_day", "margin_target_pct": 8}))["run_id"]
    assert start.runs.get(run_id)["margin_target_pct"] == 8


def test_start_run_rejects_bad_requests_without_using_the_cap(start, aws):
    _, counter = aws
    assert body_of(start("nope"))["error"]["code"] == "BAD_REQUEST"
    assert body_of(start({"scenario_id": "test_day", "margin_target_pct": 500}))["error"]["code"] == "BAD_REQUEST"
    r = start({"scenario_id": "unknown"})
    assert r["statusCode"] == 404 and body_of(r)["error"]["code"] == "SCENARIO_NOT_FOUND"
    assert start.invoked == []
    assert counter.table.scan()["Items"] == []  # nothing was counted


def test_start_run_daily_cap(start):
    assert start({"scenario_id": "test_day"}, cap=1)["statusCode"] == 202
    r = start({"scenario_id": "test_day"}, cap=1)
    assert r["statusCode"] == 429 and body_of(r)["error"]["code"] == "DAILY_LIMIT_REACHED"
    assert len(start.invoked) == 1  # the refused run never started a worker


def test_start_run_worker_failure_marks_the_run_failed(start):
    def broken(run_id):
        raise RuntimeError("lambda is down")

    r = start({"scenario_id": "test_day"}, invoke=broken)
    assert r["statusCode"] == 500 and body_of(r)["error"]["code"] == "WORKER_START_FAILED"
    items = start.runs.table.scan()["Items"]
    assert len(items) == 1 and items[0]["status"] == "failed" and items[0]["error"]["code"] == "WORKER_START_FAILED"


# ---------------------------------------------------------------------------
# GET /runs/{run_id}
# ---------------------------------------------------------------------------


def test_run_status(aws):
    runs, _ = aws
    module = load_handler_module("run_status")
    run = new_run("test_day", 6, "stub-agent")
    runs.create(run)

    def call(run_id):
        handler = module.api_handler(lambda e, c: module.ok(module.get_run(e, runs)))
        r = handler({"routeKey": "GET /runs/{run_id}", "pathParameters": {"run_id": run_id}}, None)
        return r["statusCode"], body_of(r)

    status, body = call(run["run_id"])
    assert status == 200 and body["run_id"] == run["run_id"] and body["status"] == "running"
    status, body = call("00000000-0000-4000-8000-000000000000")
    assert status == 404 and body["error"]["code"] == "RUN_NOT_FOUND"
    status, body = call("../../etc")
    assert status == 400 and body["error"]["code"] == "BAD_REQUEST"


# ---------------------------------------------------------------------------
# Stub worker
# ---------------------------------------------------------------------------


@pytest.fixture
def worker():
    return load_handler_module("agent_worker_stub")


def test_stub_worker_reaches_all_green(aws, store, worker):
    runs, _ = aws
    run = new_run("test_day", 6, "stub-agent")
    runs.create(run)
    worker.run_stub_agent(run["run_id"], runs, store, pause=0)

    done = runs.get(run["run_id"])
    assert done["status"] == "done" and done["error"] is None
    assert [t["tool"] for t in done["trace"]] == [
        "get_forecast", "get_resources", "simulate", "simulate", "simulate", "simulate", "submit_plan"
    ]
    assert done["counts"] == {"simulate_calls": 4, "tool_calls": 7}
    assert done["simulations"][0]["result"]["red_hours"] == [17, 18, 19, 20]  # attempt 1: no action
    assert done["final"]["status"] == "ALL_GREEN" and done["final"]["result"]["all_green"]
    assert {f["code"] for f in done["flags"]} == {"STUB_AGENT"}
    briefing = done["final"]["briefing"]
    assert briefing["alert_text"] is None and briefing["alert_label"] == "Draft only. Not sent."
    assert briefing["rationale"][0].startswith("STUB")
    assert {a["action"] for a in briefing["actions"]} == {"battery", "demand_response"}
    # never uses a public alert, and the submitted plan is one that was simulated
    assert not done["final"]["plan"]["public_alert"]["use"]
    assert done["final"]["plan_hash"] in {s["plan_hash"] for s in done["simulations"]}


def test_stub_worker_reports_cannot_meet_target(aws, tmp_path, worker):
    runs, _ = aws
    weak = copy.deepcopy(BASE_SCENARIO)
    weak["battery"]["energy_mwh"] = 400
    weak["demand_response"] = []
    (tmp_path / "s").mkdir()
    (tmp_path / "f").mkdir()
    (tmp_path / "s" / "test_day.json").write_text(json.dumps(weak))
    (tmp_path / "f" / "test_day.json").write_text(json.dumps(forecast_dict()))
    run = new_run("test_day", 6, "stub-agent")
    runs.create(run)
    worker.run_stub_agent(run["run_id"], runs, LocalStore(tmp_path / "s", tmp_path / "f"), pause=0)

    done = runs.get(run["run_id"])
    assert done["status"] == "done"
    assert done["final"]["status"] == "CANNOT_MEET_TARGET"
    assert done["counts"]["simulate_calls"] == 2  # no-action, then battery; no programs to add


def test_stub_worker_marks_data_problems_as_failed(aws, store, worker, tmp_path):
    runs, _ = aws
    (tmp_path / "forecasts" / "test_day.json").unlink()
    run = new_run("test_day", 6, "stub-agent")
    runs.create(run)
    worker.run_stub_agent(run["run_id"], runs, store, pause=0)
    failed = runs.get(run["run_id"])
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "DATA_ERROR"  # the forecast file is missing


def test_stub_worker_ignores_unknown_runs(aws, store, worker):
    runs, _ = aws
    worker.run_stub_agent("00000000-0000-4000-8000-000000000000", runs, store, pause=0)  # no crash
