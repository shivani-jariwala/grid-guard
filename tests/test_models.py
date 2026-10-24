"""Model tests (P3-09): plan parsing, canonical form and plan hash (C3), forecast/scenario shape (C1, C2)."""

from __future__ import annotations

import copy
import json

import pytest

from shared.models import Forecast, Plan, Scenario, ShapeError

from conftest import BASE_SCENARIO, forecast_dict


# ---------------------------------------------------------------------------
# Canonical form and hash
# ---------------------------------------------------------------------------


def test_same_plan_written_differently_has_the_same_hash():
    a = Plan.from_dict(
        {
            "battery": [{"hour": 19, "mw": 500.0}, {"hour": 18, "mw": 500}],
            "precool": {"to_hours": [15, 13, 14], "shift_mw": 300, "from_hours": [19, 17, 18]},
        }
    )
    b = Plan.from_dict(
        {
            "precool": {"shift_mw": 300.0, "from_hours": [17, 18, 19], "to_hours": [13, 14, 15]},
            "battery": [{"mw": 500, "hour": 18}, {"mw": 500, "hour": 19.0}],
            "demand_response": [],
            "public_alert": {"use": False, "start_hour": 17, "hours": 3},
        }
    )
    assert a.plan_hash() == b.plan_hash()


def test_any_change_gives_a_different_hash():
    base = Plan.from_dict({"battery": [{"hour": 18, "mw": 500}]})
    assert base.plan_hash() != Plan.from_dict({"battery": [{"hour": 18, "mw": 501}]}).plan_hash()
    assert base.plan_hash() != Plan.from_dict({"battery": [{"hour": 19, "mw": 500}]}).plan_hash()


def test_every_way_of_saying_no_action_is_the_same_plan():
    variants = [
        {},
        {"battery": [], "demand_response": []},
        {"battery": [{"hour": 18, "mw": 0}]},
        {"precool": {"shift_mw": 0, "from_hours": [17], "to_hours": [13]}},
        {"demand_response": [{"name": "Program A", "hours": []}]},
        {"public_alert": {"use": False, "start_hour": 17, "hours": 3}},
        {"precool": None, "public_alert": None},
    ]
    hashes = {Plan.from_dict(v).plan_hash() for v in variants}
    assert len(hashes) == 1
    assert Plan.from_dict({}).is_no_action


def test_canonical_json_is_sorted_and_compact():
    text = Plan.from_dict({"battery": [{"hour": 18, "mw": 500}]}).canonical_json()
    assert " " not in text
    assert text.startswith('{"battery":[{"hour":18,"mw":500.0}],"demand_response":[],"precool":')
    assert json.loads(text)["public_alert"] == {"hours": 0, "start_hour": 0, "use": False}


def test_hash_is_sha256_hex():
    h = Plan.from_dict({}).plan_hash()
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


def test_mw_is_rounded_to_3_decimals():
    a = Plan.from_dict({"battery": [{"hour": 18, "mw": 0.1 + 0.2}]})
    b = Plan.from_dict({"battery": [{"hour": 18, "mw": 0.3}]})
    assert a.plan_hash() == b.plan_hash()


def test_duplicates_are_kept_for_validation():
    plan = Plan.from_dict({"battery": [{"hour": 18, "mw": 1}, {"hour": 18, "mw": 2}]})
    assert len(plan.battery) == 2


# ---------------------------------------------------------------------------
# Plan shape errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "plan, path, message",
    [
        ("not a dict", "plan", "must be an object"),
        ({"batery": []}, "batery", "unknown key"),
        ({"battery": [{"hour": 18, "mw": 1, "extra": 1}]}, "battery[0].extra", "unknown key"),
        ({"battery": [{"hour": 18.5, "mw": 1}]}, "battery[0].hour", "whole number"),
        ({"battery": [{"hour": True, "mw": 1}]}, "battery[0].hour", "whole number"),
        ({"battery": [{"hour": "18", "mw": 1}]}, "battery[0].hour", "whole number"),
        ({"battery": [{"hour": 18}]}, "battery[0].mw", "required"),
        ({"battery": [{"hour": 18, "mw": -1}]}, "battery[0].mw", "0 or more"),
        ({"precool": {"from_hours": [17]}}, "precool.shift_mw", "required"),
        ({"demand_response": [{"hours": [18]}]}, "demand_response[0].name", "required"),
        ({"public_alert": {"use": "yes"}}, "public_alert.use", "true or false"),
        ({"public_alert": {"use": True}}, "public_alert.start_hour", "required"),
    ],
)
def test_bad_plan_shapes_raise_with_a_path(plan, path, message):
    with pytest.raises(ShapeError) as err:
        Plan.from_dict(plan)
    assert err.value.path == path
    assert message in err.value.message


# ---------------------------------------------------------------------------
# Forecast and scenario shape
# ---------------------------------------------------------------------------


def test_forecast_needs_all_24_hours():
    d = forecast_dict()
    d["hours"] = d["hours"][:23]
    with pytest.raises(ShapeError, match="0-23"):
        Forecast.from_dict(d)


def test_forecast_hours_are_sorted_on_load():
    d = forecast_dict()
    d["hours"].reverse()
    assert [h.hour for h in Forecast.from_dict(d).hours] == list(range(24))


def test_scenario_needs_24_solar_values():
    d = copy.deepcopy(BASE_SCENARIO)
    d["supply"]["solar_mw_by_hour"] = [0] * 23
    with pytest.raises(ShapeError, match="24 values"):
        Scenario.from_dict(d)


def test_battery_usable_energy():
    s = Scenario.from_dict(BASE_SCENARIO)
    assert s.battery.usable_mwh == pytest.approx(4000 * 1.0 * 0.75)
