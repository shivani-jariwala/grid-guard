"""TEMPORARY stand-in for the Bedrock agent worker. Owner: P3. Replaced by P2's agent_worker.

It does NOT call Bedrock. It plays the same tool sequence a real agent would
(get_forecast -> get_resources -> simulate ... -> submit_plan), using simple rules
and the REAL simulator, and writes the same run record (C5). That lets the dashboard's
live "Agent run" screen and the whole start -> poll -> done flow work end to end
before the real agent exists.

Every run it touches gets the flag STUB_AGENT, and the briefing says so, so nobody
mistakes it for the real agent.

Simple rules (least disruptive first):
1. try the no-action plan;
2. add battery discharge in the worst hours, as much as power and stored energy allow;
3. add demand response programs in the hours still short, then refill the battery.
If no attempt is all GREEN, it submits the best valid attempt as CANNOT_MEET_TARGET.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional

from shared.briefing import ALERT_LABEL, build_actions
from shared.config import get_settings
from shared.data import DataError, NotFound, Store, get_store, load_forecast, load_scenario
from shared.models import Plan, Scenario
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

STUB_FLAG = {"code": "STUB_AGENT", "detail": "Placeholder worker: simple rules, no Bedrock call. Replaced by the real agent (P2)."}


# ---------------------------------------------------------------------------
# Simple planning rules
# ---------------------------------------------------------------------------


def _not_green_worst_first(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    return sorted((h for h in result["hours"] if h["status"] != "GREEN"), key=lambda h: h["margin_pct"])


def battery_fill(scenario: Scenario, result: Dict[str, Any], target: float) -> List[Dict[str, Any]]:
    """Battery MW per short hour, worst hour first. `result` must have no battery in it."""
    remaining = scenario.battery.usable_mwh
    rows = []
    for h in _not_green_worst_first(result):
        need = math.ceil(h["demand_net"] * (1 + target / 100) - h["supply"]) + 1  # +1 MW cushion for rounding
        mw = min(need, int(scenario.battery.power_mw), math.floor(remaining))
        if mw > 0:
            rows.append({"hour": h["hour"], "mw": mw})
            remaining -= mw
    return sorted(rows, key=lambda r: r["hour"])


def dr_fill(scenario: Scenario, result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Each program (biggest first) runs in the worst short hours inside its allowed window."""
    short = [h["hour"] for h in _not_green_worst_first(result)]
    plan = []
    for program in sorted(scenario.demand_response, key=lambda p: -p.mw):
        hours = [h for h in short if program.earliest <= h <= program.latest][: program.max_hours]
        if hours:
            plan.append({"name": program.name, "hours": sorted(hours)})
    return plan


# ---------------------------------------------------------------------------
# The stub "agent"
# ---------------------------------------------------------------------------


def run_stub_agent(
    run_id: str,
    runs: RunStore,
    store: Store,
    max_simulate_calls: int = 5,
    pause: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    run = runs.get(run_id)
    if run is None:
        logger.error("worker: run %s not found", run_id)
        return
    try:
        _play(run, runs, store, max_simulate_calls, lambda: sleep(pause))
    except (DataError, NotFound) as err:
        logger.error("worker: data problem on run %s: %s", run_id, err)
        runs.fail(run, "DATA_ERROR", "Stored scenario or forecast data is invalid.")
    except Exception:  # noqa: BLE001
        logger.exception("worker: run %s crashed", run_id)
        runs.fail(run, "INTERNAL_ERROR", "The agent stopped unexpectedly.")


def _play(run: Dict[str, Any], runs: RunStore, store: Store, max_sims: int, wait: Callable[[], None]) -> None:
    run["flags"].append(STUB_FLAG)
    target = float(run["margin_target_pct"])

    t = time.monotonic()
    _, scenario = load_scenario(store, run["scenario_id"])
    _, forecast = load_forecast(store, scenario.forecast_id)
    peak = max(forecast.hours, key=lambda h: h.p90)
    add_trace(run, "get_forecast", {}, f"P90 peak {peak.p90:,.0f} MW at hour {peak.hour}.", elapsed_ms(t))
    runs.save(run)
    wait()

    add_trace(
        run,
        "get_resources",
        {},
        f"Battery {scenario.battery.power_mw:,.0f} MW / {scenario.battery.usable_mwh:,.0f} MWh usable; "
        f"{len(scenario.demand_response)} demand response programs; margin target {target:g}%.",
        0,
    )
    runs.save(run)
    wait()

    def simulate(plan: Dict[str, Any]) -> Dict[str, Any]:
        start = time.monotonic()
        try:
            result = simulate_dict(scenario, forecast, plan, target)
        except ValueError as err:
            raise DataError(str(err)) from None
        add_simulation(run, plan, result)
        add_trace(run, "simulate", {"plan": plan}, summary_of(result), elapsed_ms(start))
        runs.save(run)
        wait()
        return result

    attempts = []  # (plan, result)
    plan: Dict[str, Any] = {}
    attempts.append((plan, simulate(plan)))
    if not attempts[-1][1]["all_green"] and len(attempts) < max_sims:
        plan = {"battery": battery_fill(scenario, attempts[0][1], target)}
        attempts.append((plan, simulate(plan)))
    if not attempts[-1][1]["all_green"] and len(attempts) + 2 <= max_sims and scenario.demand_response:
        dr = dr_fill(scenario, attempts[-1][1])
        if dr:
            dr_only = simulate({"demand_response": dr})
            plan = {"demand_response": dr, "battery": battery_fill(scenario, dr_only, target)}
            attempts.append((plan, simulate(plan)))

    green = next(((p, r) for p, r in attempts if r["all_green"]), None)
    if green:
        final_plan, final_result, status = green[0], green[1], "ALL_GREEN"
    else:
        final_plan, final_result = max(
            ((p, r) for p, r in attempts if r["valid"]), key=lambda pr: pr[1]["lowest_margin_pct"]
        )
        status = "CANNOT_MEET_TARGET"

    refused = check_submit(run, final_plan, status)
    add_trace(run, "submit_plan", {"status": status}, "Refused: " + refused if refused else f"Accepted as {status}.", 0)
    if refused:  # should never happen: the stub only submits plans it simulated
        runs.fail(run, "SUBMIT_REFUSED", refused)
        return

    parsed = Plan.from_dict(final_plan)
    run["final"] = {
        "status": status,
        "plan_hash": final_result["plan_hash"],
        "plan": parsed.to_dict(),
        "result": final_result,
        "briefing": {
            "status": status,
            "actions": build_actions(scenario, parsed),
            "rationale": [
                "STUB: this plan was made by simple rules, not by the Bedrock agent.",
                "It uses the battery first, then demand response, and never a public alert.",
                "Every attempt was checked by the real simulator.",
            ],
            "risks": [
                "STUB: no risk analysis yet. The real agent will explain the remaining risk.",
            ],
            "alert_text": None,
            "alert_label": ALERT_LABEL,
            "number_check": {"passed": True, "mismatches": []},
        },
    }
    run["status"] = STATUS_DONE
    runs.save(run)


def handler(event: Dict[str, Any], context: Any) -> None:
    """Invoked asynchronously by agent_start with {"run_id": ...}."""
    settings = get_settings()
    run_stub_agent(str(event.get("run_id", "")), get_run_store(), get_store(), settings.agent_max_simulate_calls)
