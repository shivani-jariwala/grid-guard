"""Agent runs in DynamoDB: the run record (C5 `Runs`) and the daily run cap (C5 `Counters`). Owner: P3.

Only one writer touches a run after it's created (the agent worker), so runs use
simple read-modify-write (get -> change -> save). The daily counter is the one place
where many requests race, so it uses an atomic conditional update.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .config import Settings, get_settings

STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


class DailyLimitReached(Exception):
    """The daily run cap is used up."""


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pacific_day(now: datetime) -> str:
    """The calendar day in California, e.g. '2026-10-23'. The daily cap resets at Pacific midnight."""
    try:
        from zoneinfo import ZoneInfo

        local = now.astimezone(ZoneInfo("America/Los_Angeles"))
    except Exception:  # noqa: BLE001 - no tz database available: fall back to PDT (UTC-7)
        local = now.astimezone(timezone(timedelta(hours=-7)))
    return local.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# DynamoDB number conversion (DynamoDB wants Decimal, JSON wants int/float)
# ---------------------------------------------------------------------------


def to_dynamo(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: to_dynamo(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_dynamo(v) for v in value]
    return value


def from_dynamo(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_dynamo(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# Run record (C5)
# ---------------------------------------------------------------------------


def new_run(scenario_id: str, margin_target_pct: float, model_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    stamp = iso(now or utc_now())
    return {
        "run_id": str(uuid.uuid4()),
        "scenario_id": scenario_id,
        "margin_target_pct": margin_target_pct,
        "model_id": model_id,
        "status": STATUS_RUNNING,
        "created_at": stamp,
        "updated_at": stamp,
        "simulations": [],
        "trace": [],
        "counts": {"simulate_calls": 0, "tool_calls": 0},
        "final": None,
        "flags": [],
        "error": None,
    }


def add_trace(run: Dict[str, Any], tool: str, tool_input: Any, output_summary: str, duration_ms: int) -> None:
    """Record one tool call. The summary is cut to 500 chars (C5)."""
    run["trace"].append(
        {
            "step": len(run["trace"]) + 1,
            "tool": tool,
            "input": tool_input,
            "output_summary": output_summary[:500],
            "at": iso(utc_now()),
            "duration_ms": int(duration_ms),
        }
    )
    run["counts"]["tool_calls"] += 1


def add_simulation(run: Dict[str, Any], plan: Dict[str, Any], result: Dict[str, Any]) -> None:
    """Record one simulate call (C4: the server keeps the hash, never the model)."""
    run["simulations"].append(
        {
            "n": len(run["simulations"]) + 1,
            "plan_hash": result.get("plan_hash"),
            "plan": plan,
            "result": result,
            "at": iso(utc_now()),
        }
    )
    run["counts"]["simulate_calls"] += 1


class RunStore:
    """The `Runs` table."""

    def __init__(self, table: Any) -> None:
        self.table = table

    def create(self, run: Dict[str, Any]) -> None:
        self.table.put_item(Item=to_dynamo(run), ConditionExpression="attribute_not_exists(run_id)")

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        item = self.table.get_item(Key={"run_id": run_id}, ConsistentRead=True).get("Item")
        return None if item is None else from_dynamo(item)

    def save(self, run: Dict[str, Any]) -> None:
        run["updated_at"] = iso(utc_now())
        self.table.put_item(Item=to_dynamo(run))

    def fail(self, run: Dict[str, Any], code: str, message: str) -> None:
        run["status"] = STATUS_FAILED
        run["error"] = {"code": code, "message": message}
        self.save(run)


class DailyCounter:
    """The `Counters` table: one item per Pacific day, {day, runs}."""

    def __init__(self, table: Any) -> None:
        self.table = table

    def take(self, day: str, cap: int) -> int:
        """Count one run for `day`. Returns the new total, or raises DailyLimitReached.

        Atomic: the condition and the +1 happen in one DynamoDB call, so two requests
        racing for the last slot can't both get it.
        """
        from botocore.exceptions import ClientError

        try:
            response = self.table.update_item(
                Key={"day": day},
                UpdateExpression="ADD runs :one",
                ConditionExpression="attribute_not_exists(runs) OR runs < :cap",
                ExpressionAttributeValues={":one": 1, ":cap": cap},
                ReturnValues="UPDATED_NEW",
            )
        except ClientError as err:
            if err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise DailyLimitReached(f"The daily limit of {cap} runs has been reached.") from None
            raise
        return int(response["Attributes"]["runs"])


# ---------------------------------------------------------------------------
# Factories (real AWS clients; tests pass their own)
# ---------------------------------------------------------------------------


def _table(name: str, settings: Settings) -> Any:
    import boto3

    return boto3.resource("dynamodb", region_name=settings.aws_region).Table(name)


def get_run_store(settings: Optional[Settings] = None) -> RunStore:
    settings = settings or get_settings()
    return RunStore(_table(settings.runs_table, settings))


def get_daily_counter(settings: Optional[Settings] = None) -> DailyCounter:
    settings = settings or get_settings()
    return DailyCounter(_table(settings.counters_table, settings))


def check_submit(run: Dict[str, Any], plan: Any, status: str) -> Optional[str]:
    """The C4 submit gate. Returns None if the plan may be submitted, else the reason it can't.

    The hash is recomputed here from the plan itself. A hash sent by the model is never used.
    """
    from .models import Plan, ShapeError

    if status not in ("ALL_GREEN", "CANNOT_MEET_TARGET"):
        return "status must be ALL_GREEN or CANNOT_MEET_TARGET"
    try:
        plan_hash = Plan.from_dict(plan).plan_hash()
    except ShapeError as err:
        return f"plan has a bad shape: {err}"
    match = next((s for s in run["simulations"] if s.get("plan_hash") == plan_hash), None)
    if match is None:
        return "this exact plan was never simulated in this run"
    if not match["result"].get("valid"):
        return "this plan breaks the rules (invalid), so it can't be submitted"
    if status == "ALL_GREEN" and not match["result"].get("all_green"):
        return "status ALL_GREEN doesn't match the simulation (not every hour is GREEN)"
    return None


def elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def summary_of(result: Dict[str, Any]) -> str:
    """One-line description of a C4 result, for the trace."""
    if not result.get("valid"):
        codes = sorted({v["code"] for v in result.get("violations", [])})
        return f"Invalid plan: {', '.join(codes)}"
    if result.get("all_green"):
        return f"All 24 hours GREEN. Lowest margin {result['lowest_margin_pct']}% at hour {result['lowest_margin_hour']}."
    parts: List[str] = []
    if result.get("red_hours"):
        parts.append(f"RED at {_hours(result['red_hours'])}")
    if result.get("yellow_hours"):
        parts.append(f"YELLOW at {_hours(result['yellow_hours'])}")
    parts.append(f"lowest margin {result['lowest_margin_pct']}% at hour {result['lowest_margin_hour']}")
    return "; ".join(parts) + "."


def _hours(hours: List[int]) -> str:
    return ", ".join(str(h) for h in hours)
