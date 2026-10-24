"""Data models for GridGuard contracts C1-C4 (see docs/contracts.md). Owner: P3.

- Forecast (C1) and Scenario (C2) are parsed from our own JSON files.
- Plan (C3) is parsed from model / UI input. Parsing is strict (unknown keys
  are rejected) and normalizes the plan, so the same plan always produces the
  same canonical JSON and the same plan_hash.
- SimResult (C4) is what the simulator returns.

Pure Python, no AI, no AWS calls.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

HOURS_PER_DAY = 24
MW_DECIMALS = 3  # rounding used when normalizing plan values (C3 canonical form)

# Status labels (C3). Always shown as text in the UI, never color alone.
GREEN = "GREEN"
YELLOW = "YELLOW"
RED = "RED"


class ShapeError(ValueError):
    """Input JSON doesn't match the contract shape (missing key, wrong type, unknown key)."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


# ---------------------------------------------------------------------------
# Small parsing helpers
# ---------------------------------------------------------------------------


def _obj(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ShapeError(path, "must be an object")
    return value


def _list(value: Any, path: str) -> List[Any]:
    if not isinstance(value, list):
        raise ShapeError(path, "must be a list")
    return value


def _req(d: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in d:
        raise ShapeError(f"{path}.{key}" if path else key, "is required")
    return d[key]


def _num(value: Any, path: str) -> float:
    # bool is a subclass of int in Python; reject it explicitly.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ShapeError(path, "must be a number")
    return float(value)


def _opt_num(value: Any, path: str) -> Optional[float]:
    return None if value is None else _num(value, path)


def _int(value: Any, path: str) -> int:
    """Whole number. Accepts 18 and 18.0, rejects 18.5 and "18"."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ShapeError(path, "must be a whole number")
    if isinstance(value, float) and not value.is_integer():
        raise ShapeError(path, "must be a whole number")
    return int(value)


def _str(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise ShapeError(path, "must be a string")
    return value


def _bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ShapeError(path, "must be true or false")
    return value


def _no_unknown_keys(d: Mapping[str, Any], allowed: Tuple[str, ...], path: str) -> None:
    for key in d:
        if key not in allowed:
            where = f"{path}.{key}" if path else key
            raise ShapeError(where, f"unknown key (allowed: {', '.join(allowed)})")


def _hours(value: Any, path: str) -> Tuple[int, ...]:
    """List of hours, sorted. Duplicates are kept so validation can reject them."""
    return tuple(sorted(_int(h, f"{path}[{i}]") for i, h in enumerate(_list(value, path))))


def _mw(value: Any, path: str) -> float:
    """Plan MW value: a number >= 0, rounded for the canonical form."""
    mw = _num(value, path)
    if mw < 0:
        raise ShapeError(path, "must be 0 or more")
    return round(mw, MW_DECIMALS)


def _by_hour(value: Any, path: str) -> Tuple[float, ...]:
    values = _list(value, path)
    if len(values) != HOURS_PER_DAY:
        raise ShapeError(path, f"must have {HOURS_PER_DAY} values")
    return tuple(_num(v, f"{path}[{i}]") for i, v in enumerate(values))


# ---------------------------------------------------------------------------
# C1: Forecast
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForecastHour:
    hour: int
    p10: float
    p50: float
    p90: float
    actual: Optional[float]
    caiso_forecast: Optional[float]
    temp_avg: Optional[float]


@dataclass(frozen=True)
class Forecast:
    forecast_id: str
    date: str
    hours: Tuple[ForecastHour, ...]
    timezone: str = "America/Los_Angeles"
    temp_offset_f: float = 0.0
    model_version: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Forecast":
        d = _obj(data, "forecast")
        raw_hours = _list(_req(d, "hours", "forecast"), "forecast.hours")
        hours = []
        for i, raw in enumerate(raw_hours):
            p = f"forecast.hours[{i}]"
            h = _obj(raw, p)
            hours.append(
                ForecastHour(
                    hour=_int(_req(h, "hour", p), f"{p}.hour"),
                    p10=_num(_req(h, "p10", p), f"{p}.p10"),
                    p50=_num(_req(h, "p50", p), f"{p}.p50"),
                    p90=_num(_req(h, "p90", p), f"{p}.p90"),
                    actual=_opt_num(h.get("actual"), f"{p}.actual"),
                    caiso_forecast=_opt_num(h.get("caiso_forecast"), f"{p}.caiso_forecast"),
                    temp_avg=_opt_num(h.get("temp_avg"), f"{p}.temp_avg"),
                )
            )
        hours.sort(key=lambda fh: fh.hour)
        if [fh.hour for fh in hours] != list(range(HOURS_PER_DAY)):
            raise ShapeError("forecast.hours", "must have exactly one entry for each hour 0-23")
        return cls(
            forecast_id=_str(_req(d, "forecast_id", "forecast"), "forecast.forecast_id"),
            date=_str(_req(d, "date", "forecast"), "forecast.date"),
            hours=tuple(hours),
            timezone=_str(d.get("timezone", "America/Los_Angeles"), "forecast.timezone"),
            temp_offset_f=_num(d.get("temp_offset_f", 0), "forecast.temp_offset_f"),
            model_version=_str(d.get("model_version", ""), "forecast.model_version"),
        )

    def p90_by_hour(self) -> List[float]:
        return [fh.p90 for fh in self.hours]


# ---------------------------------------------------------------------------
# C2: Scenario (what's allowed)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Supply:
    firm_mw: float
    solar_mw_by_hour: Tuple[float, ...]
    wind_mw_by_hour: Tuple[float, ...]
    firm_mw_source: str = ""


@dataclass(frozen=True)
class BatteryLimits:
    power_mw: float
    energy_mwh: float
    start_soc_pct: float
    efficiency: float
    note: str = ""

    @property
    def usable_mwh(self) -> float:
        """Energy we can discharge today. No recharge is modeled (ASSUMPTION)."""
        return self.energy_mwh * self.start_soc_pct / 100 * self.efficiency


@dataclass(frozen=True)
class PrecoolLimits:
    allowed_from_hours: Tuple[int, ...]
    allowed_to_hours: Tuple[int, ...]
    max_shift_mw: float
    rebound_pct: float


@dataclass(frozen=True)
class DRProgram:
    name: str
    mw: float
    max_hours: int
    earliest: int  # inclusive
    latest: int  # inclusive


@dataclass(frozen=True)
class AlertReduction:
    low: float
    likely: float
    high: float


@dataclass(frozen=True)
class PublicAlertLimits:
    reduction_mw: AlertReduction
    max_hours: int
    alerts_left_this_season: int


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    date: str
    forecast_id: str
    margin_target_pct: float
    supply: Supply
    battery: BatteryLimits
    precool: PrecoolLimits
    demand_response: Tuple[DRProgram, ...]
    public_alert: PublicAlertLimits
    sources: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Scenario":
        d = _obj(data, "scenario")

        s = _obj(_req(d, "supply", "scenario"), "supply")
        supply = Supply(
            firm_mw=_num(_req(s, "firm_mw", "supply"), "supply.firm_mw"),
            solar_mw_by_hour=_by_hour(_req(s, "solar_mw_by_hour", "supply"), "supply.solar_mw_by_hour"),
            wind_mw_by_hour=_by_hour(_req(s, "wind_mw_by_hour", "supply"), "supply.wind_mw_by_hour"),
            firm_mw_source=_str(s.get("firm_mw_source", ""), "supply.firm_mw_source"),
        )

        b = _obj(_req(d, "battery", "scenario"), "battery")
        battery = BatteryLimits(
            power_mw=_num(_req(b, "power_mw", "battery"), "battery.power_mw"),
            energy_mwh=_num(_req(b, "energy_mwh", "battery"), "battery.energy_mwh"),
            start_soc_pct=_num(_req(b, "start_soc_pct", "battery"), "battery.start_soc_pct"),
            efficiency=_num(_req(b, "efficiency", "battery"), "battery.efficiency"),
            note=_str(b.get("note", ""), "battery.note"),
        )

        pc = _obj(_req(d, "precool", "scenario"), "precool")
        precool = PrecoolLimits(
            allowed_from_hours=_hours(_req(pc, "allowed_from_hours", "precool"), "precool.allowed_from_hours"),
            allowed_to_hours=_hours(_req(pc, "allowed_to_hours", "precool"), "precool.allowed_to_hours"),
            max_shift_mw=_num(_req(pc, "max_shift_mw", "precool"), "precool.max_shift_mw"),
            rebound_pct=_num(_req(pc, "rebound_pct", "precool"), "precool.rebound_pct"),
        )

        programs = []
        for i, raw in enumerate(_list(_req(d, "demand_response", "scenario"), "demand_response")):
            p = f"demand_response[{i}]"
            r = _obj(raw, p)
            programs.append(
                DRProgram(
                    name=_str(_req(r, "name", p), f"{p}.name"),
                    mw=_num(_req(r, "mw", p), f"{p}.mw"),
                    max_hours=_int(_req(r, "max_hours", p), f"{p}.max_hours"),
                    earliest=_int(_req(r, "earliest", p), f"{p}.earliest"),
                    latest=_int(_req(r, "latest", p), f"{p}.latest"),
                )
            )

        pa = _obj(_req(d, "public_alert", "scenario"), "public_alert")
        red = _obj(_req(pa, "reduction_mw", "public_alert"), "public_alert.reduction_mw")
        public_alert = PublicAlertLimits(
            reduction_mw=AlertReduction(
                low=_num(_req(red, "low", "public_alert.reduction_mw"), "public_alert.reduction_mw.low"),
                likely=_num(_req(red, "likely", "public_alert.reduction_mw"), "public_alert.reduction_mw.likely"),
                high=_num(_req(red, "high", "public_alert.reduction_mw"), "public_alert.reduction_mw.high"),
            ),
            max_hours=_int(_req(pa, "max_hours", "public_alert"), "public_alert.max_hours"),
            alerts_left_this_season=_int(
                _req(pa, "alerts_left_this_season", "public_alert"), "public_alert.alerts_left_this_season"
            ),
        )

        sources = {
            _str(k, "sources"): _str(v, f"sources.{k}")
            for k, v in _obj(d.get("sources", {}), "sources").items()
        }

        return cls(
            id=_str(_req(d, "id", "scenario"), "id"),
            name=_str(_req(d, "name", "scenario"), "name"),
            date=_str(_req(d, "date", "scenario"), "date"),
            forecast_id=_str(_req(d, "forecast_id", "scenario"), "forecast_id"),
            margin_target_pct=_num(_req(d, "margin_target_pct", "scenario"), "margin_target_pct"),
            supply=supply,
            battery=battery,
            precool=precool,
            demand_response=tuple(programs),
            public_alert=public_alert,
            sources=sources,
        )


# ---------------------------------------------------------------------------
# C3: Plan (what we do)
# ---------------------------------------------------------------------------

_PLAN_KEYS = ("battery", "precool", "demand_response", "public_alert")


@dataclass(frozen=True)
class BatteryAction:
    hour: int
    mw: float


@dataclass(frozen=True)
class PrecoolAction:
    shift_mw: float = 0.0
    from_hours: Tuple[int, ...] = ()
    to_hours: Tuple[int, ...] = ()


@dataclass(frozen=True)
class DRAction:
    name: str
    hours: Tuple[int, ...]


@dataclass(frozen=True)
class AlertAction:
    use: bool = False
    start_hour: int = 0
    hours: int = 0


@dataclass(frozen=True)
class Plan:
    """A normalized plan. Build it with Plan.from_dict() so the canonical form holds."""

    battery: Tuple[BatteryAction, ...] = ()
    precool: PrecoolAction = field(default_factory=PrecoolAction)
    demand_response: Tuple[DRAction, ...] = ()
    public_alert: AlertAction = field(default_factory=AlertAction)

    @classmethod
    def from_dict(cls, data: Any) -> "Plan":
        """Parse and normalize a plan (C3 canonical form). Raises ShapeError."""
        d = _obj(data, "plan")
        _no_unknown_keys(d, _PLAN_KEYS, "")

        # Battery: drop 0 MW entries, sort by hour (duplicates kept for validation).
        battery = []
        for i, raw in enumerate(_list(d.get("battery", []), "battery")):
            p = f"battery[{i}]"
            b = _obj(raw, p)
            _no_unknown_keys(b, ("hour", "mw"), p)
            action = BatteryAction(hour=_int(_req(b, "hour", p), f"{p}.hour"), mw=_mw(_req(b, "mw", p), f"{p}.mw"))
            if action.mw != 0:
                battery.append(action)
        battery.sort(key=lambda a: a.hour)

        # Pre-cooling: shift 0 means "not used", so the hour lists are cleared.
        precool = PrecoolAction()
        if d.get("precool") is not None:
            pc = _obj(d["precool"], "precool")
            _no_unknown_keys(pc, ("shift_mw", "from_hours", "to_hours"), "precool")
            shift = _mw(_req(pc, "shift_mw", "precool"), "precool.shift_mw")
            from_hours = _hours(pc.get("from_hours", []), "precool.from_hours")
            to_hours = _hours(pc.get("to_hours", []), "precool.to_hours")
            if shift != 0:
                precool = PrecoolAction(shift_mw=shift, from_hours=from_hours, to_hours=to_hours)

        # Demand response: drop programs with no hours, sort by name.
        programs = []
        for i, raw in enumerate(_list(d.get("demand_response", []), "demand_response")):
            p = f"demand_response[{i}]"
            r = _obj(raw, p)
            _no_unknown_keys(r, ("name", "hours"), p)
            action = DRAction(name=_str(_req(r, "name", p), f"{p}.name"), hours=_hours(_req(r, "hours", p), f"{p}.hours"))
            if action.hours:
                programs.append(action)
        programs.sort(key=lambda a: (a.name, a.hours))

        # Public alert: use=false means "not used", so start/hours become 0.
        alert = AlertAction()
        if d.get("public_alert") is not None:
            pa = _obj(d["public_alert"], "public_alert")
            _no_unknown_keys(pa, ("use", "start_hour", "hours"), "public_alert")
            if _bool(_req(pa, "use", "public_alert"), "public_alert.use"):
                alert = AlertAction(
                    use=True,
                    start_hour=_int(_req(pa, "start_hour", "public_alert"), "public_alert.start_hour"),
                    hours=_int(_req(pa, "hours", "public_alert"), "public_alert.hours"),
                )

        return cls(
            battery=tuple(battery),
            precool=precool,
            demand_response=tuple(programs),
            public_alert=alert,
        )

    def to_dict(self) -> Dict[str, Any]:
        """JSON-ready dict with all four sections present."""
        return {
            "battery": [{"hour": a.hour, "mw": a.mw} for a in self.battery],
            "precool": {
                "shift_mw": self.precool.shift_mw,
                "from_hours": list(self.precool.from_hours),
                "to_hours": list(self.precool.to_hours),
            },
            "demand_response": [{"name": a.name, "hours": list(a.hours)} for a in self.demand_response],
            "public_alert": {
                "use": self.public_alert.use,
                "start_hour": self.public_alert.start_hour,
                "hours": self.public_alert.hours,
            },
        }

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)

    def plan_hash(self) -> str:
        """SHA-256 of the canonical JSON. Computed on the server only, never trusted from the model."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def is_no_action(self) -> bool:
        return not (self.battery or self.precool.shift_mw or self.demand_response or self.public_alert.use)


# ---------------------------------------------------------------------------
# C4: Simulator result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    code: str
    path: str
    message: str


@dataclass(frozen=True)
class HourResult:
    hour: int
    demand_p90: float
    precool_delta: float
    dr_mw: float
    alert_mw: float
    demand_net: float
    firm_mw: float
    solar_mw: float
    wind_mw: float
    battery_mw: float
    supply: float
    margin_pct: float
    status: str


@dataclass(frozen=True)
class SimResult:
    engine: str
    plan_hash: Optional[str]
    valid: bool
    violations: Tuple[Violation, ...]
    margin_target_pct: float
    hours: Tuple[HourResult, ...]
    all_green: bool
    lowest_margin_pct: Optional[float]
    lowest_margin_hour: Optional[int]
    red_hours: Tuple[int, ...]
    yellow_hours: Tuple[int, ...]
    disruption_score: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "engine": self.engine,
            "plan_hash": self.plan_hash,
            "valid": self.valid,
            "violations": [{"code": v.code, "path": v.path, "message": v.message} for v in self.violations],
            "margin_target_pct": self.margin_target_pct,
            "hours": [
                {
                    "hour": h.hour,
                    "demand_p90": round(h.demand_p90, 1),
                    "precool_delta": round(h.precool_delta, 1),
                    "dr_mw": round(h.dr_mw, 1),
                    "alert_mw": round(h.alert_mw, 1),
                    "demand_net": round(h.demand_net, 1),
                    "firm_mw": round(h.firm_mw, 1),
                    "solar_mw": round(h.solar_mw, 1),
                    "wind_mw": round(h.wind_mw, 1),
                    "battery_mw": round(h.battery_mw, 1),
                    "supply": round(h.supply, 1),
                    "margin_pct": round(h.margin_pct, 2),
                    "status": h.status,
                }
                for h in self.hours
            ],
            "all_green": self.all_green,
            "lowest_margin_pct": None if self.lowest_margin_pct is None else round(self.lowest_margin_pct, 2),
            "lowest_margin_hour": self.lowest_margin_hour,
            "red_hours": list(self.red_hours),
            "yellow_hours": list(self.yellow_hours),
            "disruption_score": self.disruption_score,
        }
