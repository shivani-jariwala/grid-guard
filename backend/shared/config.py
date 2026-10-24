"""All GridGuard settings in one place. Owner: P3.

Settings come from environment variables:
- Locally: put them in a `.env` file at the repo root (copy `.env.example`).
  The `.env` file is git-ignored and is loaded automatically, without
  overriding variables that are already set in your shell.
- In AWS: the SAM template sets the same variable names on each Lambda.
  There is no `.env` file there, so nothing is loaded from disk.

Rules (see .kiro/steering/rules.md):
- No secrets in code. The EIA key lives in SSM. Locally you may set EIA_API_KEY in `.env`.
- AWS credentials never go in `.env`. Use the `~/.aws` profile (AWS_PROFILE=gridguard).
- Never hard-code the Bedrock model ID. It comes from BEDROCK_MODEL_ID.

Usage:
    from shared.config import get_settings
    settings = get_settings()
    settings.runs_table
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping, MutableMapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"


class ConfigError(RuntimeError):
    """A setting is missing or has a bad value."""


# ---------------------------------------------------------------------------
# .env loading (tiny, no dependency)
# ---------------------------------------------------------------------------


def parse_env_file(text: str) -> dict:
    """Parse KEY=VALUE lines. Ignores blank lines and # comments. Strips matching quotes."""
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def load_env_file(path: Optional[Path] = None, environ: Optional[MutableMapping[str, str]] = None) -> bool:
    """Load `.env` into the environment without overriding existing variables.

    Returns True if a file was found. Safe to call when no file exists (e.g. in Lambda).
    """
    env = os.environ if environ is None else environ
    if path is None:
        override = env.get("GRIDGUARD_ENV_FILE")  # optional: point at a different file
        path = Path(override) if override else DEFAULT_ENV_FILE
    if not path.is_file():
        return False
    for key, value in parse_env_file(path.read_text(encoding="utf-8")).items():
        env.setdefault(key, value)
    return True


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    aws_region: str
    bedrock_model_id: str  # empty until P2 picks one (P2-01)
    bedrock_guardrail_id: str  # empty until P2-10
    bedrock_guardrail_version: str
    runs_table: str
    decisions_table: str
    counters_table: str
    forecast_bucket: str  # empty = read scenarios/forecasts from the repo folders (local dev)
    forecast_prefix: str
    scenario_prefix: str
    eia_api_key_param: str  # SSM parameter NAME, not the key
    daily_run_cap: int
    agent_max_simulate_calls: int
    agent_max_tool_calls: int
    log_level: str

    def require(self, name: str) -> str:
        """Return a string setting, or fail with a clear message if it's empty."""
        value = getattr(self, name)
        if not value:
            raise ConfigError(f"{name.upper()} is not set. Add it to your .env (see .env.example) or the SAM template.")
        return value


def _int(env: Mapping[str, str], key: str, default: int, minimum: int = 1) -> int:
    raw = env.get(key, "")
    if raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be a whole number, got {raw!r}.") from None
    if value < minimum:
        raise ConfigError(f"{key} must be at least {minimum}, got {value}.")
    return value


def load_settings(env: Mapping[str, str]) -> Settings:
    """Build Settings from a mapping (os.environ in real use, a dict in tests)."""
    return Settings(
        aws_region=env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or "us-east-1",
        bedrock_model_id=env.get("BEDROCK_MODEL_ID", ""),
        bedrock_guardrail_id=env.get("BEDROCK_GUARDRAIL_ID", ""),
        bedrock_guardrail_version=env.get("BEDROCK_GUARDRAIL_VERSION", "DRAFT"),
        runs_table=env.get("RUNS_TABLE", "gridguard-runs"),
        decisions_table=env.get("DECISIONS_TABLE", "gridguard-decisions"),
        counters_table=env.get("COUNTERS_TABLE", "gridguard-counters"),
        forecast_bucket=env.get("FORECAST_BUCKET", ""),
        forecast_prefix=env.get("FORECAST_PREFIX", "forecasts/"),
        scenario_prefix=env.get("SCENARIO_PREFIX", "scenarios/"),
        eia_api_key_param=env.get("EIA_API_KEY_PARAM", "/gridguard/eia-api-key"),
        daily_run_cap=_int(env, "DAILY_RUN_CAP", 100),
        agent_max_simulate_calls=_int(env, "AGENT_MAX_SIMULATE_CALLS", 5),
        agent_max_tool_calls=_int(env, "AGENT_MAX_TOOL_CALLS", 12),
        log_level=env.get("LOG_LEVEL", "INFO").upper(),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings for this process (loads `.env` once if it exists)."""
    load_env_file()
    return load_settings(os.environ)


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------


def get_eia_api_key(settings: Optional[Settings] = None) -> str:
    """EIA API key: EIA_API_KEY from the environment (local), else SSM (AWS).

    Never log or print the returned value.
    """
    load_env_file()
    local = os.environ.get("EIA_API_KEY", "")
    if local:
        return local
    settings = settings or get_settings()
    import boto3  # imported here so the rest of config works without AWS

    ssm = boto3.client("ssm", region_name=settings.aws_region)
    response = ssm.get_parameter(Name=settings.eia_api_key_param, WithDecryption=True)
    return response["Parameter"]["Value"]
