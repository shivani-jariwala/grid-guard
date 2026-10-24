"""Plain-code simulator ("referee"): scenario + forecast + plan -> hourly safety result.

Contracts C2-C4 in docs/contracts.md. Pure Python, no AI, no AWS. Owner: P3 (P3-08).

The agent proposes plans. This module decides whether they are allowed and how
safe each hour is. It plans for the high case (P90 demand) and counts the
public alert at its LOW expected reduction, to stay conservative.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import (
    GREEN,
    HOURS_PER_DAY,
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

ENGINE = "v1"

# Disruption score weights (C3). Lower is better.
SCORE_BATTERY = 1
SCORE_PRECOOL = 2
SCORE_DR_PER_PROGRAM = 3
SCORE_PUBLIC_ALERT = 10

# Float tolerance for limit checks, so 0.1 + 0.2 style rounding never flips a result.
_EPS = 1e-6


# ---------------------------------------------------------------------------
# Validation (C3 rules). Returns every violation, not just the first one.
# ---------------------------------------------------------------------------


def _in_day(hour: int) -> bool:
    return 0 <= hour < HOURS_PER_DAY


def _fmt(mw: float) -> str:
    return f"{mw:,.0f}" if float(mw).is_integer() else f"{mw:,.1f}"


def validate(scenario: Scenario, plan: Plan) -> List[Violation]:
    """Check a normalized plan against what the scenario allows."""
    v: List[Violation] = []
    v += _validate_battery(scenario, plan)
    v += _validate_precool(scenario, plan)
    v += _validate_demand_response(scenario, plan)
    v += _validate_public_alert(scenario, plan)
    return v


def _validate_battery(scenario: Scenario, plan: Plan) -> List[Violation]:
    v: List[Violation] = []
    limits = scenario.battery
    seen = set()
    for i, action in enumerate(plan.battery):
        path = f"battery[{i}]"
        if not _in_day(action.hour):
            v.append(Violation("HOUR_OUT_OF_RANGE", f"{path}.hour", f"Hour {action.hour} is outside 0-23."))
        if action.hour in seen:
            v.append(
                Violation("BATTERY_DUPLICATE_HOUR", f"{path}.hour", f"Hour {action.hour} has more than one battery entry.")
            )
        seen.add(action.hour)
        if action.mw > limits.power_mw + _EPS:
            v.append(
                Violation(
                    "BATTERY_POWER_EXCEEDED",
                    f"{path}.mw",
                    f"Hour {action.hour}: {_fmt(action.mw)} MW is above the {_fmt(limits.power_mw)} MW battery limit.",
                )
            )
    total_mwh = sum(a.mw for a in plan.battery)  # each entry is 1 hour, so MW == MWh
    if total_mwh > limits.usable_mwh + _EPS:
        v.append(
            Violation(
                "BATTERY_ENERGY_EXCEEDED",
                "battery",
                f"Total discharge {_fmt(total_mwh)} MWh is above the {_fmt(limits.usable_mwh)} MWh usable today "
                f"(energy x start charge x efficiency).",
            )
        )
    return v


def _validate_precool(scenario: Scenario, plan: Plan) -> List[Violation]:
    v: List[Violation] = []
    pc = plan.precool
    limits = scenario.precool
    if pc.shift_mw == 0:
        return v  # not used (normalization already cleared the hour lists)

    if pc.shift_mw > limits.max_shift_mw + _EPS:
        v.append(
            Violation(
                "PRECOOL_SHIFT_EXCEEDED",
                "precool.shift_mw",
                f"{_fmt(pc.shift_mw)} MW is above the {_fmt(limits.max_shift_mw)} MW pre-cooling limit.",
            )
        )
    if not pc.from_hours or not pc.to_hours:
        v.append(
            Violation(
                "PRECOOL_HOURS_MISSING",
                "precool",
                "Pre-cooling needs at least one from_hours hour and one to_hours hour.",
            )
        )

    for name, hours, allowed in (
        ("from_hours", pc.from_hours, limits.allowed_from_hours),
        ("to_hours", pc.to_hours, limits.allowed_to_hours),
    ):
        for h in hours:
            if not _in_day(h):
                v.append(Violation("HOUR_OUT_OF_RANGE", f"precool.{name}", f"Hour {h} is outside 0-23."))
            elif h not in allowed:
                v.append(
                    Violation(
                        "PRECOOL_HOUR_NOT_ALLOWED",
                        f"precool.{name}",
                        f"Hour {h} is not allowed in {name} (allowed: {list(allowed)}).",
                    )
                )
        if len(set(hours)) != len(hours):
            v.append(Violation("PRECOOL_HOURS_OVERLAP", f"precool.{name}", f"{name} lists the same hour twice."))

    both = sorted(set(pc.from_hours) & set(pc.to_hours))
    if both:
        v.append(
            Violation("PRECOOL_HOURS_OVERLAP", "precool", f"Hours {both} are in both from_hours and to_hours.")
        )
    return v


def _validate_demand_response(scenario: Scenario, plan: Plan) -> List[Violation]:
    v: List[Violation] = []
    programs = {p.name: p for p in scenario.demand_response}
    seen = set()
    for i, action in enumerate(plan.demand_response):
        path = f"demand_response[{i}]"
        program = programs.get(action.name)
        if program is None:
            v.append(
                Violation(
                    "DR_UNKNOWN_PROGRAM",
                    f"{path}.name",
                    f"No demand response program called '{action.name}' (available: {sorted(programs)}).",
                )
            )
            continue
        if action.name in seen:
            v.append(Violation("DR_DUPLICATE_PROGRAM", f"{path}.name", f"'{action.name}' appears more than once."))
        seen.add(action.name)
        if len(action.hours) > program.max_hours:
            v.append(
                Violation(
                    "DR_TOO_MANY_HOURS",
                    f"{path}.hours",
                    f"'{action.name}' can run at most {program.max_hours} hours, plan has {len(action.hours)}.",
                )
            )
        for h in action.hours:
            if not _in_day(h):
                v.append(Violation("HOUR_OUT_OF_RANGE", f"{path}.hours", f"Hour {h} is outside 0-23."))
            elif not (program.earliest <= h <= program.latest):
                v.append(
                    Violation(
                        "DR_HOUR_NOT_ALLOWED",
                        f"{path}.hours",
                        f"'{action.name}' can only run from hour {program.earliest} to {program.latest}, not {h}.",
                    )
                )
        if len(set(action.hours)) != len(action.hours):
            v.append(
                Violation("DR_HOUR_NOT_ALLOWED", f"{path}.hours", f"'{action.name}' lists the same hour twice.")
            )
    return v


def _validate_public_alert(scenario: Scenario, plan: Plan) -> List[Violation]:
    v: List[Violation] = []
    alert = plan.public_alert
    limits = scenario.public_alert
    if not alert.use:
        return v
    if limits.alerts_left_this_season <= 0:
        v.append(Violation("ALERT_NONE_LEFT", "public_alert.use", "No public alerts are left this season."))
    if not _in_day(alert.start_hour):
        v.append(
            Violation("HOUR_OUT_OF_RANGE", "public_alert.start_hour", f"Hour {alert.start_hour} is outside 0-23.")
        )
    if alert.hours < 1 or alert.hours > limits.max_hours:
        v.append(
            Violation(
                "ALERT_TOO_MANY_HOURS",
                "public_alert.hours",
                f"An alert must last 1 to {limits.max_hours} hours, plan has {alert.hours}.",
            )
        )
    elif _in_day(alert.start_hour) and alert.start_hour + alert.hours - 1 > HOURS_PER_DAY - 1:
        v.append(
            Violation(
                "ALERT_OUTSIDE_DAY",
                "public_alert.hours",
                f"An alert starting at hour {alert.start_hour} for {alert.hours} hours runs past hour 23.",
            )
        )
    return v


# ---------------------------------------------------------------------------
# Math (C3). Only called for valid plans.
# ---------------------------------------------------------------------------


def precool_delta(scenario: Scenario, plan: Plan) -> List[float]:
    """Demand change per hour from pre-cooling. Moves energy, doesn't remove it."""
    delta = [0.0] * HOURS_PER_DAY
    pc = plan.precool
    if pc.shift_mw <= 0 or not pc.from_hours or not pc.to_hours:
        return delta
    shifted_energy = pc.shift_mw * len(pc.from_hours)
    for h in pc.from_hours:
        delta[h] -= pc.shift_mw
    for h in pc.to_hours:
        delta[h] += shifted_energy / len(pc.to_hours)
    rebound_hour = max(pc.from_hours) + 1
    if rebound_hour <= HOURS_PER_DAY - 1:
        delta[rebound_hour] += pc.shift_mw * scenario.precool.rebound_pct / 100
    return delta


def _dr_by_hour(scenario: Scenario, plan: Plan) -> List[float]:
    mw = {p.name: p.mw for p in scenario.demand_response}
    out = [0.0] * HOURS_PER_DAY
    for action in plan.demand_response:
        for h in action.hours:
            out[h] += mw[action.name]
    return out


def _alert_by_hour(scenario: Scenario, plan: Plan) -> List[float]:
    out = [0.0] * HOURS_PER_DAY
    alert = plan.public_alert
    if alert.use:
        for h in range(alert.start_hour, alert.start_hour + alert.hours):
            out[h] = scenario.public_alert.reduction_mw.low  # conservative: LOW estimate
    return out


def _status(margin_pct: float, target: float) -> str:
    if margin_pct >= target:
        return GREEN
    if margin_pct >= 0:
        return YELLOW
    return RED


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


# ---------------------------------------------------------------------------
# Entry points (signatures are part of contract C4)
# ---------------------------------------------------------------------------


def simulate(
    scenario: Scenario,
    forecast: Forecast,
    plan: Plan,
    margin_target_pct: Optional[float] = None,
) -> SimResult:
    """Validate the plan, then compute margin and status for every hour.

    An invalid plan returns valid=False with every violation and no hours.
    Raises ValueError if the data itself is broken (net demand <= 0).
    """
    target = scenario.margin_target_pct if margin_target_pct is None else margin_target_pct
    violations = validate(scenario, plan)
    if violations:
        return _empty_result(plan.plan_hash(), target, violations)

    precool = precool_delta(scenario, plan)
    dr = _dr_by_hour(scenario, plan)
    alert = _alert_by_hour(scenario, plan)
    battery = [0.0] * HOURS_PER_DAY
    for action in plan.battery:
        battery[action.hour] = action.mw

    supply_cfg = scenario.supply
    hours: List[HourResult] = []
    for fh in forecast.hours:
        h = fh.hour
        demand_net = fh.p90 + precool[h] - dr[h] - alert[h]
        if demand_net <= 0:
            raise ValueError(f"Net demand is {demand_net} MW at hour {h}. Check the scenario and forecast data.")
        supply = supply_cfg.firm_mw + supply_cfg.solar_mw_by_hour[h] + supply_cfg.wind_mw_by_hour[h] + battery[h]
        margin = (supply - demand_net) / demand_net * 100
        hours.append(
            HourResult(
                hour=h,
                demand_p90=fh.p90,
                precool_delta=precool[h],
                dr_mw=dr[h],
                alert_mw=alert[h],
                demand_net=demand_net,
                firm_mw=supply_cfg.firm_mw,
                solar_mw=supply_cfg.solar_mw_by_hour[h],
                wind_mw=supply_cfg.wind_mw_by_hour[h],
                battery_mw=battery[h],
                supply=supply,
                margin_pct=margin,
                status=_status(margin, target),
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
        return _empty_result(None, target, [Violation("PLAN_SHAPE", err.path, err.message)]).to_dict()
    return simulate(scenario, forecast, parsed, margin_target_pct).to_dict()


def _empty_result(plan_hash: Optional[str], target: float, violations: List[Violation]) -> SimResult:
    return SimResult(
        engine=ENGINE,
        plan_hash=plan_hash,
        valid=False,
        violations=tuple(violations),
        margin_target_pct=target,
        hours=(),
        all_green=False,
        lowest_margin_pct=None,
        lowest_margin_hour=None,
        red_hours=(),
        yellow_hours=(),
        disruption_score=0,
    )
