"""Write scenario files (contract C2) and validate each with Scenario.from_dict. Owner: P1.

Three scenarios share the same resource limits and the same firm_mw:
  sept6_2022           forecast_id sept6_2022
  sept6_2022_plus5f    forecast_id sept6_2022_plus5f (same values as sept6_2022)
  normal_summer_day    date 2022-07-28, forecast_id normal_summer_day

solar_mw_by_hour / wind_mw_by_hour are the 24 local-hour EIA actuals for each scenario's
own date (UTC -> America/Los_Angeles), floored at 0 (EIA reports small negative solar at
night from net metering, which isn't real supply).

firm_mw is tuned so that, with the real simulator and the forecast files in forecasts/:
  - the no-action plan on sept6_2022 is RED across the evening (hours 18-21), and
  - a full plan (battery + both DR programs + max pre-cool) has no RED hours (close to
    ALL_GREEN; true ALL_GREEN isn't reachable because ~3,900 MW of 4-hour batteries plus
    DR and pre-cool can't fully cover the record P90 evening peak).
The same firm_mw is used for all three scenarios.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.shared.models import HOURS_PER_DAY, Forecast, Scenario  # noqa: E402
from backend.shared.simulator import simulate_dict  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"
EIA_PATH = DATA_DIR / "eia_raw.parquet"
FORECAST_DIR = REPO_ROOT / "forecasts"
OUT_DIR = REPO_ROOT / "scenarios"
LOCAL_TZ = "America/Los_Angeles"

MARGIN_TARGET_PCT = 6.0
# firm_mw = (Sept 6 actual demand - solar - wind) at the actual peak hour + CUSHION.
# CUSHION = +500 MW is tuned (see simulate_max_plans) so that on sept6_2022 the "max
# resources" plan A is NOT all-green, but plan A + a 3-hour public alert (plan B) IS
# ALL_GREEN -- the day needs the alert, like the real Sept 6 2022 event -- while
# sept6_2022_plus5f stays not-green even with the alert. No-action sept6 is still RED in
# the evening (hours 19-21).
FIRM_CUSHION_MW = 500.0

BATTERY = {
    "power_mw": 3900,
    "energy_mwh": 15600,
    "start_soc_pct": 90,
    "efficiency": 0.85,
    "note": "ASSUMPTION: batteries assumed charged from midday solar. No recharge modeled.",
}
PRECOOL = {
    "allowed_from_hours": [17, 18, 19, 20],
    "allowed_to_hours": [13, 14, 15, 16],
    "max_shift_mw": 1000,
    "rebound_pct": 30,
}
DR_PROGRAMS = [
    {"name": "Utility DR programs", "mw": 1200, "max_hours": 4, "earliest": 16, "latest": 21},
    {"name": "Emergency Load Reduction", "mw": 500, "max_hours": 3, "earliest": 17, "latest": 21},
]
PUBLIC_ALERT = {
    "reduction_mw": {"low": 1000, "likely": 2000, "high": 2400},
    "max_hours": 3,
    "alerts_left_this_season": 3,
}

# Source / assumption text for every numeric field (keyed by dotted path, per C2).
SOURCES = {
    "margin_target_pct": "ASSUMPTION: 6% operating reserve margin target, in line with NERC-style minimums.",
    "supply.firm_mw": (
        "Estimated from EIA actuals: Sept 6 2022 actual demand minus solar minus wind at the "
        "peak hour, plus a tuned cushion so the no-action evening is RED and the day needs the "
        "public alert to go green (demo calibration, not a measured firm-capacity figure). See docs/sources.md."
    ),
    "supply.solar_mw_by_hour": "EIA CAISO hourly generation by fuel type (SUN), local hours, floored at 0.",
    "supply.wind_mw_by_hour": "EIA CAISO hourly generation by fuel type (WND), local hours, floored at 0.",
    "battery.power_mw": "E Source: CAISO had more than 3,900 MW of batteries, Sept 2022.",
    "battery.energy_mwh": "ASSUMPTION: typical 4-hour batteries (3,900 MW x 4 h = 15,600 MWh).",
    "battery.start_soc_pct": "ASSUMPTION: batteries assumed ~90% charged from midday solar.",
    "battery.efficiency": "ASSUMPTION: 85% round-trip/discharge efficiency for grid batteries.",
    "precool.max_shift_mw": "ASSUMPTION: up to 1,000 MW of load shiftable via utility pre-cooling programs.",
    "precool.rebound_pct": "ASSUMPTION: 30% of shifted load rebounds the hour after pre-cooling ends.",
    "demand_response[0].mw": "ASSUMPTION: utility demand response programs worth ~1,200 MW.",
    "demand_response[1].mw": "ASSUMPTION: emergency load reduction program worth ~500 MW.",
    "public_alert.reduction_mw.low": "Cal Advocates: ~2,000 MW drop in 20-30 min (low end used conservatively, 1,000 MW).",
    "public_alert.reduction_mw.likely": "Cal Advocates: ~2,000 MW demand drop after the Sept 6 2022 text alert.",
    "public_alert.reduction_mw.high": "E Source: up to ~2,400 MW reduction attributed to the alert.",
    "public_alert.max_hours": "ASSUMPTION: a public alert runs at most 3 hours.",
    "public_alert.alerts_left_this_season": "ASSUMPTION: 3 public alerts remain this season.",
}


def _load_eia_local() -> pd.DataFrame:
    e = pd.read_parquet(EIA_PATH)
    e["local"] = (
        e["period_utc"].dt.tz_localize("UTC").dt.tz_convert(LOCAL_TZ).dt.tz_localize(None)
    )
    return e


def _day_hourly(eia_local: pd.DataFrame, date: str) -> pd.DataFrame:
    d = eia_local[eia_local["local"].dt.date == pd.Timestamp(date).date()].copy()
    d["hour"] = d["local"].dt.hour
    d = d.sort_values("hour").reset_index(drop=True)
    if len(d) != 24:
        raise ValueError(f"{date}: expected 24 local hours, got {len(d)}")
    return d


def _floored(series: pd.Series) -> list[float]:
    """Hourly generation as MW, floored at 0 (EIA reports tiny negatives overnight)."""
    return [max(0.0, round(float(x), 1)) for x in series]


def compute_firm_mw(eia_local: pd.DataFrame) -> float:
    """firm_mw = (actual demand - solar - wind) at Sept 6 peak hour + cushion."""
    d = _day_hourly(eia_local, "2022-09-06")
    peak = d.loc[d["demand"].idxmax()]
    net_at_peak = float(peak["demand"] - peak["solar"] - peak["wind"])
    return round(net_at_peak + FIRM_CUSHION_MW, 1)


def build_scenario_dict(
    *, scenario_id: str, name: str, date: str, forecast_id: str, firm_mw: float,
    eia_local: pd.DataFrame,
) -> dict:
    d = _day_hourly(eia_local, date)
    return {
        "id": scenario_id,
        "name": name,
        "date": date,
        "forecast_id": forecast_id,
        "margin_target_pct": MARGIN_TARGET_PCT,
        "supply": {
            "firm_mw": firm_mw,
            "firm_mw_source": SOURCES["supply.firm_mw"],
            "solar_mw_by_hour": _floored(d["solar"]),
            "wind_mw_by_hour": _floored(d["wind"]),
        },
        "battery": dict(BATTERY),
        "precool": dict(PRECOOL),
        "demand_response": [dict(p) for p in DR_PROGRAMS],
        "public_alert": {
            "reduction_mw": dict(PUBLIC_ALERT["reduction_mw"]),
            "max_hours": PUBLIC_ALERT["max_hours"],
            "alerts_left_this_season": PUBLIC_ALERT["alerts_left_this_season"],
        },
        "sources": dict(SOURCES),
    }


def _usable_battery_mwh() -> float:
    """Energy we can discharge today (power x start charge x efficiency). No recharge."""
    return BATTERY["energy_mwh"] * BATTERY["start_soc_pct"] / 100 * BATTERY["efficiency"]


def _base_actions() -> dict:
    """DR at max hours + max pre-cool from 17-19 to 13-15 (shared by plans A and B)."""
    return {
        "precool": {"shift_mw": PRECOOL["max_shift_mw"], "from_hours": [17, 18, 19], "to_hours": [13, 14, 15]},
        "demand_response": [
            {"name": "Utility DR programs", "hours": [18, 19, 20, 21]},
            {"name": "Emergency Load Reduction", "hours": [19, 20, 21]},
        ],
    }


def build_plan_a(scenario: Scenario, forecast: Forecast) -> dict:
    """Plan A: full battery spread over the worst (RED/YELLOW) hours within power and
    usable energy, plus both DR programs at max hours and max pre-cool."""
    usable = _usable_battery_mwh()
    power = BATTERY["power_mw"]
    battery: dict[int, float] = {}
    remaining = usable
    # Greedily add battery to the lowest-margin non-green hour that isn't already at full
    # power, recomputing after each placement, until energy runs out (<= 24 placements).
    for _ in range(HOURS_PER_DAY):
        plan = dict(_base_actions())
        plan["battery"] = [{"hour": h, "mw": round(mw, 3)} for h, mw in sorted(battery.items())]
        plan["public_alert"] = {"use": False, "start_hour": 0, "hours": 0}
        result = simulate_dict(scenario, forecast, plan)
        candidates = [
            (h["hour"], h["margin_pct"])
            for h in result["hours"]
            if h["status"] != "GREEN" and h["battery_mw"] < power - 1e-6
        ]
        if not candidates or remaining <= 1e-6:
            break
        candidates.sort(key=lambda x: x[1])  # worst margin first
        hour = candidates[0][0]
        add = min(power - battery.get(hour, 0.0), remaining)
        if add <= 1e-6:
            break
        battery[hour] = battery.get(hour, 0.0) + add
        remaining -= add

    plan = dict(_base_actions())
    plan["battery"] = [{"hour": h, "mw": round(mw, 3)} for h, mw in sorted(battery.items())]
    plan["public_alert"] = {"use": False, "start_hour": 0, "hours": 0}
    return plan


def build_plan_b(scenario: Scenario, forecast: Forecast, plan_a: dict) -> dict:
    """Plan B: plan A plus a 3-hour public alert placed at the worst 3-hour window."""
    hours = PUBLIC_ALERT["max_hours"]
    best_start, best_key = 0, None
    for start in range(0, HOURS_PER_DAY - hours + 1):
        plan = dict(plan_a)
        plan["public_alert"] = {"use": True, "start_hour": start, "hours": hours}
        result = simulate_dict(scenario, forecast, plan)
        key = (result["all_green"], result["lowest_margin_pct"])
        if best_key is None or key > best_key:
            best_key, best_start = key, start
    plan = dict(plan_a)
    plan["public_alert"] = {"use": True, "start_hour": best_start, "hours": hours}
    return plan


def _status_line(result: dict) -> str:
    statuses = {h["hour"]: h["status"] for h in result["hours"]}
    return " ".join(f"{h:02d}:{statuses[h][0]}" for h in range(HOURS_PER_DAY))


def simulate_max_plans(scenario: Scenario, forecast: Forecast) -> dict:
    """Simulate plan A and plan B, printing hourly status and lowest margin."""
    plan_a = build_plan_a(scenario, forecast)
    res_a = simulate_dict(scenario, forecast, plan_a)
    plan_b = build_plan_b(scenario, forecast, plan_a)
    res_b = simulate_dict(scenario, forecast, plan_b)

    print(f"  {scenario.id}")
    print(f"    A (battery+DR+precool)        {_status_line(res_a)}")
    print(
        f"      all_green={res_a['all_green']} RED={res_a['red_hours']} "
        f"lowest_margin={res_a['lowest_margin_pct']}% @ hour {res_a['lowest_margin_hour']}"
    )
    alert = plan_b["public_alert"]
    print(
        f"    B (A + {alert['hours']}h alert @ h{alert['start_hour']})  {_status_line(res_b)}"
    )
    print(
        f"      all_green={res_b['all_green']} RED={res_b['red_hours']} "
        f"lowest_margin={res_b['lowest_margin_pct']}% @ hour {res_b['lowest_margin_hour']}"
    )
    return {"a_all_green": res_a["all_green"], "b_all_green": res_b["all_green"]}


def _goal_met(eia_local: pd.DataFrame, firm_mw: float) -> bool:
    """sept6: A not green, B green. plus5f: B not green."""
    s6, f6 = _scn_fc(eia_local, "sept6_2022", "2022-09-06", "sept6_2022", firm_mw)
    s5, f5 = _scn_fc(eia_local, "sept6_2022_plus5f", "2022-09-06", "sept6_2022_plus5f", firm_mw)
    a6 = simulate_dict(s6, f6, build_plan_a(s6, f6))
    b6 = simulate_dict(s6, f6, build_plan_b(s6, f6, build_plan_a(s6, f6)))
    b5 = simulate_dict(s5, f5, build_plan_b(s5, f5, build_plan_a(s5, f5)))
    return (not a6["all_green"]) and b6["all_green"] and (not b5["all_green"])


def _scn_fc(
    eia_local: pd.DataFrame, scenario_id: str, date: str, forecast_id: str, firm_mw: float
) -> tuple[Scenario, Forecast]:
    doc = build_scenario_dict(
        scenario_id=scenario_id, name=scenario_id, date=date,
        forecast_id=forecast_id, firm_mw=firm_mw, eia_local=eia_local,
    )
    scenario = Scenario.from_dict(doc)
    forecast = Forecast.from_dict(
        json.loads((FORECAST_DIR / f"{forecast_id}.json").read_text(encoding="utf-8"))
    )
    return scenario, forecast


def resolve_firm_mw(eia_local: pd.DataFrame) -> float:
    """Firm_mw from the configured cushion; if the A/B goal isn't met, adjust the
    cushion (and only the cushion) until it is, then report the new value."""
    firm_mw = compute_firm_mw(eia_local)
    if _goal_met(eia_local, firm_mw):
        return firm_mw

    net_at_peak = firm_mw - FIRM_CUSHION_MW
    print("Configured cushion does not meet the A/B goal; searching cushion ...")
    # Scan cushions; prefer the one closest to the configured value that meets the goal.
    candidates = sorted(range(-2000, 2001, 100), key=lambda c: abs(c - FIRM_CUSHION_MW))
    for cushion in candidates:
        candidate = round(net_at_peak + cushion, 1)
        if _goal_met(eia_local, candidate):
            print(f"Adjusted firm_mw cushion: {cushion:+.0f} MW -> firm_mw {candidate:.0f} MW")
            return candidate
    print("No cushion in the search range met the goal; keeping configured value.")
    return firm_mw


def _print_no_action_status(scenario: Scenario, forecast: Forecast) -> None:
    result = simulate_dict(scenario, forecast, {})
    statuses = {h["hour"]: h["status"] for h in result["hours"]}
    line = " ".join(f"{h:02d}:{statuses[h][0]}" for h in range(24))
    print(f"  {scenario.id}")
    print(f"    {line}")
    print(
        f"    RED={result['red_hours']} YELLOW={result['yellow_hours']} "
        f"lowest_margin={result['lowest_margin_pct']}% @ hour {result['lowest_margin_hour']}"
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    eia_local = _load_eia_local()
    firm_mw = resolve_firm_mw(eia_local)
    print(f"firm_mw (shared): {firm_mw:.0f} MW")

    specs = [
        dict(scenario_id="sept6_2022", name="Sept 6, 2022 heat wave",
             date="2022-09-06", forecast_id="sept6_2022"),
        dict(scenario_id="sept6_2022_plus5f", name="Sept 6, 2022 heat wave +5F",
             date="2022-09-06", forecast_id="sept6_2022_plus5f"),
        dict(scenario_id="normal_summer_day", name="Normal summer day (Jul 28, 2022)",
             date="2022-07-28", forecast_id="normal_summer_day"),
    ]

    scenarios: list[tuple[Scenario, Forecast]] = []
    for spec in specs:
        doc = build_scenario_dict(firm_mw=firm_mw, eia_local=eia_local, **spec)
        path = OUT_DIR / f"{spec['scenario_id']}.json"
        path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        scenario = Scenario.from_dict(json.loads(path.read_text(encoding="utf-8")))
        forecast = Forecast.from_dict(
            json.loads((FORECAST_DIR / f"{spec['forecast_id']}.json").read_text(encoding="utf-8"))
        )
        scenarios.append((scenario, forecast))
        print(f"Wrote {path.name} (validated against Scenario.from_dict)")

    print("\n=== No-action hourly status (G/Y/R per hour) ===")
    for scenario, forecast in scenarios:
        _print_no_action_status(scenario, forecast)

    print("\n=== Max plans A (resources) and B (A + public alert) ===")
    for scenario, forecast in scenarios:
        if scenario.id in ("sept6_2022", "sept6_2022_plus5f"):
            simulate_max_plans(scenario, forecast)


if __name__ == "__main__":
    main()
