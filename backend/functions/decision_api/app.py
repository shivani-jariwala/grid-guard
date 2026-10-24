"""POST /runs/{run_id}/decision and GET /audit: Dana's decisions and the audit log (contract C5). Owner: P3.

A decision can only be recorded on a finished run (status "done"). It stores the
hash of that run's final plan, so the log shows exactly which plan was approved.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from shared.api import ApiError, api_handler, bad_request, json_body, ok, path_param, query_param, valid_run_id
from shared.data import NotFound
from shared.decisions import DecisionError, DecisionStore, decision_timestamp, get_decision_store, validate_decision
from shared.runs import STATUS_DONE, RunStore, get_run_store

AUDIT_DEFAULT_LIMIT = 50
AUDIT_MAX_LIMIT = 200


def record_decision(event: Mapping[str, Any], runs: RunStore, decisions: DecisionStore, now: Optional[Any] = None) -> Dict[str, Any]:
    run_id = valid_run_id(path_param(event, "run_id"))
    try:
        fields = validate_decision(json_body(event))
    except DecisionError as err:
        raise bad_request(str(err)) from None

    run = runs.get(run_id)
    if run is None:
        raise NotFound("RUN_NOT_FOUND", f"No run with ID '{run_id}'.")
    if run.get("status") != STATUS_DONE or not run.get("final"):
        raise ApiError(409, "RUN_NOT_DONE", "Decisions can only be recorded on a finished run.")

    item = {
        "run_id": run_id,
        "timestamp": decision_timestamp(now),
        "scenario_id": run["scenario_id"],
        "plan_hash": run["final"]["plan_hash"],
        "final_status": run["final"]["status"],
        **fields,
    }
    decisions.put(item)
    return item


def list_audit(event: Mapping[str, Any], decisions: DecisionStore) -> Dict[str, Any]:
    raw = query_param(event, "limit")
    if raw is None or raw == "":
        limit = AUDIT_DEFAULT_LIMIT
    else:
        try:
            limit = int(raw)
        except ValueError:
            raise bad_request("'limit' must be a whole number.") from None
        if not 1 <= limit <= AUDIT_MAX_LIMIT:
            raise bad_request(f"'limit' must be between 1 and {AUDIT_MAX_LIMIT}.")
    return {"decisions": decisions.recent(limit)}


@api_handler
def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    route = event.get("routeKey", "")
    if route == "GET /audit":
        return ok(list_audit(event, get_decision_store()))
    if route == "POST /runs/{run_id}/decision":
        return ok(record_decision(event, get_run_store(), get_decision_store()), 201)
    raise ApiError(404, "NOT_FOUND", "Unknown route.")
