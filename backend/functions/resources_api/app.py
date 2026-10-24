"""GET /resources?scenario={id}: the full scenario as stored, including sources (contract C2). Owner: P3."""

from __future__ import annotations

from typing import Any, Dict, Mapping

from shared.api import api_handler, ok, query_param, valid_id
from shared.data import Store, get_store, load_scenario


def get_resources(event: Mapping[str, Any], store: Store) -> Dict[str, Any]:
    scenario_id = valid_id(query_param(event, "scenario"), "scenario")
    raw, _ = load_scenario(store, scenario_id)  # parsing checks the file matches C2
    return raw


@api_handler
def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    return ok(get_resources(event, get_store()))
