# GridGuard: Team Tasks

Planning only. No code yet. Each task has an ID, an owner, what it needs first, and a "done when" check.

- `[ ]` = to do, `[x]` = done
- **Needs:** tasks that must finish first
- **Done when:** how we know it's finished
- Task IDs are stable. New tasks get the next free number, so some phases list IDs out of order.

---

## Team and ownership

| Person | Role | Owns |
|---|---|---|
| **P1** | ML | `ml/`, forecasts in S3, `forecast_api`, number guard tests |
| **P2** | Agent | Bedrock access, tools, plan hashing, system prompt, `agent_worker` loop, Guardrails, `number_guard.py` |
| **P3** | Simulator + backend | Kiro setup, SAM template, `simulator.py`, APIs, DynamoDB, IAM, authorizer + rate limits, budget alarm |
| **P4** | Frontend | `frontend/`, Amplify Hosting, all 6 screens, demo replay switch |
| **P5** | Data, story, eval | Fact checks, `scenarios/*.json` values, `firm_mw` estimate, `eval/`, README, slides, demo |

---

## Shared contracts (agree on these TONIGHT)

Everyone builds against these, so they must be written down before real work starts. Put them in `docs/contracts.md`. Changes after tonight need a heads-up in the team chat.

| ID | Contract | Owner | Used by |
|---|---|---|---|
| C1 | Forecast JSON (`hour, p10, p50, p90, actual, caiso_forecast, temp_avg`) | P1 | P2, P3, P4 |
| C2 | Scenario JSON (schema by P3, values by P5). Details below. | P3 + P5 | P2, P4 |
| C3 | Plan JSON + pre-cool math. Details below. | P3 | P2, P4 |
| C4 | Simulator result JSON + plan hash. Details below. | P3 (+ P2 for hashing) | P2, P4 |
| C5 | Run record + API responses (`/runs`, `/runs/{id}`, `/audit`). Every request sends the `x-demo-passcode` header. | P3 | P4 |
| C6 | Briefing shape (status, actions, rationale, risks, alert_text) + tool schemas | P2 | P1, P4 |

### C2: Scenario (what's allowed)

Pre-cool and supply fields changed. Everything else stays as in section 8 of the plan.

```json
{
  "supply": {
    "firm_mw": 0,
    "firm_mw_source": "estimated from EIA actuals",
    "solar_mw_by_hour": [],
    "wind_mw_by_hour": []
  },
  "battery": {
    "power_mw": 0, "energy_mwh": 0, "start_soc_pct": 90, "efficiency": 0.85,
    "note": "ASSUMPTION: batteries assumed charged from midday solar. No recharge modeled."
  },
  "precool": {
    "allowed_from_hours": [17, 18, 19, 20],
    "allowed_to_hours": [13, 14, 15, 16],
    "max_shift_mw": 0,
    "rebound_pct": 30
  }
}
```

- `precool_hours` is gone. It was replaced by `allowed_from_hours` / `allowed_to_hours`.
- `firm_mw` comes from P5-11 (decision 5).
- Battery: no charging is modeled. The battery starts at `start_soc_pct` (decision 4).

### C3: Plan (what we do) and the pre-cool math

```json
{
  "precool": { "shift_mw": 0, "from_hours": [17, 18, 19], "to_hours": [13, 14, 15] }
}
```

Validation (the simulator rejects the plan with a violation message if any rule fails):
- `shift_mw` ≤ `max_shift_mw`
- every `from_hours` hour is in `allowed_from_hours`
- every `to_hours` hour is in `allowed_to_hours`

Pre-cool math. Pre-cooling moves energy. It doesn't remove it.

```
shifted_energy = shift_mw × len(from_hours)
for h in from_hours: demand[h] −= shift_mw
for h in to_hours:   demand[h] += shifted_energy / len(to_hours)
rebound_hour = last(from_hours) + 1
if rebound_hour ≤ 23: demand[rebound_hour] += shift_mw × rebound_pct / 100
else: ignore the rebound
```

Full demand per hour:

```
demand_net[h] = p90[h] + precool_delta[h] − dr_mw[h] − alert_reduction_LOW[h]
```

### C4: Simulator result and plan hash

- Same as before: hourly status, lowest margin and its hour, violations, disruption score.
- The result also includes `plan_hash` and `all_green` (true/false).
- **Hashing happens on the server, never in the model:**
  - `simulate` computes SHA-256 of the canonical plan JSON (sorted keys, no whitespace).
  - It stores `{plan_hash, result}` in the run record (`Runs.simulations[]`, see C5).
  - `submit_plan` recomputes the hash from the plan it receives. It accepts the plan only if:
    - the hash matches a plan already simulated **in this run**, and
    - that result was `all_green`, or the submitted status is `CANNOT_MEET_TARGET`.
  - Any hash sent by the model is ignored.

---

## Checkpoints

| When | Everyone should see |
|---|---|
| End of tonight | Contracts C1–C6 written. Stub forecast JSON and simulator stub exist, so nobody is blocked. SAM skeleton deployed. Amplify URL live. Budget alarm on. Region confirmed for Claude + Guardrails. First `firm_mw` estimate done. |
| Day 1 noon | Simulator tests pass. Real forecasts in S3. `firm_mw` confirmed (no-action plan is RED around 6–7 p.m. at P90). Agent makes one real tool call. |
| Day 1 evening | **End-to-end happy path:** click "Make a plan" in the UI → attempts appear → final plan. Passcode + rate limits active. |
| Day 1 night | Eval run #1 done, failures logged. |
| Day 2 midday | **Code freeze.** Backup video recorded. Replay fallback tested. |

---

## Phase 0: Tonight

### P1 (ML)
- [ ] **P1-01** Request the EIA API key. Hand it to P3 for SSM. Never commit it.
- [ ] **P1-02** Publish a hand-made stub `forecasts/sept6_2022.json` matching C1, so P2/P3/P4 are unblocked.
  - Done when: file is in the repo / S3 and matches C1.
- [ ] **P1-03** `fetch_eia.py`: CAISO hourly D, DF, solar, wind for 2019–2022. Loop with `offset` (5,000 rows per call).
  - Done when: row count ≈ 4 years × 8,760 hours per series, gaps listed.
- [ ] **P1-04** `fetch_weather.py`: Open-Meteo hourly °F for LA, Riverside, Fresno, Sacramento, San Jose.
- [ ] **P1-05** Cleaning: UTC → `America/Los_Angeles`, handle DST, fill small gaps, drop bad rows.
  - Done when: one row per local hour, DST days checked by hand.
- [ ] **P1-06** `build_features.py`: per-city temps, avg/max, 24 h and 72 h heat build-up, hour/day-of-week/month/holiday, demand lag 48 h and 7 days (**no 24 h lag**).
- [ ] **P1-07** `train.py`: 3 XGBoost models, `reg:quantileerror`, alpha 0.1 / 0.5 / 0.9. **Train 2019–2021 only.**
  - Needs: P1-06
- [ ] **P1-08** `evaluate.py`: summer 2022 MAPE, Sept 6 peak error, P10–P90 coverage (~80%), vs CAISO DF and "same hour last week".
  - Done when: a results table P5 can paste into slides.
- [ ] **P1-09** `export_forecasts.py`: `sept6_2022`, `sept6_2022_plus5F`, `normal_summer_day`.
  - Needs: P1-08, P5-03 (normal day date)

### P2 (Agent)
- [ ] **P2-01** Turn on Claude model access in the hackathon Bedrock account. Record region + model ID in config.
- [ ] **P2-02** Test one Converse API call with a dummy tool from a laptop.
  - Done when: the model calls the tool and we read the result back.
- [ ] **P2-03** With P3 (see P3-16): confirm the Claude model, Bedrock Guardrails and the contextual grounding check are all available in our region. Check quotas.
- [ ] **P2-04** Draft system prompt v0 and JSON schemas for the 5 tools. Write C6.

### P3 (Simulator + backend)
- [ ] **P3-01** Create the repo with the folder layout from section 6. Open it in Kiro.
- [ ] **P3-02** Steering files: `product.md`, `tech.md`, `rules.md` (from sections 1–5 and 13).
- [ ] **P3-03** Kiro specs: create the 5 spec folders. Each owner runs the spec flow (requirements → design → tasks) for their area:
  - 01 data-forecast → P1 · 02 simulator → P3 · 03 agent → P2 · 04 api-backend → P3 · 05 dashboard → P4
- [ ] **P3-04** SAM skeleton: S3 bucket, `Runs` + `Decisions` tables, HTTP API, placeholder Lambdas. Deploy once.
  - Done when: a placeholder `GET /scenarios` returns 200.
- [ ] **P3-05** Store EIA key in SSM Parameter Store.
  - Needs: P1-01
- [ ] **P3-06** Write contracts C2–C5, including the C2/C3/C4 details above.
- [ ] **P3-07** Kiro hook: run pytest when backend files are saved.
- [ ] **P3-15** Simulator stub with the **final function signature**. It returns a fake C4 result (with `plan_hash` and `all_green`), so P2 can build tools before P3-08 is done.
  - Done when: P2 can import it and call it with a C3 plan.
- [ ] **P3-16** AWS Budgets alarm on the hackathon account (email the team). With P2: confirm Claude + Guardrails region availability (P2-03).
  - Done when: the alarm shows in Budgets, and the region is written in `tech.md`.

### P4 (Frontend)
- [ ] **P4-01** Vite + React + TS app, Recharts, routes for the 6 screens (empty pages).
- [ ] **P4-02** Amplify Hosting deploy from the repo. Send the URL to P3 for CORS.
- [ ] **P4-03** Mock API layer that reads stub JSON, so screens can be built before the backend is ready.
  - Needs: P1-02, C3–C5
- [ ] **P4-04** Status style guide: GREEN / YELLOW / RED with text labels, colorblind-safe colors.
- [ ] **P4-05** Kiro hook: lint frontend on save.

### P5 (Data, story, eval)
- [ ] **P5-01** Verify section 2 facts. Write each with its link in `docs/sources.md`.
  - Must resolve: the "2,100+ MW" alert claim (confirm or remove), 51,426 MW, EEA 3 timeline, 6.2% reserve.
- [ ] **P5-02** Fill values for all 3 scenario files. Every field gets a source or an `ASSUMPTION` label + reason.
  - Needs: C2, P5-11
- [ ] **P5-03** Pick the date for `normal_summer_day` (a 2022 summer day with no alert). Tell P1.
- [ ] **P5-04** Ask organizers for the exact wording of the hidden judging line.
- [ ] **P5-05** Confirm Kiro specs, steering and hooks work as described in section 13.
- [ ] **P5-11** Compute `firm_mw` (decision 5) and give it to P3. **Must finish before P5-06.**
  - At the Sept 6, 2022 peak hour: actual demand − solar − wind, plus a small cushion.
  - Write in `docs/sources.md`: the EIA numbers used, the cushion value and why it was chosen.
  - Tune the cushion so the no-action plan is RED around 6–7 p.m. at P90. Use the stub P90 tonight, then re-check on real P90.
  - Needs: P1-09 for the final check
  - Done when: P3 has the value, and the no-action plan is RED around 6–7 p.m. on the real P90.

---

## Phase 1: Day 1 morning

### P1
- [ ] **P1-10** Upload real forecasts to S3 (replacing the stub).
- [ ] **P1-11** `forecast_api` Lambda: `GET /forecast?scenario=`. Coordinate with P3 on the handler pattern.
- [ ] **P1-12** Peak hour + risk window logic (used by `get_forecast` and the UI banner).

### P2
- [ ] **P2-05** Implement tools: `get_forecast`, `get_resources`, `simulate`, `draft_alert`, `submit_plan`.
  - Needs: P3-15 (stub) to start, P3-08 (real simulator) to finish
- [ ] **P2-06** Server-side plan hash + `submit_plan` gate, as in C4.
  - Tests:
    - same plan with keys in a different order gives the same hash;
    - changing any value gives a different hash;
    - a plan never simulated in this run is refused;
    - a plan simulated in a different run is refused;
    - a non-green plan is refused unless the status is `CANNOT_MEET_TARGET`;
    - a hash sent by the model is ignored.
- [ ] **P2-07** System prompt v1.

### P3
- [ ] **P3-08** `simulator.py` + `models.py`: math and rules from section 8, the new pre-cool math from C3, allowed-hours checks, disruption score, clear violation messages.
  - Replaces the P3-15 stub with no change to the signature.
- [ ] **P3-09** pytest:
  - battery can't over-discharge (`power_mw` per hour, `energy_mwh × efficiency` total);
  - **pre-cool energy is conserved:** MW removed across `from_hours` = MW added across `to_hours` (not counting the rebound);
  - **rebound is applied:** `shift_mw × rebound_pct / 100` lands in the hour after the last `from_hours` hour;
  - rebound is ignored when the last `from_hours` hour is 23;
  - plans with hours outside `allowed_from_hours` / `allowed_to_hours` are rejected;
  - `shift_mw` > `max_shift_mw` is rejected;
  - DR hours and time limits; alert hours and season limit; status thresholds; disruption score.
  - Done when: all tests pass and P2 can import the simulator.

### P4
- [ ] **P4-06** Screen 1 (Forecast): scenario picker, P10–P90 band, P50 line, actual, CAISO forecast, available supply, risk window banner, source tooltips.

### P5
- [ ] **P5-06** `eval/agent_cases.yaml`: 10+ cases (scenarios × temperatures × margin targets), each with the expected result (all green / cannot meet target / alert expected or not).
  - Needs: P5-11 (`firm_mw` final)

---

## Phase 2: Day 1 afternoon

### P1
- [ ] **P1-13** Pair with P2 on forecast tools. Check the agent sees the right numbers.

### P2
- [ ] **P2-08** Agent loop in `agent_worker`: max 5 `simulate` calls, max 12 tool calls total, `CANNOT_MEET_TARGET` path, model ID from config.
- [ ] **P2-09** Save every tool call + result to the `Runs` trace as it happens (UI shows attempts live).

### P3
- [ ] **P3-10** APIs: `GET /scenarios`, `GET /resources`, `POST /simulate`.
- [ ] **P3-11** `POST /runs` (`agent_start`: save "running", invoke worker async) and `GET /runs/{run_id}`.
- [ ] **P3-12** Worker timeout 5 min, least-privilege IAM per Lambda, CORS limited to the Amplify domain (allow the `x-demo-passcode` header).
- [ ] **P3-17** Demo security (decision 7):
  - a Lambda authorizer on all routes that checks the `x-demo-passcode` header against SSM;
  - stage throttling, set very low on `POST /runs`;
  - a daily run cap of 100, counted in DynamoDB and checked in `agent_start`. Over the cap, return a clear error.
  - Done when: a missing or wrong passcode gets 401/403, run #101 of the day is refused, and throttling shows up under load.

### P4
- [ ] **P4-07** Screen 2 (Agent run): "Make a plan" button, poll every 2 s, attempts timeline, 24-hour strip per attempt with labels, loading and error states.
- [ ] **P4-12** Passcode entry: send `x-demo-passcode` on every request. Keep it in session memory only, never in the build or the repo. Show clear messages for a wrong passcode and for "daily limit reached".
  - Needs: P3-17

### P5
- [ ] **P5-07** Draft slides and the story. Practice the "CAISO already has tools" answer.

**Checkpoint: end-to-end happy path works from the UI.**

---

## Phase 3: Day 1 night

### P1
- [ ] **P1-14** Number guard **tests** (implementation moved to P2-13). Cover: exact matches, rounding, units (MW / %), hour formats ("6 p.m." = 18), a made-up number that must be flagged.

### P2
- [ ] **P2-10** Bedrock Guardrails: content filters, denied topic "controlling real grid equipment", alarmist word filter for alerts, contextual grounding check on the briefing.
- [ ] **P2-11** Enforce alert text ≤ 160 characters. Run the number guard on every briefing.
  - Needs: P2-13
- [ ] **P2-13** `number_guard.py` (moved from P1): pull every number out of the briefing, match it against tool outputs, flag mismatches, fall back to tool numbers only. Agree tolerance rules with P1.
  - Done when: P1-14 tests pass.

### P3
- [ ] **P3-13** `POST /runs/{run_id}/decision` (Approve / Edit / Reject + operator name + note) and `GET /audit`. Validate input (operator name required, length-limited).

### P4
- [ ] **P4-08** Screen 3 (Briefing + approval): status, action table (from simulator), before/after margin chart, "Why this plan", "What could go wrong", editable draft alert with character count and **"Draft only. Not sent."**, Approve / Edit / Reject + note.
- [ ] **P4-13** Operator name field on the approval screen. It's required and saved with each decision for the audit log.
  - Needs: P3-13

### P5
- [ ] **P5-08** Run eval #1 with `eval/run_agent_eval.py`. Log the score and each failure's cause. Hand failures to P2.

---

## Phase 4: Day 2 morning

### P1
- [ ] **P1-15** What-if scenarios: pre-compute forecasts for +0 to +8 °F and store in S3 (no live model calls).

### P2
- [ ] **P2-12** Fix eval failures (prompt + tool tweaks). Re-run the eval with P5.
- [ ] **P2-14** "Replay last good run" fallback, backend side: mark a saved run as the demo replay, and serve its full trace (attempts, final plan, briefing).

### P3
- [ ] **P3-14** Hardening: Bedrock slow/down → clear error + "try again", input validation everywhere, CloudWatch logs readable.

### P4
- [ ] **P4-09** Screen 4 (What-if): +°F slider, margin target slider, "Re-plan".
- [ ] **P4-10** Screen 5 (Audit log) and Screen 6 (About and limits). About must include:
  - "ASSUMPTION: batteries assumed charged from midday solar."
  - "`firm_mw` estimated from EIA actuals."
  - "Demo security: passcode + rate limits. Production would use Cognito."
  - the simulated-only statement, forecast error and eval score.
- [ ] **P4-11** Polish checklist: loading states, errors, laptop + phone, large numbers, consistent colors, every number has a source tooltip, every assumption labelled.
- [ ] **P4-14** "Replay last good run" switch, frontend side: plays the saved run through the same attempt timeline. While it's on, the screen shows a visible "Replay" label.
  - Needs: P2-14
  - Done when: the full demo flow works with Bedrock turned off.

### P5
- [ ] **P5-09** Final eval run. Put the score and the forecast error into the slides and the About page.
- [ ] **P5-10** Record the backup demo video.
- [ ] **P5-12** README + a simple architecture diagram (based on the section 4 flow) for the judges and the submission page.

---

## Phase 5: Day 2 midday

- [ ] **ALL-01** Code freeze.
- [ ] **ALL-02** Rehearse the 3-minute demo at least 3 times. P5 leads. Practice switching to replay once.
- [ ] **ALL-03** Run through the section 17 checklist together.

---

## Open questions

### Resolved

1. ~~**Pre-cool math**~~ **Resolved:** pre-cooling moves energy instead of removing it. `from_hours` lose `shift_mw` each, `to_hours` share the shifted energy equally, and the rebound is added after. Written in C3 and tested in P3-09.
2. ~~**Pre-cool field names**~~ **Resolved:** the scenario has `allowed_from_hours`, `allowed_to_hours`, `max_shift_mw`, `rebound_pct`. The plan has `shift_mw`, `from_hours`, `to_hours`. Hours outside the allowed lists are rejected. Written in C2 and C3.
3. ~~**Rebound timing**~~ **Resolved:** the rebound goes in the hour right after the last `from_hours` hour. Past hour 23, it's ignored. Written in C3 and tested in P3-09.
4. ~~**Battery charging**~~ **Resolved:** not modeled. Batteries start at `start_soc_pct` with no recharge. Labelled on the About page as an ASSUMPTION: "batteries assumed charged from midday solar" (P4-10).
5. ~~**`firm_mw`**~~ **Resolved:** estimated from EIA actuals at the Sept 6, 2022 peak (demand − solar − wind + a small cushion), tuned so the no-action plan is RED around 6–7 p.m. at P90. Labelled "estimated from EIA actuals". P5-11 must finish before eval expectations (P5-06).
6. ~~**Exact-plan check**~~ **Resolved:** the server hashes the plan (SHA-256 of canonical JSON) in `simulate` and again in `submit_plan`. The hash must match a plan simulated in this run, with an all-GREEN result or `CANNOT_MEET_TARGET`. The model's hash is never trusted. Written in C4 and tested in P2-06.
7. ~~**API security**~~ **Resolved:** Lambda authorizer with an `x-demo-passcode` header checked against SSM, stage throttling (very low on `POST /runs`), daily cap of 100 runs in DynamoDB, and an AWS Budgets alarm. No Cognito. The About page says "Demo security: passcode + rate limits. Production would use Cognito." (P3-16, P3-17, P4-10, P4-12)

### New (small, settle Day 1 morning)

8. **Battery double counting in `firm_mw` (P5).** Peak-hour demand − solar − wind also includes whatever CAISO batteries were discharging at that hour. If we then add our battery resource on top, that battery output is counted twice. If EIA or CAISO has battery output for that hour, subtract it from `firm_mw`. If not, note it as a limitation.
9. **Hash normalization (P2 + P3).** Sorted keys don't make `500` and `500.0` hash the same, or `[17,18,19]` and `[19,18,17]`. Proposal: hash the plan after it's parsed into the `models.py` plan object (numbers normalized, hour lists sorted), not the raw model output.
10. **Overlapping hours (P3).** Should a plan be rejected if `from_hours` and `to_hours` overlap, or if the rebound hour falls inside `to_hours`? Proposal: reject overlaps. The allowed lists in C2 don't overlap anyway.
