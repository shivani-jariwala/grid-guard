"""GET /forecast?scenario=: hourly forecast + actual + CAISO forecast (contract C1). Owner: P1 (P1-11).

Mirrors resources_api: validate the scenario id, load the scenario to find its
forecast_id, then load and return that forecast's raw C1 JSON. A missing scenario
returns 404 SCENARIO_NOT_FOUND (same as GET /resources).
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

from shared.api import api_handler, ok, query_param, valid_id
from shared.data import Store, get_store, load_forecast, load_scenario


def get_forecast(event: Mapping[str, Any], store: Store) -> Dict[str, Any]:
    scenario_id = valid_id(query_param(event, "scenario"), "scenario")
    _, scenario = load_scenario(store, scenario_id)  # 404 SCENARIO_NOT_FOUND if missing
    raw, _ = load_forecast(store, scenario.forecast_id)  # parsing checks the file matches C1
    return raw


@api_handler
def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    return ok(get_forecast(event, get_store()))
