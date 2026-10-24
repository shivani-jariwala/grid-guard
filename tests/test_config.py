"""Config tests: defaults, overrides, validation and .env loading."""

from __future__ import annotations

import pytest

from shared.config import ConfigError, load_env_file, load_settings, parse_env_file


def test_defaults_work_with_an_empty_environment():
    s = load_settings({})
    assert s.aws_region == "us-east-1"
    assert s.daily_run_cap == 100
    assert s.agent_max_simulate_calls == 5 and s.agent_max_tool_calls == 12
    assert s.runs_table == "gridguard-runs"
    assert s.bedrock_model_id == ""  # never hard-coded


def test_environment_overrides_defaults():
    s = load_settings({"AWS_REGION": "us-west-2", "DAILY_RUN_CAP": "7", "RUNS_TABLE": "Runs-abc", "LOG_LEVEL": "debug"})
    assert (s.aws_region, s.daily_run_cap, s.runs_table, s.log_level) == ("us-west-2", 7, "Runs-abc", "DEBUG")


def test_blank_numbers_fall_back_to_defaults():
    assert load_settings({"DAILY_RUN_CAP": ""}).daily_run_cap == 100


@pytest.mark.parametrize("value", ["abc", "1.5", "0", "-3"])
def test_bad_numbers_fail_clearly(value):
    with pytest.raises(ConfigError, match="DAILY_RUN_CAP"):
        load_settings({"DAILY_RUN_CAP": value})


def test_require_explains_missing_settings():
    s = load_settings({})
    with pytest.raises(ConfigError, match="BEDROCK_MODEL_ID is not set"):
        s.require("bedrock_model_id")
    assert load_settings({"BEDROCK_MODEL_ID": "us.anthropic.x"}).require("bedrock_model_id") == "us.anthropic.x"


def test_parse_env_file():
    text = """
    # comment
    A=1
    export B = "two words"
    C='quoted'
    D=
    not a pair
    E=x=y
    """
    assert parse_env_file(text) == {"A": "1", "B": "two words", "C": "quoted", "D": "", "E": "x=y"}


def test_env_file_never_overrides_existing_variables(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("RUNS_TABLE=from-file\nDECISIONS_TABLE=from-file\n")
    environ = {"RUNS_TABLE": "from-shell"}
    assert load_env_file(env_file, environ) is True
    assert environ == {"RUNS_TABLE": "from-shell", "DECISIONS_TABLE": "from-file"}


def test_missing_env_file_is_fine(tmp_path):
    environ = {}
    assert load_env_file(tmp_path / "nope.env", environ) is False
    assert environ == {}
