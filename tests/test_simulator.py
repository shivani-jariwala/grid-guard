"""Simulator tests (P3-09). Hand-checkable numbers are explained in conftest.py."""

from __future__ import annotations

import json

import pytest

from shared.models import GREEN, RED, YELLOW, Forecast, Plan
from shared.simulator import ENGINE, precool_delta, simulate_dict

from conftest import EVENING, forecast_dict


def codes(result):
    return [v.code for v in result.violations]


def hour(result, h):
    return result.hours[h]


# ---------------------------------------------------------------------------
# Status math and margins
# ---------------------------------------------------------------------------


def test_no_action_plan_is_red_only_in_the_evening(run):
    r = run({})
    assert r.valid and r.engine == ENGINE == "v1"
    assert r.red_hours == EVENING
    assert r.yellow_hours == ()
    assert not r.all_green
    assert r.lowest_margin_hour == 17  # first of the tied evening hours
    assert r.lowest_margin_pct == pytest.approx((10600 - 11000) / 11000 * 100)
    assert r.disruption_score == 0


def test_margin_exactly_at_target_is_green(run):
    r = run({})
    assert hour(r, 3).margin_pct == pytest.approx(6.0)
    assert hour(r, 3).status == GREEN


def test_margin_between_zero_and_target_is_yellow(run):
    # Raise the target to 10%: the 6% hours are now short of it, but not negative.
    r = run({}, target=10)
    assert r.margin_target_pct == 10
    assert hour(r, 3).status == YELLOW
    assert hour(r, 18).status == RED


def test_margin_formula_with_battery(run):
    r = run({"battery": [{"hour": 18, "mw": 1000}]})
    h18 = hour(r, 18)
    assert h18.supply == 10600 + 1000
    assert h18.demand_net == 11000
    assert h18.margin_pct == pytest.approx((11600 - 11000) / 11000 * 100)  # 5.45%
    assert h18.status == YELLOW


def test_a_plan_can_reach_all_green(run):
    plan = {
        "battery": [{"hour": 17, "mw": 660}, {"hour": 18, "mw": 660}, {"hour": 19, "mw": 660}, {"hour": 20, "mw": 850}],
        "demand_response": [
            {"name": "Program A", "hours": [17, 18, 19]},
            {"name": "Program B", "hours": [20]},
        ],
    }
    r = run(plan)
    assert r.valid, r.violations
    assert r.all_green
    assert r.red_hours == () and r.yellow_hours == ()
    assert r.disruption_score == 1 + 3 + 3


def test_supply_includes_solar_and_wind(run, make_scenario):
    def edit(d):
        d["supply"]["solar_mw_by_hour"] = [100] * 24
        d["supply"]["wind_mw_by_hour"] = [50] * 24

    r = run({}, scenario_=make_scenario(edit))
    assert hour(r, 5).supply == 10600 + 100 + 50


def test_broken_data_raises(run):
    tiny = Forecast.from_dict(forecast_dict([100.0] * 24))
    with pytest.raises(ValueError, match="Net demand"):
        run({"demand_response": [{"name": "Program A", "hours": [17]}]}, forecast_=tiny)


# ---------------------------------------------------------------------------
# Battery
# ---------------------------------------------------------------------------


def test_battery_only_adds_supply_in_its_hour(run):
    r = run({"battery": [{"hour": 18, "mw": 500}]})
    assert hour(r, 18).battery_mw == 500
    assert hour(r, 17).battery_mw == 0
    assert hour(r, 19).supply == 10600


def test_battery_power_limit(run):
    r = run({"battery": [{"hour": 18, "mw": 1001}]})
    assert not r.valid
    assert codes(r) == ["BATTERY_POWER_EXCEEDED"]


def test_battery_can_use_exactly_its_usable_energy(run):
    # 3,000 MWh usable = 4,000 MWh x 100% x 0.75
    r = run({"battery": [{"hour": h, "mw": 1000} for h in (17, 18, 19)]})
    assert r.valid, r.violations


def test_battery_cannot_over_discharge(run):
    r = run({"battery": [{"hour": h, "mw": 1000} for h in (17, 18, 19)] + [{"hour": 20, "mw": 1}]})
    assert codes(r) == ["BATTERY_ENERGY_EXCEEDED"]


def test_battery_usable_energy_uses_start_charge(run, make_scenario):
    half_charged = make_scenario(lambda d: d["battery"].update(start_soc_pct=50))  # 1,500 MWh usable
    r = run({"battery": [{"hour": 17, "mw": 1000}, {"hour": 18, "mw": 600}]}, scenario_=half_charged)
    assert codes(r) == ["BATTERY_ENERGY_EXCEEDED"]


def test_battery_duplicate_and_out_of_range_hours(run):
    r = run({"battery": [{"hour": 18, "mw": 100}, {"hour": 18, "mw": 100}, {"hour": 24, "mw": 100}]})
    assert sorted(codes(r)) == ["BATTERY_DUPLICATE_HOUR", "HOUR_OUT_OF_RANGE"]


# ---------------------------------------------------------------------------
# Pre-cooling: moves energy, doesn't remove it
# ---------------------------------------------------------------------------

PRECOOL = {"shift_mw": 600, "from_hours": [17, 18, 19], "to_hours": [13, 14, 15]}


def test_precool_energy_is_conserved(scenario):
    delta = precool_delta(scenario, Plan.from_dict({"precool": PRECOOL}))
    moved = [delta[h] for h in (17, 18, 19, 13, 14, 15)]
    assert sum(moved) == pytest.approx(0)  # removed in the evening == added in the afternoon
    assert delta[17] == delta[18] == delta[19] == -600
    assert delta[13] == delta[14] == delta[15] == pytest.approx(600)  # 1,800 MWh / 3 hours


def test_precool_energy_is_conserved_with_uneven_hours(scenario):
    plan = Plan.from_dict({"precool": {"shift_mw": 600, "from_hours": [17, 18], "to_hours": [13, 14, 15]}})
    delta = precool_delta(scenario, plan)
    assert [delta[h] for h in (13, 14, 15)] == pytest.approx([400, 400, 400])  # 1,200 MWh / 3
    assert sum(delta[h] for h in (17, 18, 13, 14, 15)) == pytest.approx(0)


def test_precool_rebound_is_applied_after_the_last_from_hour(scenario):
    delta = precool_delta(scenario, Plan.from_dict({"precool": PRECOOL}))
    assert delta[20] == pytest.approx(600 * 50 / 100)  # rebound_pct = 50
    assert sum(delta) == pytest.approx(300)  # the only extra energy is the rebound


def test_precool_rebound_uses_the_latest_hour_even_if_unsorted(scenario):
    plan = Plan.from_dict({"precool": {"shift_mw": 600, "from_hours": [19, 17], "to_hours": [13]}})
    delta = precool_delta(scenario, plan)
    assert delta[20] == pytest.approx(300)
    assert delta[18] == 0


def test_precool_rebound_is_ignored_after_hour_23(run, make_scenario):
    def edit(d):
        d["precool"]["allowed_from_hours"] = [22, 23]

    late = make_scenario(edit)
    plan = Plan.from_dict({"precool": {"shift_mw": 600, "from_hours": [23], "to_hours": [13]}})
    delta = precool_delta(late, plan)
    assert delta[23] == -600 and delta[13] == 600
    assert sum(delta) == pytest.approx(0)
    assert run({"precool": plan.to_dict()["precool"]}, scenario_=late).valid


def test_precool_moves_risk_into_the_afternoon(run):
    r = run({"precool": PRECOOL})
    assert hour(r, 18).demand_net == 11000 - 600
    assert hour(r, 13).demand_net == pytest.approx(10000 + 600)
    assert hour(r, 13).status == YELLOW  # 0% margin: the afternoon got tighter
    assert hour(r, 20).demand_net == pytest.approx(11000 + 300)  # rebound


def test_precool_shift_limit(run):
    r = run({"precool": dict(PRECOOL, shift_mw=601)})
    assert codes(r) == ["PRECOOL_SHIFT_EXCEEDED"]


def test_precool_hours_must_be_allowed(run):
    r = run({"precool": {"shift_mw": 100, "from_hours": [16], "to_hours": [17]}})
    assert codes(r) == ["PRECOOL_HOUR_NOT_ALLOWED", "PRECOOL_HOUR_NOT_ALLOWED"]


def test_precool_needs_both_hour_lists(run):
    r = run({"precool": {"shift_mw": 100, "from_hours": [17], "to_hours": []}})
    assert codes(r) == ["PRECOOL_HOURS_MISSING"]


def test_precool_rejects_overlapping_and_duplicate_hours(run, make_scenario):
    def edit(d):
        d["precool"]["allowed_from_hours"] = [15, 16, 17, 18]
        d["precool"]["allowed_to_hours"] = [13, 14, 15, 16, 17]

    wide = make_scenario(edit)
    assert codes(run({"precool": {"shift_mw": 100, "from_hours": [17, 18], "to_hours": [17]}}, scenario_=wide)) == [
        "PRECOOL_HOURS_OVERLAP"
    ]
    assert codes(run({"precool": {"shift_mw": 100, "from_hours": [17, 17], "to_hours": [13]}}, scenario_=wide)) == [
        "PRECOOL_HOURS_OVERLAP"
    ]


def test_precool_with_zero_shift_is_not_used(run):
    r = run({"precool": {"shift_mw": 0, "from_hours": [5], "to_hours": [99]}})
    assert r.valid
    assert r.disruption_score == 0


# ---------------------------------------------------------------------------
# Demand response
# ---------------------------------------------------------------------------


def test_demand_response_reduces_demand_in_its_hours(run):
    r = run({"demand_response": [{"name": "Program A", "hours": [17, 18, 19]}]})
    assert [hour(r, h).dr_mw for h in (16, 17, 18, 19, 20)] == [0, 400, 400, 400, 0]
    assert hour(r, 17).demand_net == 10600
    assert hour(r, 17).status == YELLOW  # 0% margin


def test_demand_response_programs_stack(run):
    r = run({"demand_response": [{"name": "Program A", "hours": [18]}, {"name": "Program B", "hours": [18]}]})
    assert hour(r, 18).dr_mw == 600


def test_demand_response_rules(run):
    assert codes(run({"demand_response": [{"name": "Program Z", "hours": [18]}]})) == ["DR_UNKNOWN_PROGRAM"]
    assert codes(
        run({"demand_response": [{"name": "Program A", "hours": [17]}, {"name": "Program A", "hours": [18]}]})
    ) == ["DR_DUPLICATE_PROGRAM"]
    assert codes(run({"demand_response": [{"name": "Program A", "hours": [16, 17, 18, 19]}]})) == ["DR_TOO_MANY_HOURS"]
    assert codes(run({"demand_response": [{"name": "Program A", "hours": [15]}]})) == ["DR_HOUR_NOT_ALLOWED"]
    assert codes(run({"demand_response": [{"name": "Program A", "hours": [17, 17]}]})) == ["DR_HOUR_NOT_ALLOWED"]


# ---------------------------------------------------------------------------
# Public alert
# ---------------------------------------------------------------------------


def test_alert_uses_the_low_reduction_in_its_hours(run):
    r = run({"public_alert": {"use": True, "start_hour": 17, "hours": 3}})
    assert [hour(r, h).alert_mw for h in (16, 17, 18, 19, 20)] == [0, 500, 500, 500, 0]
    assert r.disruption_score == 10


def test_alert_needs_alerts_left_this_season(run, make_scenario):
    none_left = make_scenario(lambda d: d["public_alert"].update(alerts_left_this_season=0))
    r = run({"public_alert": {"use": True, "start_hour": 17, "hours": 2}}, scenario_=none_left)
    assert codes(r) == ["ALERT_NONE_LEFT"]


def test_alert_hour_limits(run):
    assert codes(run({"public_alert": {"use": True, "start_hour": 17, "hours": 4}})) == ["ALERT_TOO_MANY_HOURS"]
    assert codes(run({"public_alert": {"use": True, "start_hour": 17, "hours": 0}})) == ["ALERT_TOO_MANY_HOURS"]
    assert codes(run({"public_alert": {"use": True, "start_hour": 22, "hours": 3}})) == ["ALERT_OUTSIDE_DAY"]
    assert codes(run({"public_alert": {"use": True, "start_hour": 24, "hours": 1}})) == ["HOUR_OUT_OF_RANGE"]


# ---------------------------------------------------------------------------
# Disruption score and invalid results
# ---------------------------------------------------------------------------


def test_disruption_score_adds_up(run):
    plan = {
        "battery": [{"hour": 18, "mw": 100}],
        "precool": {"shift_mw": 100, "from_hours": [18], "to_hours": [14]},
        "demand_response": [{"name": "Program A", "hours": [18]}, {"name": "Program B", "hours": [18]}],
        "public_alert": {"use": True, "start_hour": 18, "hours": 1},
    }
    assert run(plan).disruption_score == 1 + 2 + 3 * 2 + 10


def test_invalid_plan_reports_every_violation_and_no_hours(run):
    plan = {
        "battery": [{"hour": 18, "mw": 5000}],
        "demand_response": [{"name": "Nope", "hours": [18]}],
        "public_alert": {"use": True, "start_hour": 17, "hours": 9},
    }
    r = run(plan)
    assert sorted(codes(r)) == ["ALERT_TOO_MANY_HOURS", "BATTERY_ENERGY_EXCEEDED", "BATTERY_POWER_EXCEEDED", "DR_UNKNOWN_PROGRAM"]
    assert not r.valid and not r.all_green
    assert r.hours == () and r.red_hours == () and r.yellow_hours == ()
    assert r.lowest_margin_pct is None and r.lowest_margin_hour is None
    assert r.plan_hash == Plan.from_dict(plan).plan_hash()  # still hashed, so the agent's attempt is traceable


# ---------------------------------------------------------------------------
# simulate_dict (what the agent tool and POST /simulate call)
# ---------------------------------------------------------------------------


def test_simulate_dict_turns_shape_errors_into_violations(scenario, forecast):
    d = simulate_dict(scenario, forecast, {"battery": [{"hour": 18, "mw": -5}]})
    assert d["valid"] is False and d["plan_hash"] is None
    assert d["violations"] == [{"code": "PLAN_SHAPE", "path": "battery[0].mw", "message": "must be 0 or more"}]


def test_simulate_dict_output_is_json_and_rounded(scenario, forecast):
    d = simulate_dict(scenario, forecast, {"battery": [{"hour": 18, "mw": 1000}]})
    json.dumps(d, allow_nan=False)
    assert d["engine"] == "v1" and len(d["hours"]) == 24
    assert d["hours"][18]["margin_pct"] == 5.45
    assert d["hours"][18]["status"] == "YELLOW"
    assert d["red_hours"] == [17, 19, 20]
