"""GET /runs/{run_id}: status, attempts, trace, final plan and briefing (contract C5). Owner: P3.

The dashboard polls this every 2 seconds until status is no longer "running".
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from shared.api import api_handler, ok, path_param, valid_run_id
from shared.data import NotFound
from shared.runs import RunStore, get_run_store, view_run


def get_run(event: Mapping[str, Any], runs: RunStore, now: Optional[Any] = None) -> Dict[str, Any]:
    run_id = valid_run_id(path_param(event, "run_id"))
    run = runs.get(run_id)
    if run is None:
        raise NotFound("RUN_NOT_FOUND", f"No run with ID '{run_id}'.")
    return view_run(run, now)  # a run stuck in "running" past the worker timeout shows as RUN_TIMED_OUT


@api_handler
def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    return ok(get_run(event, get_run_store()))
