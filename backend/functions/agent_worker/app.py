"""Bedrock agent loop (Converse API + tools), max 5 simulate / 12 tool calls. Owner: P2 (P2-05 to P2-09).

This is the real agent. It has the same shape as agent_worker_stub (same run record C5,
same helpers, same final briefing shape, runs.save after every tool call), but instead of
rule-based planning it drives Amazon Bedrock's Converse API with tool use.

Tools the model can call:
  get_forecast  -> peak P90 and the 24 hourly P90 values.
  get_resources -> the scenario's resources (battery/precool/DR/alert/supply/target), no sources.
  simulate      -> runs the real simulator, records it (C4), returns a compact result.
  submit_plan   -> the C4 submit gate; on accept, writes run["final"] and stops.

The server owns correctness: every number the UI shows comes from a tool, plan hashes are
computed server-side in check_submit, and if the model runs out of steps without submitting,
the server submits the best valid simulated plan as CANNOT_MEET_TARGET.
"""

from __future__ import annotations

import json
import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from shared.briefing import ALERT_LABEL, build_actions
from shared.config import Settings, get_settings
from shared.data import DataError, NotFound, Store, get_store, load_forecast, load_scenario
from shared.models import Forecast, Plan, Scenario
from shared.runs import (
    STATUS_DONE,
    RunStore,
    add_simulation,
    add_trace,
    check_submit,
    elapsed_ms,
    get_run_store,
    summary_of,
)
from shared.simulator import simulate_dict

logger = logging.getLogger("gridguard")

# Default model if BEDROCK_MODEL_ID is empty (us cross-region inference profile).
DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-5-5"

ALERT_TEXT_MAX = 160
MAX_BULLETS = 3

NUMBER_CHECK_OFF = {"passed": None, "mismatches": [], "note": "number guard not enabled"}

SYSTEM_PROMPT = (
    "You are GridGuard, planning tomorrow's grid for Dana, a California grid operator during "
    "a heat wave. Your job is to make every hour GREEN (supply margin at or above the target) "
    "using the least disruptive actions first, in this order of preference: "
    "battery -> pre-cooling buildings -> demand response programs -> a public conservation "
    "alert as a last resort.\n\n"
    "Rules you must follow:\n"
    "- Always call get_forecast and get_resources first, before planning.\n"
    "- Never state a number you did not get from a tool result.\n"
    "- Always call simulate before submit_plan, and revise the plan if any hour is not GREEN.\n"
    "- If you cannot reach all-GREEN within the limits, submit your best valid plan with "
    "status CANNOT_MEET_TARGET and explain the risk.\n"
    "- Public alerts are drafts for a human to review and are never sent by you.\n"
    "- Keep rationale and risks to at most 3 short, plain-English bullets each.\n\n"
    "Call submit_plan once you are satisfied. The server checks every plan and hash."
)


# ---------------------------------------------------------------------------
# Tool schemas (Bedrock Converse toolConfig)
# ---------------------------------------------------------------------------

_HOURS_ARRAY = {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 23}}

_PLAN_SCHEMA = {
    "type": "object",
    "description": "A plan (contract C3). Any section may be omitted (means 'not used').",
    "properties": {
        "battery": {
            "type": "array",
            "description": "Battery discharge: one entry per hour.",
            "items": {
                "type": "object",
                "properties": {
                    "hour": {"type": "integer", "minimum": 0, "maximum": 23},
                    "mw": {"type": "number", "minimum": 0},
                },
                "required": ["hour", "mw"],
            },
        },
        "precool": {
            "type": "object",
            "description": "Pre-cool buildings: move load from from_hours to to_hours.",
            "properties": {
                "shift_mw": {"type": "number", "minimum": 0},
                "from_hours": _HOURS_ARRAY,
                "to_hours": _HOURS_ARRAY,
            },
        },
        "demand_response": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "hours": _HOURS_ARRAY},
                "required": ["name", "hours"],
            },
        },
        "public_alert": {
            "type": "object",
            "properties": {
                "use": {"type": "boolean"},
                "start_hour": {"type": "integer", "minimum": 0, "maximum": 23},
                "hours": {"type": "integer", "minimum": 0, "maximum": 23},
            },
        },
    },
}

_BULLETS = {"type": "array", "items": {"type": "string"}, "maxItems": MAX_BULLETS}

TOOL_CONFIG = {
    "tools": [
        {
            "toolSpec": {
                "name": "get_forecast",
                "description": "Tomorrow's demand forecast: peak P90 MW and hour, plus 24 hourly P90 values.",
                "inputSchema": {"json": {"type": "object", "properties": {}}},
            }
        },
        {
            "toolSpec": {
                "name": "get_resources",
                "description": "The resources available (battery, pre-cool, demand response, public alert, supply) and the margin target.",
                "inputSchema": {"json": {"type": "object", "properties": {}}},
            }
        },
        {
            "toolSpec": {
                "name": "simulate",
                "description": "Run the plan through the simulator and get each hour's safety margin and status.",
                "inputSchema": {"json": {"type": "object", "properties": {"plan": _PLAN_SCHEMA}, "required": ["plan"]}},
            }
        },
        {
            "toolSpec": {
                "name": "submit_plan",
                "description": "Submit the final plan for the operator. Only submit a plan you have simulated.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "plan": _PLAN_SCHEMA,
                            "status": {"type": "string", "enum": ["ALL_GREEN", "CANNOT_MEET_TARGET"]},
                            "rationale": _BULLETS,
                            "risks": _BULLETS,
                            "alert_text": {
                                "type": "string",
                                "description": f"Draft public alert, at most {ALERT_TEXT_MAX} characters, or omit if no alert.",
                            },
                        },
                        "required": ["plan", "status", "rationale", "risks"],
                    }
                },
            }
        },
    ]
}


# ---------------------------------------------------------------------------
# Tool implementations (compact JSON for the model)
# ---------------------------------------------------------------------------


def forecast_tool_result(forecast: Forecast) -> Dict[str, Any]:
    peak = max(forecast.hours, key=lambda h: h.p90)
    return {
        "peak": {"hour": peak.hour, "p90_mw": round(peak.p90)},
        "hourly_p90_mw": [{"hour": h.hour, "p90": round(h.p90)} for h in forecast.hours],
    }


def resources_tool_result(scenario: Scenario) -> Dict[str, Any]:
    """The scenario resources, without the `sources` map."""
    b = scenario.battery
    pc = scenario.precool
    return {
        "margin_target_pct": scenario.margin_target_pct,
        "supply": {
            "firm_mw": scenario.supply.firm_mw,
            "solar_mw_by_hour": [round(x) for x in scenario.supply.solar_mw_by_hour],
            "wind_mw_by_hour": [round(x) for x in scenario.supply.wind_mw_by_hour],
        },
        "battery": {
            "power_mw": b.power_mw,
            "energy_mwh": b.energy_mwh,
            "start_soc_pct": b.start_soc_pct,
            "efficiency": b.efficiency,
            "usable_mwh": round(b.usable_mwh),
        },
        "precool": {
            "allowed_from_hours": list(pc.allowed_from_hours),
            "allowed_to_hours": list(pc.allowed_to_hours),
            "max_shift_mw": pc.max_shift_mw,
            "rebound_pct": pc.rebound_pct,
        },
        "demand_response": [
            {"name": p.name, "mw": p.mw, "max_hours": p.max_hours, "earliest": p.earliest, "latest": p.latest}
            for p in scenario.demand_response
        ],
        "public_alert": {
            "reduction_mw": {
                "low": scenario.public_alert.reduction_mw.low,
                "likely": scenario.public_alert.reduction_mw.likely,
                "high": scenario.public_alert.reduction_mw.high,
            },
            "max_hours": scenario.public_alert.max_hours,
            "alerts_left_this_season": scenario.public_alert.alerts_left_this_season,
        },
    }


def compact_result(result: Dict[str, Any], target: float) -> Dict[str, Any]:
    """A trimmed simulate result: summary plus only the non-GREEN hours with the shortfall."""
    out: Dict[str, Any] = {
        "valid": result["valid"],
        "violations": result.get("violations", []),
        "all_green": result.get("all_green", False),
        "lowest_margin_pct": result.get("lowest_margin_pct"),
        "lowest_margin_hour": result.get("lowest_margin_hour"),
        "red_hours": result.get("red_hours", []),
        "yellow_hours": result.get("yellow_hours", []),
        "disruption_score": result.get("disruption_score", 0),
    }
    not_green = []
    for h in result.get("hours", []):
        if h["status"] != "GREEN":
            # MW of extra supply (or demand cut) needed to reach the target margin.
            needed = h["demand_net"] * (1 + target / 100) - h["supply"]
            not_green.append(
                {
                    "hour": h["hour"],
                    "status": h["status"],
                    "margin_pct": h["margin_pct"],
                    "shortfall_mw": max(0, math.ceil(needed)),
                }
            )
    out["not_green_hours"] = not_green
    return out


# ---------------------------------------------------------------------------
# Bedrock client
# ---------------------------------------------------------------------------


def get_bedrock_client(settings: Settings) -> Any:
    import boto3

    return boto3.client("bedrock-runtime", region_name=settings.aws_region)


def _bedrock_error(err: Exception) -> str:
    """A short, safe message for a Bedrock failure."""
    try:
        from botocore.exceptions import ClientError

        if isinstance(err, ClientError):
            e = err.response.get("Error", {})
            return f"{e.get('Code', 'ClientError')}: {e.get('Message', str(err))}"
    except Exception:  # noqa: BLE001
        pass
    return f"{type(err).__name__}: {err}"


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------


def run_real_agent(
    run_id: str,
    runs: RunStore,
    store: Store,
    bedrock: Any = None,
    settings: Optional[Settings] = None,
) -> None:
    run = runs.get(run_id)
    if run is None:
        logger.error("worker: run %s not found", run_id)
        return
    settings = settings or get_settings()
    if bedrock is None:
        bedrock = get_bedrock_client(settings)
    try:
        _play(run, runs, store, bedrock, settings)
    except (DataError, NotFound) as err:
        logger.error("worker: data problem on run %s: %s", run_id, err)
        runs.fail(run, "DATA_ERROR", "Stored scenario or forecast data is invalid.")
    except Exception:  # noqa: BLE001
        logger.exception("worker: run %s crashed", run_id)
        runs.fail(run, "INTERNAL_ERROR", "The agent stopped unexpectedly.")


def _model_id(settings: Settings) -> str:
    return settings.bedrock_model_id or DEFAULT_MODEL_ID


def _clean_assistant_content(content: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop trailing reasoning/thinking blocks so the last block is text or toolUse.

    Some models (extended thinking) can return a message whose last block is a
    reasoningContent block. Bedrock rejects an assistant turn that ends on reasoning
    when it's sent back as history, so we trim trailing non-text/non-toolUse blocks.
    Returns a possibly-empty list (caller skips appending an empty message).
    """
    cleaned = list(content)
    while cleaned and not ("text" in cleaned[-1] or "toolUse" in cleaned[-1]):
        cleaned.pop()
    return cleaned


NUDGE_TEXT = "Continue. Use the tools, and call submit_plan when done (CANNOT_MEET_TARGET is allowed)."


def _play(run: Dict[str, Any], runs: RunStore, store: Store, bedrock: Any, settings: Settings) -> None:
    run.setdefault("agent_notes", [])
    target = float(run["margin_target_pct"])
    _, scenario = load_scenario(store, run["scenario_id"])
    _, forecast = load_forecast(store, scenario.forecast_id)

    max_sims = settings.agent_max_simulate_calls
    max_tools = settings.agent_max_tool_calls

    # Keep every valid simulated plan so the server can submit the best one if needed.
    valid_attempts: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []

    def record_simulation(plan: Dict[str, Any]) -> Dict[str, Any]:
        start = time.monotonic()
        try:
            result = simulate_dict(scenario, forecast, plan, target)
        except ValueError as err:
            raise DataError(str(err)) from None
        add_simulation(run, plan, result)
        add_trace(run, "simulate", {"plan": plan}, summary_of(result), elapsed_ms(start))
        runs.save(run)
        if result.get("valid"):
            valid_attempts.append((plan, result))
        return result

    def finalize(plan: Dict[str, Any], result: Dict[str, Any], status: str,
                 rationale: List[str], risks: List[str], alert_text: Optional[str]) -> None:
        parsed = Plan.from_dict(plan)
        run["final"] = {
            "status": status,
            "plan_hash": result["plan_hash"],
            "plan": parsed.to_dict(),
            "result": result,
            "briefing": {
                "status": status,
                "actions": build_actions(scenario, parsed),
                "rationale": rationale[:MAX_BULLETS],
                "risks": risks[:MAX_BULLETS],
                "alert_text": alert_text,
                "alert_label": ALERT_LABEL,
                "number_check": dict(NUMBER_CHECK_OFF),
            },
        }
        run["status"] = STATUS_DONE
        runs.save(run)

    messages: List[Dict[str, Any]] = [
        {"role": "user", "content": [{"text": "Plan tomorrow's grid. Start by calling get_forecast and get_resources."}]}
    ]

    while True:
        if run["counts"]["tool_calls"] >= max_tools:
            _server_submit_best(run, runs, scenario, valid_attempts, finalize,
                                 reason=f"tool-call limit ({max_tools}) reached")
            return

        try:
            response = bedrock.converse(
                modelId=_model_id(settings),
                system=[{"text": SYSTEM_PROMPT}],
                messages=messages,
                toolConfig=TOOL_CONFIG,
                inferenceConfig={"maxTokens": 8000},
            )
        except Exception as err:  # noqa: BLE001 - any Bedrock failure fails the run
            runs.fail(run, "BEDROCK_ERROR", _bedrock_error(err))
            return

        output_message = response.get("output", {}).get("message", {})
        raw_content = output_message.get("content", [])
        content = _clean_assistant_content(raw_content)
        stop_reason = response.get("stopReason")

        # Append the cleaned assistant message only if something is left after trimming.
        if content:
            messages.append({"role": "assistant", "content": content})
            for block in content:
                if "text" in block and block["text"].strip():
                    run["agent_notes"].append(block["text"].strip())
            runs.save(run)

        tool_uses = [b["toolUse"] for b in content if "toolUse" in b]
        if not tool_uses:
            # No tool call this turn. Whether the model stopped (end_turn / max_tokens) or
            # returned only reasoning, nudge it to keep using the tools. The nudge counts
            # toward the tool-call budget, so the loop can't run forever.
            messages.append({"role": "user", "content": [{"text": NUDGE_TEXT}]})
            add_trace(run, "nudge", {"stop_reason": stop_reason}, NUDGE_TEXT, 0)
            runs.save(run)
            continue

        tool_results: List[Dict[str, Any]] = []
        for tool_use in tool_uses:
            name = tool_use["name"]
            tool_id = tool_use["toolUseId"]
            tool_input = tool_use.get("input", {}) or {}

            if name == "get_forecast":
                payload = forecast_tool_result(forecast)
                peak = payload["peak"]
                add_trace(run, "get_forecast", {}, f"P90 peak {peak['p90_mw']:,} MW at hour {peak['hour']}.", 0)
            elif name == "get_resources":
                payload = resources_tool_result(scenario)
                add_trace(run, "get_resources", {},
                          f"Battery {scenario.battery.power_mw:,.0f} MW; "
                          f"{len(scenario.demand_response)} DR programs; target {target:g}%.", 0)
            elif name == "simulate":
                if run["counts"]["simulate_calls"] >= max_sims:
                    payload = {"error": f"simulate limit reached ({max_sims}). Submit your best plan now."}
                    add_trace(run, "simulate", {"plan": tool_input.get("plan")}, "Refused: simulate limit reached.", 0)
                    runs.save(run)
                else:
                    result = record_simulation(tool_input.get("plan", {}))
                    payload = compact_result(result, target)
            elif name == "submit_plan":
                done = _handle_submit(run, runs, scenario, tool_input, finalize)
                if done is True:
                    return
                payload = {"accepted": False, "reason": done}  # `done` is the refusal reason
            else:
                payload = {"error": f"unknown tool {name}"}

            tool_results.append(
                {"toolResult": {"toolUseId": tool_id, "content": [{"json": payload}]}}
            )

        messages.append({"role": "user", "content": tool_results})


def _handle_submit(
    run: Dict[str, Any],
    runs: RunStore,
    scenario: Scenario,
    tool_input: Dict[str, Any],
    finalize: Callable[..., None],
) -> Any:
    """Run the C4 submit gate. Returns True on success, else the refusal reason (str)."""
    plan = tool_input.get("plan", {})
    status = tool_input.get("status", "")
    rationale = [str(x) for x in (tool_input.get("rationale") or [])]
    risks = [str(x) for x in (tool_input.get("risks") or [])]
    alert_text = tool_input.get("alert_text")
    if isinstance(alert_text, str):
        alert_text = alert_text[:ALERT_TEXT_MAX]
    else:
        alert_text = None

    refused = check_submit(run, plan, status)
    add_trace(run, "submit_plan", {"status": status},
              "Refused: " + refused if refused else f"Accepted as {status}.", 0)
    runs.save(run)
    if refused:
        return refused

    # Pull the recorded result for this exact plan (hash computed server-side).
    plan_hash = Plan.from_dict(plan).plan_hash()
    match = next(s for s in run["simulations"] if s.get("plan_hash") == plan_hash)
    finalize(plan, match["result"], status, rationale, risks, alert_text)
    return True


def _server_submit_best(
    run: Dict[str, Any],
    runs: RunStore,
    scenario: Scenario,
    valid_attempts: List[Tuple[Dict[str, Any], Dict[str, Any]]],
    finalize: Callable[..., None],
    reason: str,
) -> None:
    """Fallback: submit the best valid simulated plan as CANNOT_MEET_TARGET."""
    run.setdefault("flags", []).append({"code": "LOOP_LIMIT", "detail": reason})
    if not valid_attempts:
        runs.fail(run, "NO_VALID_PLAN", f"The agent produced no valid plan ({reason}).")
        return
    # Highest lowest-margin wins; an all-green plan would already have been submitted.
    plan, result = max(valid_attempts, key=lambda pr: pr[1]["lowest_margin_pct"])
    status = "ALL_GREEN" if result.get("all_green") else "CANNOT_MEET_TARGET"
    finalize(
        plan,
        result,
        status,
        rationale=[
            "The agent reached a step limit, so the server submitted the best valid plan it simulated.",
            f"Lowest margin {result['lowest_margin_pct']}% at hour {result['lowest_margin_hour']}.",
        ],
        risks=["This plan was chosen automatically and may not be the safest option available."],
        alert_text=None,
    )


def handler(event: Dict[str, Any], context: Any) -> None:
    """Invoked asynchronously by agent_start with {"run_id": ...}."""
    run_real_agent(str(event.get("run_id", "")), get_run_store(), get_store())
