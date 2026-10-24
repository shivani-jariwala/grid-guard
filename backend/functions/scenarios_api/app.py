"""GET /scenarios: list available scenarios (contract C5). Owner: P3."""

from __future__ import annotations

from typing import Any, Dict

from shared.api import api_handler, ok
from shared.data import Store, get_store, load_scenario


def list_scenarios(store: Store) -> Dict[str, Any]:
    scenarios = []
    for scenario_id in store.list_scenario_ids():
        _, s = load_scenario(store, scenario_id)
        scenarios.append({"id": s.id, "name": s.name, "date": s.date, "margin_target_pct": s.margin_target_pct})
    return {"scenarios": scenarios}


@api_handler
def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    return ok(list_scenarios(get_store()))
