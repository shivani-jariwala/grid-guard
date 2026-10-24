"""POST /simulate: run the simulator on a plan for the what-if panel (contracts C3, C4). Owner: P3.

Plain code only, no AI. An invalid plan is NOT an HTTP error: it returns 200 with
valid=false and the violations, so the UI can show what's wrong.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

from shared.api import api_handler, bad_request, json_body, ok, optional_number, valid_id
from shared.data import DataError, Store, get_store, load_forecast, load_scenario
from shared.simulator import simulate_dict


def run_simulation(event: Mapping[str, Any], store: Store) -> Dict[str, Any]:
    body = json_body(event)
    scenario_id = valid_id(body.get("scenario_id"), "scenario_id")
    if "plan" not in body:
        raise bad_request("'plan' is required (use {} for the no-action plan).")
    target = optional_number(body, "margin_target_pct", 0, 100)

    _, scenario = load_scenario(store, scenario_id)
    _, forecast = load_forecast(store, scenario.forecast_id)
    try:
        return simulate_dict(scenario, forecast, body["plan"], target)
    except ValueError as err:  # net demand <= 0: the stored data is impossible
        raise DataError(f"scenario '{scenario_id}': {err}") from None


@api_handler
def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    return ok(run_simulation(event, get_store()))
