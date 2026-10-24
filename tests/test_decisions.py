"""Decisions and audit log: validation, POST /runs/{run_id}/decision and GET /audit (DynamoDB faked with moto)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from moto import mock_aws

from shared.decisions import DecisionError, DecisionStore, decision_timestamp, validate_decision
from shared.runs import RunStore, new_run

from test_handlers import load_handler_module

DONE_FINAL = {"status": "ALL_GREEN", "plan_hash": "abc123", "plan": {}, "result": {}, "briefing": {}}


@pytest.fixture
def aws(monkeypatch):
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
        decisions = db.create_table(
            TableName="decisions",
            KeySchema=[{"AttributeName": "run_id", "KeyType": "HASH"}, {"AttributeName": "timestamp", "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "run_id", "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield RunStore(runs), DecisionStore(decisions)


@pytest.fixture
def api(aws):
    """api(route, run_id=None, body=None, params=None) -> (status, body), calling decision_api's logic."""
    runs, decisions = aws
    module = load_handler_module("decision_api")
    module.get_run_store = lambda: runs
    module.get_decision_store = lambda: decisions

    def _api(route, run_id=None, body=None, params=None):
        event = {"routeKey": route, "pathParameters": {"run_id": run_id} if run_id else None, "queryStringParameters": params}
        if body is not None:
            event["body"] = body if isinstance(body, str) else json.dumps(body)
        r = module.handler(event, None)
        return r["statusCode"], json.loads(r["body"])

    return _api


def finished_run(runs, scenario_id="test_day"):
    run = new_run(scenario_id, 6, "stub-agent")
    run["status"] = "done"
    run["final"] = DONE_FINAL
    runs.create(run)
    return run["run_id"]


# ---------------------------------------------------------------------------
# validate_decision
# ---------------------------------------------------------------------------


def test_valid_decisions_are_cleaned():
    assert validate_decision({"operator_name": "  Dana Ruiz ", "decision": "APPROVE", "note": "  ok  "}) == {
        "operator_name": "Dana Ruiz",
        "decision": "APPROVE",
        "note": "ok",
        "edits": None,
    }
    edit = validate_decision({"operator_name": "Dana", "decision": "EDIT", "edits": {"alert_text": " Please save power 5-8 p.m. "}})
    assert edit["edits"] == {"alert_text": "Please save power 5-8 p.m."}
    assert validate_decision({"operator_name": "Dana", "decision": "REJECT", "note": "   "})["note"] is None


@pytest.mark.parametrize(
    "body, message",
    [
        ({"decision": "APPROVE"}, "operator_name"),
        ({"operator_name": "   ", "decision": "APPROVE"}, "operator_name"),
        ({"operator_name": "x" * 61, "decision": "APPROVE"}, "60"),
        ({"operator_name": "Dana", "decision": "approve"}, "APPROVE, EDIT or REJECT"),
        ({"operator_name": "Dana", "decision": "APPROVE", "note": "x" * 501}, "500"),
        ({"operator_name": "Dana", "decision": "APPROVE", "note": 5}, "text"),
        ({"operator_name": "Dana", "decision": "EDIT"}, "EDIT needs"),
        ({"operator_name": "Dana", "decision": "EDIT", "edits": {"alert_text": ""}}, "required"),
        ({"operator_name": "Dana", "decision": "EDIT", "edits": {"alert_text": "x" * 161}}, "160"),
        ({"operator_name": "Dana", "decision": "EDIT", "edits": {"alert_text": "ok", "plan": {}}}, "EDIT needs"),
        ({"operator_name": "Dana", "decision": "APPROVE", "edits": {"alert_text": "x"}}, "only allowed"),
        ({"operator_name": "Dana", "decision": "APPROVE", "email": "a@b.c"}, "Unknown field"),
    ],
)
def test_invalid_decisions(body, message):
    with pytest.raises(DecisionError, match=message):
        validate_decision(body)


def test_timestamps_have_microseconds():
    t = datetime(2026, 10, 24, 7, 0, 0, 123456, tzinfo=timezone.utc)
    assert decision_timestamp(t) == "2026-10-24T07:00:00.123456Z"


# ---------------------------------------------------------------------------
# POST /runs/{run_id}/decision
# ---------------------------------------------------------------------------


def test_record_a_decision(api, aws):
    runs, decisions = aws
    run_id = finished_run(runs)
    status, body = api("POST /runs/{run_id}/decision", run_id, {"operator_name": "Dana Ruiz", "decision": "APPROVE", "note": "Looks right"})
    assert status == 201
    assert body["run_id"] == run_id and body["decision"] == "APPROVE" and body["operator_name"] == "Dana Ruiz"
    assert body["plan_hash"] == "abc123" and body["final_status"] == "ALL_GREEN" and body["scenario_id"] == "test_day"
    assert body["timestamp"].endswith("Z")
    assert decisions.recent(10) == [body]


def test_decision_errors(api, aws):
    runs, _ = aws
    done = finished_run(runs)
    running = new_run("test_day", 6, "stub-agent")
    runs.create(running)
    ok_body = {"operator_name": "Dana", "decision": "APPROVE"}

    assert api("POST /runs/{run_id}/decision", "not-a-uuid", ok_body)[1]["error"]["code"] == "BAD_REQUEST"
    assert api("POST /runs/{run_id}/decision", done, {"decision": "APPROVE"})[1]["error"]["code"] == "BAD_REQUEST"
    assert api("POST /runs/{run_id}/decision", done, "not json")[1]["error"]["code"] == "BAD_REQUEST"
    status, body = api("POST /runs/{run_id}/decision", "00000000-0000-4000-8000-000000000000", ok_body)
    assert (status, body["error"]["code"]) == (404, "RUN_NOT_FOUND")
    status, body = api("POST /runs/{run_id}/decision", running["run_id"], ok_body)
    assert (status, body["error"]["code"]) == (409, "RUN_NOT_DONE")


# ---------------------------------------------------------------------------
# GET /audit
# ---------------------------------------------------------------------------


def test_audit_is_newest_first_with_a_limit(api, aws):
    runs, decisions = aws
    base = datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc)
    for i, d in enumerate(["APPROVE", "REJECT", "EDIT"]):
        decisions.put({"run_id": finished_run(runs), "timestamp": decision_timestamp(base + timedelta(minutes=i)),
                       "operator_name": "Dana", "decision": d, "plan_hash": "h", "final_status": "ALL_GREEN",
                       "scenario_id": "test_day", "note": None, "edits": None})
    status, body = api("GET /audit")
    assert status == 200 and [d["decision"] for d in body["decisions"]] == ["EDIT", "REJECT", "APPROVE"]
    status, body = api("GET /audit", params={"limit": "2"})
    assert [d["decision"] for d in body["decisions"]] == ["EDIT", "REJECT"]


@pytest.mark.parametrize("limit", ["0", "201", "abc", "-1"])
def test_audit_bad_limits(api, limit):
    status, body = api("GET /audit", params={"limit": limit})
    assert status == 400 and body["error"]["code"] == "BAD_REQUEST"


def test_audit_empty(api):
    assert api("GET /audit") == (200, {"decisions": []})
