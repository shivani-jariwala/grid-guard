"""Tests for the real Bedrock agent worker, using a scripted fake Converse client.

No AWS is touched: the Bedrock client is a fake that replays a list of scripted responses,
and the run store is a tiny in-memory dict. Covers the happy path (reaches ALL_GREEN and
submits), a refused submit (unsimulated plan, then a corrected one), and the step limit
(server submits the best valid plan as CANNOT_MEET_TARGET).
"""

from __future__ import annotations

import copy
import json

import pytest

from shared.config import load_settings
from shared.data import LocalStore
from shared.models import Plan

from conftest import BASE_SCENARIO, forecast_dict
from test_handlers import load_handler_module

worker = load_handler_module("agent_worker")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class InMemoryRunStore:
    """Minimal RunStore: just holds one run dict in memory."""

    def __init__(self):
        self.runs = {}
        self.saves = 0

    def create(self, run):
        self.runs[run["run_id"]] = copy.deepcopy(run)

    def get(self, run_id):
        run = self.runs.get(run_id)
        return copy.deepcopy(run) if run is not None else None

    def save(self, run):
        self.saves += 1
        self.runs[run["run_id"]] = copy.deepcopy(run)

    def fail(self, run, code, message):
        run["status"] = "failed"
        run["error"] = {"code": code, "message": message}
        self.save(run)


def tool_use(name, tool_id, payload):
    return {"toolUse": {"toolUseId": tool_id, "name": name, "input": payload}}


def assistant_turn(blocks, stop_reason="tool_use"):
    return {"output": {"message": {"role": "assistant", "content": blocks}}, "stopReason": stop_reason}


def reasoning_block(text="thinking about the plan"):
    return {"reasoningContent": {"reasoningText": {"text": text}}}


class FakeBedrock:
    """Replays scripted converse responses; records the modelId and message turns."""

    def __init__(self, responses, error=None):
        self._responses = list(responses)
        self._error = error
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        if not self._responses:
            raise AssertionError("FakeBedrock ran out of scripted responses")
        return self._responses.pop(0)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings():
    return load_settings({"BEDROCK_MODEL_ID": "test-model", "AGENT_MAX_SIMULATE_CALLS": "5", "AGENT_MAX_TOOL_CALLS": "12"})


@pytest.fixture
def store(tmp_path):
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "forecasts").mkdir()
    (tmp_path / "scenarios" / "test_day.json").write_text(json.dumps(BASE_SCENARIO))
    (tmp_path / "forecasts" / "test_day.json").write_text(json.dumps(forecast_dict()))
    return LocalStore(tmp_path / "scenarios", tmp_path / "forecasts")


@pytest.fixture
def new_run():
    from shared.runs import new_run as _new_run

    return _new_run


def make_green_plan():
    """A plan that is all-GREEN for BASE_SCENARIO (verified against the real simulator).

    Each evening hour (17-20) needs ~1,060 MW of relief to clear the 6% target. Program B
    (200 MW) runs all four hours, Program A (400 MW) runs three, and the battery fills the
    rest (860 MW at hour 17 which lacks Program A, 460 MW at the others) -- 2,240 MWh total,
    within the 3,000 MWh usable energy.
    """
    return {
        "battery": [
            {"hour": 17, "mw": 860},
            {"hour": 18, "mw": 460},
            {"hour": 19, "mw": 460},
            {"hour": 20, "mw": 460},
        ],
        "demand_response": [
            {"name": "Program A", "hours": [18, 19, 20]},
            {"name": "Program B", "hours": [17, 18, 19, 20]},
        ],
    }


# ---------------------------------------------------------------------------
# Happy path: get_forecast -> get_resources -> simulate -> submit (ALL_GREEN)
# ---------------------------------------------------------------------------


def test_happy_path_reaches_all_green(store, settings, new_run):
    runs = InMemoryRunStore()
    run = new_run("test_day", 6, "test-model")
    runs.create(run)

    plan = make_green_plan()
    script = [
        assistant_turn([{"text": "Let me check the forecast and resources."},
                        tool_use("get_forecast", "t1", {})]),
        assistant_turn([tool_use("get_resources", "t2", {})]),
        assistant_turn([{"text": "I'll try battery plus one DR program."},
                        tool_use("simulate", "t3", {"plan": plan})]),
        assistant_turn([tool_use("submit_plan", "t4", {
            "plan": plan,
            "status": "ALL_GREEN",
            "rationale": ["Battery covers the evening peak", "One DR program for the last hour"],
            "risks": ["Battery energy is nearly fully used"],
        })]),
    ]
    bedrock = FakeBedrock(script)
    worker.run_real_agent(run["run_id"], runs, store, bedrock=bedrock, settings=settings)

    done = runs.get(run["run_id"])
    assert done["status"] == "done"
    assert done["final"]["status"] == "ALL_GREEN"
    assert done["final"]["result"]["all_green"] is True
    # final plan must be one that was simulated (hash matches)
    assert done["final"]["plan_hash"] in {s["plan_hash"] for s in done["simulations"]}
    # briefing shape matches the stub
    b = done["final"]["briefing"]
    assert b["alert_label"] == "Draft only. Not sent."
    assert b["number_check"] == {"passed": None, "mismatches": [], "note": "number guard not enabled"}
    assert b["alert_text"] is None
    assert len(b["rationale"]) <= 3 and len(b["risks"]) <= 3
    assert {a["action"] for a in b["actions"]} == {"battery", "demand_response"}
    # agent text replies are saved for the UI
    assert any("forecast" in note.lower() for note in done["agent_notes"])
    # correct model id used
    assert bedrock.calls[0]["modelId"] == "test-model"
    # no STUB flag
    assert all(f["code"] != "STUB_AGENT" for f in done["flags"])


# ---------------------------------------------------------------------------
# Refused submit: an unsimulated plan is refused, the model fixes it
# ---------------------------------------------------------------------------


def test_refused_submit_is_returned_then_fixed(store, settings, new_run):
    runs = InMemoryRunStore()
    run = new_run("test_day", 6, "test-model")
    runs.create(run)

    plan = make_green_plan()
    other_plan = {"battery": [{"hour": 18, "mw": 500}]}  # never simulated
    script = [
        assistant_turn([tool_use("get_forecast", "t1", {})]),
        assistant_turn([tool_use("get_resources", "t2", {})]),
        # Submit a plan that was never simulated -> refused.
        assistant_turn([tool_use("submit_plan", "t3", {
            "plan": other_plan, "status": "ALL_GREEN", "rationale": ["x"], "risks": ["y"]})]),
        # Now simulate the real plan.
        assistant_turn([tool_use("simulate", "t4", {"plan": plan})]),
        # And submit it correctly.
        assistant_turn([tool_use("submit_plan", "t5", {
            "plan": plan, "status": "ALL_GREEN",
            "rationale": ["Battery + DR"], "risks": ["tight energy"]})]),
    ]
    bedrock = FakeBedrock(script)
    worker.run_real_agent(run["run_id"], runs, store, bedrock=bedrock, settings=settings)

    done = runs.get(run["run_id"])
    assert done["status"] == "done"
    assert done["final"]["status"] == "ALL_GREEN"
    # The refusal was recorded in the trace and fed back to the model.
    submit_steps = [t for t in done["trace"] if t["tool"] == "submit_plan"]
    assert len(submit_steps) == 2
    assert submit_steps[0]["output_summary"].startswith("Refused:")
    assert submit_steps[1]["output_summary"].startswith("Accepted")
    # The refusal reason went back to the model as a tool result in a later converse call.
    refusals = [
        block["toolResult"]["content"][0]["json"]
        for call in bedrock.calls
        for msg in call["messages"]
        if msg["role"] == "user"
        for block in msg["content"]
        if "toolResult" in block and block["toolResult"]["content"][0]["json"].get("accepted") is False
    ]
    assert refusals, "the refusal was never sent back to the model"
    assert "never simulated" in refusals[0]["reason"]


# ---------------------------------------------------------------------------
# Step limit: server submits the best valid plan as CANNOT_MEET_TARGET
# ---------------------------------------------------------------------------


def test_tool_limit_server_submits_best_plan(store, new_run):
    # Tight budget: 3 tool calls, so the model can't finish before the server steps in.
    settings = load_settings({"BEDROCK_MODEL_ID": "test-model", "AGENT_MAX_TOOL_CALLS": "3", "AGENT_MAX_SIMULATE_CALLS": "5"})
    runs = InMemoryRunStore()
    run = new_run("test_day", 6, "test-model")
    runs.create(run)

    weak_plan = {"battery": [{"hour": 18, "mw": 500}]}  # valid, not all-green
    script = [
        assistant_turn([tool_use("get_forecast", "t1", {})]),
        assistant_turn([tool_use("get_resources", "t2", {})]),
        assistant_turn([tool_use("simulate", "t3", {"plan": weak_plan})]),
        # The model would keep going, but the tool-call budget (3) is now spent.
        assistant_turn([tool_use("simulate", "t4", {"plan": weak_plan})]),
    ]
    bedrock = FakeBedrock(script)
    worker.run_real_agent(run["run_id"], runs, store, bedrock=bedrock, settings=settings)

    done = runs.get(run["run_id"])
    assert done["status"] == "done"
    assert done["final"]["status"] == "CANNOT_MEET_TARGET"
    assert done["final"]["plan_hash"] in {s["plan_hash"] for s in done["simulations"]}
    assert any(f["code"] == "LOOP_LIMIT" for f in done["flags"])
    assert done["counts"]["tool_calls"] == 3  # stopped at the limit


# ---------------------------------------------------------------------------
# Bedrock error -> run fails with BEDROCK_ERROR
# ---------------------------------------------------------------------------


def test_reasoning_only_turn_is_nudged_then_continues(store, settings, new_run):
    runs = InMemoryRunStore()
    run = new_run("test_day", 6, "test-model")
    runs.create(run)

    plan = make_green_plan()
    script = [
        assistant_turn([tool_use("get_forecast", "t1", {})]),
        assistant_turn([tool_use("get_resources", "t2", {})]),
        # A turn that is ONLY a reasoning block and hit the token cap: nothing to run.
        assistant_turn([reasoning_block("I need to think longer")], stop_reason="max_tokens"),
        # After the nudge, the model simulates and submits.
        assistant_turn([tool_use("simulate", "t3", {"plan": plan})]),
        assistant_turn([tool_use("submit_plan", "t4", {
            "plan": plan, "status": "ALL_GREEN",
            "rationale": ["Battery + DR"], "risks": ["tight energy"]})]),
    ]
    bedrock = FakeBedrock(script)
    worker.run_real_agent(run["run_id"], runs, store, bedrock=bedrock, settings=settings)

    done = runs.get(run["run_id"])
    assert done["status"] == "done"
    assert done["final"]["status"] == "ALL_GREEN"
    # A nudge was recorded (and counts toward the tool budget).
    nudges = [t for t in done["trace"] if t["tool"] == "nudge"]
    assert len(nudges) == 1 and nudges[0]["input"]["stop_reason"] == "max_tokens"
    # The reasoning-only turn was not appended as history; a nudge user message was sent.
    nudge_sent = any(
        msg["role"] == "user"
        and msg["content"]
        and "text" in msg["content"][0]
        and "Continue." in msg["content"][0]["text"]
        for call in bedrock.calls
        for msg in call["messages"]
    )
    assert nudge_sent, "the nudge user message was never sent to the model"
    # The reasoning block never became an assistant message with empty/invalid content.
    for call in bedrock.calls:
        for msg in call["messages"]:
            if msg["role"] == "assistant":
                assert all("reasoningContent" not in b for b in msg["content"])
    # maxTokens raised to 8000.
    assert bedrock.calls[0]["inferenceConfig"]["maxTokens"] == 8000


def test_bedrock_error_fails_the_run(store, settings, new_run):
    runs = InMemoryRunStore()
    run = new_run("test_day", 6, "test-model")
    runs.create(run)
    bedrock = FakeBedrock([], error=RuntimeError("bedrock exploded"))
    worker.run_real_agent(run["run_id"], runs, store, bedrock=bedrock, settings=settings)

    done = runs.get(run["run_id"])
    assert done["status"] == "failed"
    assert done["error"]["code"] == "BEDROCK_ERROR"
