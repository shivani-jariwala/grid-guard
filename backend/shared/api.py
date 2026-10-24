"""HTTP helpers shared by every API Lambda (API Gateway HTTP API, payload v2.0). Owner: P3.

Handlers stay thin: validate input with these helpers, call shared code, return ok(...).
Errors are raised as ApiError (or NotFound / DataError from shared.data) and turned
into the standard error body by @api_handler:

    {"error": {"code": "SCENARIO_NOT_FOUND", "message": "..."}}

CORS headers are added by API Gateway, not here.
"""

from __future__ import annotations

import base64
import functools
import json
import logging
import re
from typing import Any, Callable, Dict, Mapping, Optional

from .config import get_settings
from .data import DataError, NotFound

logger = logging.getLogger("gridguard")
logger.setLevel(get_settings().log_level)

_ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class ApiError(Exception):
    """An expected client-facing error with an HTTP status and a stable code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def ok(body: Any, status: int = 200) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, allow_nan=False),
    }


def error(status: int, code: str, message: str) -> Dict[str, Any]:
    return ok({"error": {"code": code, "message": message}}, status)


def bad_request(message: str) -> ApiError:
    return ApiError(400, "BAD_REQUEST", message)


def json_body(event: Mapping[str, Any]) -> Dict[str, Any]:
    """The request body as a JSON object, or ApiError 400."""
    raw = event.get("body")
    if raw is None or raw == "":
        raise bad_request("Request body is required.")
    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise bad_request("Request body is not valid base64 UTF-8.") from None
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        raise bad_request("Request body is not valid JSON.") from None
    if not isinstance(body, dict):
        raise bad_request("Request body must be a JSON object.")
    return body


def query_param(event: Mapping[str, Any], name: str) -> Optional[str]:
    return (event.get("queryStringParameters") or {}).get(name)


def path_param(event: Mapping[str, Any], name: str) -> Optional[str]:
    return (event.get("pathParameters") or {}).get(name)


def valid_id(value: Any, field: str) -> str:
    """Scenario / forecast IDs: lowercase letters, digits and _ only (no '/' or '..')."""
    if not isinstance(value, str) or not value:
        raise bad_request(f"'{field}' is required.")
    if not _ID_RE.match(value):
        raise bad_request(f"'{field}' must be 1-64 lowercase letters, digits or underscores.")
    return value


def valid_run_id(value: Any, field: str = "run_id") -> str:
    if not isinstance(value, str) or not _UUID_RE.match(value):
        raise bad_request(f"'{field}' must be a run ID (UUID).")
    return value


def optional_number(body: Mapping[str, Any], field: str, low: float, high: float) -> Optional[float]:
    """A number in [low, high] if present, else None. Rejects true/false."""
    if field not in body or body[field] is None:
        return None
    value = body[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise bad_request(f"'{field}' must be a number.")
    if not (low <= value <= high):
        raise bad_request(f"'{field}' must be between {low:g} and {high:g}.")
    return float(value)


Handler = Callable[[Mapping[str, Any], Any], Dict[str, Any]]


def api_handler(fn: Handler) -> Handler:
    """Wrap a Lambda handler: map known errors to responses, hide unexpected ones."""

    @functools.wraps(fn)
    def wrapper(event: Mapping[str, Any], context: Any) -> Dict[str, Any]:
        request_id = getattr(context, "aws_request_id", "-")
        route = event.get("routeKey", "-")
        try:
            return fn(event, context)
        except ApiError as err:
            logger.info("client error %s %s on %s [%s]", err.status, err.code, route, request_id)
            return error(err.status, err.code, err.message)
        except NotFound as err:
            logger.info("not found %s on %s [%s]", err.code, route, request_id)
            return error(404, err.code, err.message)
        except DataError as err:
            # Our stored data is broken. Log the detail, keep the client message generic.
            logger.error("data error on %s [%s]: %s", route, request_id, err)
            return error(500, "DATA_ERROR", "Stored scenario or forecast data is invalid. The team has been notified in the logs.")
        except Exception:  # noqa: BLE001 - last line of defense
            logger.exception("unexpected error on %s [%s]", route, request_id)
            return error(500, "INTERNAL_ERROR", "Something went wrong on our side. Please try again.")

    return wrapper
