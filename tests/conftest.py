"""Shared test data for the simulator and models.

Numbers are picked so results can be checked by hand:
- P90 demand is 10,000 MW all day, except 11,000 MW at hours 17-20 (the evening peak).
- Firm supply is 10,600 MW, with no solar or wind.
  - Normal hours:  (10,600 - 10,000) / 10,000 = 6.0%  -> exactly the 6% target -> GREEN
  - Evening hours: (10,600 - 11,000) / 11,000 = -3.6% -> RED
- Battery: 1,000 MW, 4,000 MWh, 100% charged, 75% efficient -> 3,000 MWh usable.
"""

from __future__ import annotations

import copy
from typing import Any, Callable, Dict, List, Optional

import pytest

from shared.models import Forecast, Plan, Scenario, SimResult
from shared.simulator import simulate

EVENING = (17, 18, 19, 20)


def p90_profile() -> List[float]:
    return [11000.0 if h in EVENING else 10000.0 for h in range(24)]


def forecast_dict(p90: Optional[List[float]] = None) -> Dict[str, Any]:
    p90 = p90 or p90_profile()
    return {
        "forecast_id": "test_day",
        "date": "2022-09-06",
        "hours": [
            {
                "hour": h,
                "p10": p90[h] * 0.9,
                "p50": p90[h] * 0.95,
                "p90": p90[h],
                "actual": None,
                "caiso_forecast": None,
                "temp_avg": 100.0,
            }
            for h in range(24)
        ],
    }


BASE_SCENARIO: Dict[str, Any] = {
    "id": "test_day",
    "name": "Test day",
    "date": "2022-09-06",
    "forecast_id": "test_day",
    "margin_target_pct": 6,
    "supply": {
        "firm_mw": 10600,
        "firm_mw_source": "test value",
        "solar_mw_by_hour": [0] * 24,
        "wind_mw_by_hour": [0] * 24,
    },
    "battery": {"power_mw": 1000, "energy_mwh": 4000, "start_soc_pct": 100, "efficiency": 0.75},
    "precool": {
        "allowed_from_hours": [17, 18, 19, 20],
        "allowed_to_hours": [13, 14, 15, 16],
        "max_shift_mw": 600,
        "rebound_pct": 50,
    },
    "demand_response": [
        {"name": "Program A", "mw": 400, "max_hours": 3, "earliest": 16, "latest": 21},
        {"name": "Program B", "mw": 200, "max_hours": 4, "earliest": 12, "latest": 22},
    ],
    "public_alert": {
        "reduction_mw": {"low": 500, "likely": 800, "high": 1200},
        "max_hours": 3,
        "alerts_left_this_season": 2,
    },
}


@pytest.fixture
def scenario() -> Scenario:
    return Scenario.from_dict(BASE_SCENARIO)


@pytest.fixture
def forecast() -> Forecast:
    return Forecast.from_dict(forecast_dict())


@pytest.fixture
def make_scenario() -> Callable[[Callable[[Dict[str, Any]], None]], Scenario]:
    """Build a scenario from the base one after applying an edit, e.g.
    make_scenario(lambda d: d["public_alert"].update(alerts_left_this_season=0))."""

    def _make(edit: Callable[[Dict[str, Any]], None]) -> Scenario:
        d = copy.deepcopy(BASE_SCENARIO)
        edit(d)
        return Scenario.from_dict(d)

    return _make


@pytest.fixture
def run(scenario: Scenario, forecast: Forecast) -> Callable[..., SimResult]:
    """run(plan_dict) -> SimResult, with optional scenario / forecast / target overrides."""

    def _run(
        plan: Dict[str, Any],
        scenario_: Optional[Scenario] = None,
        forecast_: Optional[Forecast] = None,
        target: Optional[float] = None,
    ) -> SimResult:
        return simulate(scenario_ or scenario, forecast_ or forecast, Plan.from_dict(plan), target)

    return _run
