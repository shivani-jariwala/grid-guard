"""Briefing parts that are built by code, never written by the model (C6). Owner: P3, used by P2.

The action table's numbers come straight from the accepted plan and the scenario,
so the number guard only has to check the model's own words (rationale, risks, alert).
"""

from __future__ import annotations

from typing import Any, Dict, List

from .models import Plan, Scenario

ALERT_LABEL = "Draft only. Not sent."


def build_actions(scenario: Scenario, plan: Plan) -> List[Dict[str, Any]]:
    """One row per action for the briefing's action table, in least-disruptive-first order."""
    rows: List[Dict[str, Any]] = []
    for a in plan.battery:
        rows.append({"action": "battery", "label": "Battery discharge", "hours": [a.hour], "mw": a.mw})
    if plan.precool.shift_mw > 0:
        rows.append(
            {
                "action": "precool",
                "label": f"Pre-cool buildings (move load from hours {_span(plan.precool.from_hours)} "
                f"to {_span(plan.precool.to_hours)})",
                "hours": list(plan.precool.from_hours),
                "mw": plan.precool.shift_mw,
            }
        )
    program_mw = {p.name: p.mw for p in scenario.demand_response}
    for a in plan.demand_response:
        rows.append(
            {
                "action": "demand_response",
                "label": f"Demand response: {a.name}",
                "hours": list(a.hours),
                "mw": program_mw.get(a.name, 0.0),
            }
        )
    if plan.public_alert.use:
        start, n = plan.public_alert.start_hour, plan.public_alert.hours
        rows.append(
            {
                "action": "public_alert",
                "label": "Public conservation alert (draft only, not sent)",
                "hours": list(range(start, start + n)),
                "mw": scenario.public_alert.reduction_mw.low,
            }
        )
    return rows


def _span(hours: Any) -> str:
    hours = list(hours)
    if not hours:
        return "-"
    if hours == list(range(hours[0], hours[-1] + 1)):
        return f"{hours[0]}-{hours[-1]}" if len(hours) > 1 else str(hours[0])
    return ", ".join(str(h) for h in hours)
