"""Operator decisions and the audit log (contract C5 `Decisions`). Owner: P3.

Every Approve / Edit / Reject is stored with the operator's name, a server timestamp
and the hash of the exact plan it applies to, so the audit log shows who decided
what, and when. No personal data other than the operator name.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from .config import Settings, get_settings
from .runs import from_dynamo, to_dynamo

DECISIONS = ("APPROVE", "EDIT", "REJECT")
MAX_OPERATOR_NAME = 60
MAX_NOTE = 500
MAX_ALERT_TEXT = 160


class DecisionError(ValueError):
    """The decision request is invalid. The message names the field."""


def decision_timestamp(now: Optional[datetime] = None) -> str:
    """ISO-8601 UTC with microseconds, so two decisions on one run in the same second don't collide."""
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def validate_decision(body: Mapping[str, Any]) -> Dict[str, Any]:
    """Check a decision request body and return the cleaned fields."""
    allowed = {"operator_name", "decision", "note", "edits"}
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise DecisionError(f"Unknown field(s): {', '.join(unknown)}.")

    name = body.get("operator_name")
    if not isinstance(name, str) or not name.strip():
        raise DecisionError("'operator_name' is required.")
    name = name.strip()
    if len(name) > MAX_OPERATOR_NAME:
        raise DecisionError(f"'operator_name' must be at most {MAX_OPERATOR_NAME} characters.")

    decision = body.get("decision")
    if decision not in DECISIONS:
        raise DecisionError("'decision' must be APPROVE, EDIT or REJECT.")

    note = body.get("note")
    if note is not None:
        if not isinstance(note, str):
            raise DecisionError("'note' must be text.")
        note = note.strip()
        if len(note) > MAX_NOTE:
            raise DecisionError(f"'note' must be at most {MAX_NOTE} characters.")
        note = note or None

    edits = body.get("edits")
    if decision == "EDIT":
        if not isinstance(edits, Mapping) or set(edits) - {"alert_text"}:
            raise DecisionError("An EDIT needs 'edits': {\"alert_text\": \"...\"}.")
        text = edits.get("alert_text")
        if not isinstance(text, str) or not text.strip():
            raise DecisionError("'edits.alert_text' is required for an EDIT.")
        text = text.strip()
        if len(text) > MAX_ALERT_TEXT:
            raise DecisionError(f"'edits.alert_text' must be at most {MAX_ALERT_TEXT} characters.")
        edits = {"alert_text": text}
    elif edits is not None:
        raise DecisionError("'edits' is only allowed when 'decision' is EDIT.")

    return {"operator_name": name, "decision": decision, "note": note, "edits": edits}


class DecisionStore:
    """The `Decisions` table (partition key run_id, sort key timestamp)."""

    def __init__(self, table: Any) -> None:
        self.table = table

    def put(self, item: Dict[str, Any]) -> None:
        self.table.put_item(Item=to_dynamo(item))

    def recent(self, limit: int) -> List[Dict[str, Any]]:
        """Newest first. A full scan is fine at hackathon scale (a few hundred items at most)."""
        items: List[Dict[str, Any]] = []
        kwargs: Dict[str, Any] = {}
        while True:
            page = self.table.scan(**kwargs)
            items.extend(page.get("Items", []))
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        items.sort(key=lambda d: d["timestamp"], reverse=True)
        return [from_dynamo(d) for d in items[:limit]]


def get_decision_store(settings: Optional[Settings] = None) -> DecisionStore:
    import boto3

    settings = settings or get_settings()
    return DecisionStore(boto3.resource("dynamodb", region_name=settings.aws_region).Table(settings.decisions_table))
