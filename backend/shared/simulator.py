"""Plain-code simulator ("referee"): scenario + forecast + plan -> hourly safety result.

Contracts C2-C4 in docs/contracts.md. Pure Python, no AI. Owner: P3.

STATUS: STUB (P3-15). The function signatures and the result shape are final.
The numbers are FAKE. They let P2 build the agent tools and P4 build the UI
before the real math lands in P3-08. Every result says engine="stub".

Fake rule:
- no-action plan -> hours 18-19 RED, hours 17 and 20 YELLOW, everything else GREEN
- any action      -> every hour GREEN
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import (
    GREEN,
    RED,
    YELLOW,
    Forecast,
    HourResult,
    Plan,
    Scenario,
    ShapeError,
    SimResult,
    Violation,
)

ENGINE = "stub"

# Disruption score weights (C3). Lower is better.
SCORE_BATTERY = 1
SCORE_PRECOOL = 2
SCORE_DR_PER_PROGRAM = 3
SCORE_PUBLIC_ALERT = 10

_STUB_RED_HOURS = (18, 19)
_STUB_YELLOW_HOURS = (17, 20)


def disruption_score(plan: Plan) -> int:
    """Battery 1, pre-cooling 2, demand response 3 per program, public alert 10."""
    score = 0
    if plan.battery:
        score += SCORE_BATTERY
    if plan.precool.shift_mw > 0:
        score += SCORE_PRECOOL
    score += SCORE_DR_PER_PROGRAM * len(plan.demand_response)
    if plan.public_alert.use:
        score += SCORE_PUBLIC_ALERT
    return score


def simulate(
    scenario: Scenario,
    forecast: Forecast,
    plan: Plan,
    margin_target_pct: Optional[float] = None,
) -> SimResult:
    """Run the plan against the scenario and forecast. STUB: returns fake margins."""
    target = scenario.margin_target_pct if margin_target_pct is None else margin_target_pct
    battery_by_hour = {a.hour: a.mw for a in plan.battery}
    no_action = plan.is_no_action

    hours: List[HourResult] = []
    for fh in forecast.hours:
        h = fh.hour
        if no_action and h in _STUB_RED_HOURS:
            status, margin = RED, -2.0
        elif no_action and h in _STUB_YELLOW_HOURS:
            status, margin = YELLOW, target / 2
        else:
            status, margin = GREEN, target + 2.0
        demand_net = fh.p90
        hours.append(
            HourResult(
                hour=h,
                demand_p90=fh.p90,
                precool_delta=0.0,
                dr_mw=0.0,
                alert_mw=0.0,
                demand_net=demand_net,
                firm_mw=scenario.supply.firm_mw,
                solar_mw=scenario.supply.solar_mw_by_hour[h],
                wind_mw=scenario.supply.wind_mw_by_hour[h],
                battery_mw=battery_by_hour.get(h, 0.0),
                supply=demand_net * (1 + margin / 100),  # fake: chosen to match the fake margin
                margin_pct=margin,
                status=status,
            )
        )

    lowest = min(hours, key=lambda r: r.margin_pct)
    return SimResult(
        engine=ENGINE,
        plan_hash=plan.plan_hash(),
        valid=True,
        violations=(),
        margin_target_pct=target,
        hours=tuple(hours),
        all_green=all(r.status == GREEN for r in hours),
        lowest_margin_pct=lowest.margin_pct,
        lowest_margin_hour=lowest.hour,
        red_hours=tuple(r.hour for r in hours if r.status == RED),
        yellow_hours=tuple(r.hour for r in hours if r.status == YELLOW),
        disruption_score=disruption_score(plan),
    )


def simulate_dict(
    scenario: Scenario,
    forecast: Forecast,
    plan: Any,
    margin_target_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """JSON in, JSON out, for the agent tool and POST /simulate.

    Never raises on a bad plan: a shape error becomes an invalid result with a
    PLAN_SHAPE violation and plan_hash = None (C4).
    """
    try:
        parsed = Plan.from_dict(plan)
    except ShapeError as err:
        target = scenario.margin_target_pct if margin_target_pct is None else margin_target_pct
        return SimResult(
            engine=ENGINE,
            plan_hash=None,
            valid=False,
            violations=(Violation(code="PLAN_SHAPE", path=err.path, message=err.message),),
            margin_target_pct=target,
            hours=(),
            all_green=False,
            lowest_margin_pct=None,
            lowest_margin_hour=None,
            red_hours=(),
            yellow_hours=(),
            disruption_score=0,
        ).to_dict()
    return simulate(scenario, forecast, parsed, margin_target_pct).to_dict()
