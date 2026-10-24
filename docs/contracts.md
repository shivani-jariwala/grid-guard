# GridGuard contracts (C1–C6)

These are the data shapes every area builds against. If you need to change one, post in the team chat first and update this file in the same PR.

| ID | What | Owner | Status |
|---|---|---|---|
| C1 | Forecast JSON | P1 | Draft by P3. **P1 to confirm.** |
| C2 | Scenario JSON | P3 (schema) + P5 (values) | Agreed |
| C3 | Plan JSON + simulator math and rules | P3 | Agreed |
| C4 | Simulator result + plan hash | P3 (+ P2 for the submit gate) | Agreed |
| C5 | Run record, DynamoDB tables, API | P3 | Agreed |
| C6 | Agent tools + briefing | P2 | Draft by P3. **P2 to confirm.** |

General rules:
- All power is in **MW**, energy in **MWh**, temperature in **°F**, margins in **%**.
- Hours are integers **0–23**, local time `America/Los_Angeles`. Scenario dates must not be DST change days, so every day has exactly 24 hours.
- Timestamps are ISO-8601 UTC, e.g. `2026-10-24T03:15:00Z`.
- Field names are `snake_case`.

---

## C1: Forecast

Stored at `s3://<bucket>/forecasts/{forecast_id}.json`. One file per forecast (`sept6_2022`, `sept6_2022_plus5F`).

```json
{
  "forecast_id": "sept6_2022",
  "date": "2022-09-06",
  "timezone": "America/Los_Angeles",
  "temp_offset_f": 0,
  "model_version": "xgb-quantile-v1",
  "hours": [
    { "hour": 0, "p10": 0, "p50": 0, "p90": 0, "actual": 0, "caiso_forecast": 0, "temp_avg": 0 }
  ]
}
```

- `hours` has exactly 24 entries, sorted by `hour` 0–23.
- `p10 ≤ p50 ≤ p90` in every hour. P1 fixes any quantile crossing before export.
- `actual` and `caiso_forecast` can be `null` (for example, synthetic +5°F days have no real actual).
- `temp_avg` is the average of the 5 cities, after `temp_offset_f` is applied.
- MW values are rounded to whole numbers. Temperatures to 1 decimal.

---

## C2: Scenario (what's allowed)

Stored in the repo at `scenarios/{id}.json` and served by `GET /resources`.

```json
{
  "id": "sept6_2022",
  "name": "Sept 6, 2022 heat wave",
  "date": "2022-09-06",
  "forecast_id": "sept6_2022",
  "margin_target_pct": 6,
  "supply": {
    "firm_mw": 0,
    "firm_mw_source": "estimated from EIA actuals",
    "solar_mw_by_hour": [0, "... 24 values"],
    "wind_mw_by_hour": [0, "... 24 values"]
  },
  "battery": {
    "power_mw": 0,
    "energy_mwh": 0,
    "start_soc_pct": 90,
    "efficiency": 0.85,
    "note": "ASSUMPTION: batteries assumed charged from midday solar. No recharge modeled."
  },
  "precool": {
    "allowed_from_hours": [17, 18, 19, 20],
    "allowed_to_hours": [13, 14, 15, 16],
    "max_shift_mw": 0,
    "rebound_pct": 30
  },
  "demand_response": [
    { "name": "Program A", "mw": 0, "max_hours": 4, "earliest": 16, "latest": 21 }
  ],
  "public_alert": {
    "reduction_mw": { "low": 0, "likely": 0, "high": 0 },
    "max_hours": 3,
    "alerts_left_this_season": 3
  },
  "sources": {
    "supply.firm_mw": "estimated from EIA actuals: <numbers and cushion, see docs/sources.md>",
    "battery.power_mw": "ASSUMPTION: <reason>"
  }
}
```

- `sept6_2022_plus5F` is the same file with `id`, `name` and `forecast_id` changed to the +5°F forecast.
- `solar_mw_by_hour` and `wind_mw_by_hour` have 24 values each (EIA actuals for that date).
- `earliest` / `latest` are inclusive hours. Demand response program names are unique.
- **`sources`:** every numeric value has an entry, keyed by its dotted path (`demand_response[0].mw`). The text is a source, or starts with `ASSUMPTION:` followed by a reason. The UI uses this for its source tooltips.
- Usable battery energy = `energy_mwh × start_soc_pct / 100 × efficiency`. No recharge is modeled.

---

## C3: Plan (what we do) and the simulator rules

The agent sends this to `simulate` and `submit_plan`. The UI sends it to `POST /simulate`.

```json
{
  "battery": [{ "hour": 18, "mw": 500 }],
  "precool": { "shift_mw": 300, "from_hours": [17, 18, 19], "to_hours": [13, 14, 15] },
  "demand_response": [{ "name": "Program A", "hours": [17, 18, 19] }],
  "public_alert": { "use": false, "start_hour": 0, "hours": 0 }
}
```

- **No-action plan:** `{}` or all four sections empty/off. A missing section means "not used".
- **Unknown keys are rejected** (shape error), so typos don't silently turn into "no action".
- Hours must be whole numbers (`18` or `18.0`). MW values must be numbers ≥ 0.

### Canonical form (used for the hash)

When a plan is parsed, it's normalized before anything else:

1. All MW values become floats rounded to 3 decimals (`500` and `500.0` are the same).
2. `battery` entries with `mw = 0` are dropped. The rest are sorted by `hour`.
3. Every hour list is sorted. Duplicates are kept, so validation can reject them.
4. `demand_response` entries with no hours are dropped. The rest are sorted by `name`.
5. If `precool.shift_mw = 0`, `from_hours` and `to_hours` become `[]`.
6. If `public_alert.use = false`, `start_hour` and `hours` become `0`.

Canonical JSON = the normalized plan with all four sections present, `json.dumps(plan, sort_keys=True, separators=(",", ":"))`. `plan_hash` = SHA-256 hex of that string. This resolves open question 9.

### Validation rules

The simulator returns every violation it finds, not just the first one. A plan with any violation is **invalid**.

| Code | Rule |
|---|---|
| `PLAN_SHAPE` | Missing required field, wrong type or unknown key. |
| `HOUR_OUT_OF_RANGE` | Any hour outside 0–23. |
| `BATTERY_DUPLICATE_HOUR` | Two battery entries for the same hour. |
| `BATTERY_POWER_EXCEEDED` | Any hour's `mw` > `battery.power_mw`. |
| `BATTERY_ENERGY_EXCEEDED` | Sum of battery `mw` (1 hour each, so MWh) > usable battery energy. |
| `PRECOOL_SHIFT_EXCEEDED` | `shift_mw` > `max_shift_mw`. |
| `PRECOOL_HOURS_MISSING` | `shift_mw` > 0 but `from_hours` or `to_hours` is empty. |
| `PRECOOL_HOUR_NOT_ALLOWED` | A `from_hours` hour not in `allowed_from_hours`, or a `to_hours` hour not in `allowed_to_hours`. |
| `PRECOOL_HOURS_OVERLAP` | An hour appears in both `from_hours` and `to_hours`, or twice in one list. Resolves open question 10. |
| `DR_UNKNOWN_PROGRAM` | `name` isn't in the scenario. |
| `DR_DUPLICATE_PROGRAM` | The same program appears twice. |
| `DR_TOO_MANY_HOURS` | More hours than the program's `max_hours`. |
| `DR_HOUR_NOT_ALLOWED` | An hour outside `earliest`–`latest`, or a duplicate hour. |
| `ALERT_NONE_LEFT` | `use = true` but `alerts_left_this_season = 0`. |
| `ALERT_TOO_MANY_HOURS` | `hours` < 1 or > `max_hours`. |
| `ALERT_OUTSIDE_DAY` | `start_hour + hours − 1` > 23. |

### Math (for each hour h, only for valid plans)

```
# Pre-cooling moves energy. It doesn't remove it.
precool_delta[h] = 0 for every h
if shift_mw > 0:
    shifted_energy = shift_mw × len(from_hours)
    for h in from_hours: precool_delta[h] −= shift_mw
    for h in to_hours:   precool_delta[h] += shifted_energy / len(to_hours)
    rebound_hour = max(from_hours) + 1
    if rebound_hour ≤ 23: precool_delta[rebound_hour] += shift_mw × rebound_pct / 100

dr_mw[h]     = sum of program.mw for each program running in hour h
alert_mw[h]  = public_alert.reduction_mw.low if the alert covers h, else 0   (be conservative)
battery_mw[h] = battery mw for hour h, else 0

demand_net[h] = p90[h] + precool_delta[h] − dr_mw[h] − alert_mw[h]
supply[h]     = firm_mw + solar[h] + wind[h] + battery_mw[h]
margin_pct[h] = (supply[h] − demand_net[h]) / demand_net[h] × 100

status[h] = GREEN  if margin_pct ≥ margin_target_pct
            YELLOW if 0 ≤ margin_pct < margin_target_pct
            RED    if margin_pct < 0
```

- Status is decided on the unrounded margin. The output rounds `margin_pct` to 2 decimals and MW to 1.
- `margin_target_pct` comes from the scenario unless the caller overrides it.

### Disruption score (lower is better)

| Action | Points |
|---|---|
| Battery (any hour with mw > 0) | 1 |
| Pre-cooling (`shift_mw` > 0) | 2 |
| Demand response | 3 **per program used** |
| Public alert (`use = true`) | 10 |

---

## C4: Simulator result

Python entry points (in `backend/shared/simulator.py`):

```python
simulate(scenario: Scenario, forecast: Forecast, plan: Plan,
         margin_target_pct: float | None = None) -> SimResult

simulate_dict(scenario: Scenario, forecast: Forecast, plan: dict,
              margin_target_pct: float | None = None) -> dict   # JSON-ready, never raises on a bad plan
```

JSON shape (`SimResult.to_dict()`):

```json
{
  "engine": "v1",
  "plan_hash": "3f2a…",
  "valid": true,
  "violations": [
    { "code": "BATTERY_POWER_EXCEEDED", "path": "battery[0].mw", "message": "Hour 18: 900 MW is above the 800 MW battery limit." }
  ],
  "margin_target_pct": 6,
  "hours": [
    {
      "hour": 18, "demand_p90": 0, "precool_delta": 0, "dr_mw": 0, "alert_mw": 0, "demand_net": 0,
      "firm_mw": 0, "solar_mw": 0, "wind_mw": 0, "battery_mw": 0, "supply": 0,
      "margin_pct": 0, "status": "RED"
    }
  ],
  "all_green": false,
  "lowest_margin_pct": -2.15,
  "lowest_margin_hour": 18,
  "red_hours": [18, 19],
  "yellow_hours": [17, 20],
  "disruption_score": 0
}
```

- `engine` is `"v1"` (the real simulator, P3-08). It was `"stub"` during P3-15. The UI can show a "STUB DATA" badge if it ever sees `"stub"`.
- Broken input data (net demand ≤ 0 in any hour) raises `ValueError`. That's a data bug, not a plan problem, so the API returns `500`.
- **Invalid plan:** `valid = false`, `hours = []`, `all_green = false`, `lowest_margin_* = null`, `red_hours = yellow_hours = []`.
- **Shape error** (`PLAN_SHAPE`): same as invalid, plus `plan_hash = null`, because the plan couldn't be normalized.

### Plan hash and the submit gate

- **The server computes every hash. The model never does.** Any hash the model sends is ignored.
- Every `simulate` call appends `{n, plan_hash, plan, result, at}` to `Runs.simulations[]` (C5).
- `submit_plan` parses the plan it receives, recomputes the hash, and accepts it only if **all** of these hold:
  1. the hash matches an entry in this run's `simulations[]`;
  2. that result is `valid = true`;
  3. the claimed status matches: `ALL_GREEN` needs `all_green = true`; `CANNOT_MEET_TARGET` is allowed for any valid result.

Rule 2 is stricter than decision 6 as written: an invalid plan can't be submitted, even as `CANNOT_MEET_TARGET`.

---

## C5: Run record, tables and API

### DynamoDB tables

**`Runs`** (partition key `run_id`)

| Field | Type | Notes |
|---|---|---|
| `run_id` | string | UUID4 |
| `scenario_id` | string | |
| `margin_target_pct` | number | Value used for this run |
| `model_id` | string | Bedrock model / inference profile ID |
| `status` | string | `running` · `done` · `failed` |
| `created_at`, `updated_at` | string | ISO-8601 UTC |
| `simulations` | list | `{n, plan_hash, plan, result, at}` per `simulate` call, in order |
| `trace` | list | `{step, tool, input, output_summary, at, duration_ms}` per tool call. `output_summary` ≤ 500 chars. |
| `counts` | map | `{simulate_calls, tool_calls}` |
| `final` | map or null | `{status, plan_hash, plan, result, briefing}` (briefing = C6) |
| `flags` | list | `{code, detail}`, e.g. `NUMBER_MISMATCH`, `GUARDRAIL_INTERVENED`, `LOOP_LIMIT` |
| `error` | map or null | `{code, message}` when `status = failed` |

Size check: 5 simulations × ~5 KB + a 12-step trace stays well under DynamoDB's 400 KB item limit.

**`Decisions`** (partition key `run_id`, sort key `timestamp`)

| Field | Type | Notes |
|---|---|---|
| `run_id`, `timestamp` | string | |
| `operator_name` | string | Required, 1–60 chars |
| `decision` | string | `APPROVE` · `EDIT` · `REJECT` |
| `plan_hash` | string | The plan the decision applies to |
| `edits` | map | `{alert_text}` (≤ 160 chars), only for `EDIT` |
| `note` | string | Optional, ≤ 500 chars |

**`Counters`** (partition key `day`, e.g. `2026-10-23` in Pacific time)

- `runs` (number). `agent_start` does an atomic `ADD runs 1` with the condition `runs < 100`. If the condition fails, it returns `429 DAILY_LIMIT_REACHED`.

### API (HTTP API, JSON)

Errors from our Lambdas always look like:

```json
{ "error": { "code": "SCENARIO_NOT_FOUND", "message": "No scenario called 'abc'." } }
```

API Gateway's own throttling returns `429` with `{"message": "Too Many Requests"}`. The frontend handles both shapes.

| Method + path | Request | Success | Errors |
|---|---|---|---|
| `GET /scenarios` | none | `200 {"scenarios": [{id, name, date, margin_target_pct}]}` | |
| `GET /forecast?scenario={id}` | none | `200` C1 (the scenario's `forecast_id`) | `404 SCENARIO_NOT_FOUND` |
| `GET /resources?scenario={id}` | none | `200` C2 | `404 SCENARIO_NOT_FOUND` |
| `POST /simulate` | `{scenario_id, plan, margin_target_pct?}` | `200` C4 (an invalid plan is still a `200` with `valid: false`) | `400 BAD_REQUEST`, `404 SCENARIO_NOT_FOUND` |
| `POST /runs` | `{scenario_id, margin_target_pct?}` | `202 {"run_id", "status": "running"}` | `400`, `404`, `429 DAILY_LIMIT_REACHED` |
| `GET /runs/{run_id}` | none | `200` the full `Runs` item | `404 RUN_NOT_FOUND` |
| `POST /runs/{run_id}/decision` | `{operator_name, decision, note?, edits?}` | `201` the `Decisions` item | `400`, `404`, `409 RUN_NOT_DONE` |
| `GET /audit?limit=50` | none | `200 {"decisions": [...]}`, newest first | |

- `GET /audit` scans `Decisions` and sorts in the Lambda. That's fine at hackathon scale.
- The frontend polls `GET /runs/{run_id}` every 2 seconds until `status` isn't `running`.
- No login. Cost protection is the agent loop limits + throttling + the `Counters` cap. (Budgets alarm deferred, see P3-16 in `tasks.md`.)

---

## C6: Agent tools and briefing (draft, P2 to confirm)

**The run's scenario is bound on the server.** Tools don't take a `scenario_id`, so the model can't switch scenarios mid-run. This follows the same idea as the plan hash.

| Tool | Input | Returns |
|---|---|---|
| `get_forecast` | `{}` | `{date, hours: [{hour, p10, p50, p90}], peak: {hour, p90}, risk_window: {start_hour, end_hour} or null}` |
| `get_resources` | `{}` | C2 without `sources`, plus `margin_target_pct` for this run |
| `simulate` | `{plan}` (C3) | C4 |
| `draft_alert` | `{start_hour, hours}` | `{start_hour, end_hour, hours, reduction_mw_low, reduction_mw_likely, alerts_left_this_season, max_chars: 160}` |
| `submit_plan` | `{plan, status, rationale, risks, alert_text}` | `{accepted: true}` or `{accepted: false, reason}` |

- `get_forecast` never returns `actual` or `caiso_forecast`. Actual demand is the future, and giving it to the agent would be cheating.
- `risk_window` = first to last hour that isn't GREEN under the no-action plan (computed with the simulator). `null` if every hour is GREEN.
- `submit_plan` inputs:
  - `status`: `ALL_GREEN` or `CANNOT_MEET_TARGET`;
  - `rationale`, `risks`: up to 3 short strings each;
  - `alert_text`: ≤ 160 chars, or `null` if no alert.
  - The gate in C4 applies.

### Briefing (stored in `Runs.final.briefing`)

```json
{
  "status": "ALL_GREEN",
  "actions": [
    { "action": "battery", "label": "Battery discharge", "hours": [18, 19], "mw": 500 }
  ],
  "rationale": ["…", "…", "…"],
  "risks": ["…", "…", "…"],
  "alert_text": null,
  "alert_label": "Draft only. Not sent.",
  "number_check": { "passed": true, "mismatches": [] }
}
```

- **`actions` is built by code from the accepted plan and its result, not written by the model.** That keeps the action table's numbers out of the number guard's scope.
- `rationale`, `risks` and `alert_text` are the model's words. The number guard checks every number in them against the tool outputs of this run. Mismatches go to `number_check.mismatches` as `{text, value}` and add a `NUMBER_MISMATCH` flag to the run.
