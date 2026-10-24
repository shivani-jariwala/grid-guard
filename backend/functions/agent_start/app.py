"""POST /runs: start an agent run and return its run_id right away (contract C5). Owner: P3.

Order matters:
1. validate the request and check the scenario exists (bad requests don't use up the cap);
2. take one slot of the daily run cap (atomic, so racing requests can't exceed it);
3. save the run as "running";
4. start the agent worker asynchronously (API Gateway would time out waiting ~30 s).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Mapping, Optional

from shared.api import ApiError, api_handler, json_body, ok, optional_number, valid_id
from shared.config import Settings, get_settings
from shared.data import Store, get_store, load_scenario
from shared.runs import (
    DailyCounter,
    DailyLimitReached,
    RunStore,
    get_daily_counter,
    get_run_store,
    new_run,
    pacific_day,
    utc_now,
)

logger = logging.getLogger("gridguard")

Invoker = Callable[[str], None]


def make_invoker(settings: Settings) -> Invoker:
    """Fire-and-forget call to the agent worker Lambda."""
    import boto3

    function_name = settings.require("worker_function_name")
    client = boto3.client("lambda", region_name=settings.aws_region)

    def invoke(run_id: str) -> None:
        client.invoke(FunctionName=function_name, InvocationType="Event", Payload=json.dumps({"run_id": run_id}))

    return invoke


def start_run(
    event: Mapping[str, Any],
    store: Store,
    runs: RunStore,
    counter: DailyCounter,
    invoke: Invoker,
    settings: Settings,
    now: Optional[Any] = None,
) -> Dict[str, Any]:
    body = json_body(event)
    scenario_id = valid_id(body.get("scenario_id"), "scenario_id")
    override = optional_number(body, "margin_target_pct", 0, 100)
    _, scenario = load_scenario(store, scenario_id)  # 404 before the cap is touched
    target = scenario.margin_target_pct if override is None else override

    try:
        counter.take(pacific_day(now or utc_now()), settings.daily_run_cap)
    except DailyLimitReached as err:
        raise ApiError(429, "DAILY_LIMIT_REACHED", f"{err} Try again tomorrow.") from None

    model_id = settings.bedrock_model_id if settings.agent_worker == "bedrock" else "stub-agent"
    run = new_run(scenario_id, target, model_id, now)
    runs.create(run)
    try:
        invoke(run["run_id"])
    except Exception:  # noqa: BLE001 - record the failure on the run, then tell the client
        logger.exception("could not start worker for run %s", run["run_id"])
        runs.fail(run, "WORKER_START_FAILED", "The agent could not be started.")
        raise ApiError(500, "WORKER_START_FAILED", "The agent could not be started. Please try again.") from None
    return ok({"run_id": run["run_id"], "status": run["status"]}, 202)


@api_handler
def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    settings = get_settings()
    return start_run(event, get_store(), get_run_store(), get_daily_counter(), make_invoker(settings), settings)
