# GridGuard: Team Tasks

Planning only. No code yet. Each task has an ID, an owner, what it needs first, and a "done when" check.

- `[ ]` = to do, `[x]` = done
- **Needs:** tasks that must finish first
- **Done when:** how we know it's finished
- Task IDs are stable. New tasks get the next free number, so some blocks list IDs out of order. Removed IDs are listed under "Removed tasks" and never reused.

---

## Team and ownership

| Person | Role | Owns |
|---|---|---|
| **P1** | ML | `ml/`, forecasts in S3, `forecast_api`, number guard tests |
| **P2** | Agent | Bedrock access, tools, plan hashing, system prompt, `agent_worker` loop, Guardrails, `number_guard.py`, Bedrock trace panel |
| **P3** | Simulator + backend | Kiro setup, SAM template, `simulator.py`, APIs, DynamoDB, IAM, throttling + daily run cap, budget alarm |
| **P4** | Frontend | `frontend/`, Amplify Hosting, 3 screens (Forecast, Agent run, Briefing + approval) |
| **P5** | Data, story, eval, video | Fact checks, `scenarios/*.json` values, `firm_mw` estimate, `eval/`, architecture diagram, README, **video (lead)** |

---

## Submission and judging

- **Deliverable:** a 5-minute video, due **Saturday 10:30 AM**. Judging is by video only. There's no live demo and judges don't use the app.
- **The video must show:** (1) the architecture, (2) the app plus the persona and need, (3) Amazon Bedrock working inside the app.
- **Gate (pass/fail):** Bedrock must do real work, or we don't place.
- **Build time:** about 18 hours. Code freeze Saturday 7:30 AM.

| # | Criterion | Points | How we score it |
|---|---|---|---|
| Gate | AWS AI service | pass/fail | Bedrock is the core. Show the Bedrock trace panel (P2-15) in the video. |
| 1 | Persona + need | 20 | The "so what": rolling blackouts cut power to homes and businesses in a heat wave. Use Sept 6, 2022. |
| 2 | Working app end-to-end | 25 | One take: forecast → plan → fix → approve. |
| 3 | Bedrock doing real work | 20 | Show the agent's attempts: RED → revised → GREEN. |
| 4 | Understand what you built | 15 | 1 minute on the architecture diagram, plus how the simulator and number guard work. |
| 5 | Polish | 10 | 3 clean screens beat 6 rough ones. |
| 6 | Above and beyond | 10 | Real data, comparison with CAISO's forecast, eval score, hash-checked plans. |

### Scope cuts

- Screens go from 6 to 3: (1) Forecast, (2) Agent run, (3) Briefing + approval with a small audit log panel. The What-if, Audit and About screens are gone. About and limits content goes into the video and the README.
- The what-if slider becomes a scenario switch: `sept6_2022` ↔ `sept6_2022_plus5F`. `normal_summer_day` only if time allows.
- Eval: 10 cases.
- No "replay last good run". We can re-record the video.
- Security is cost protection only: throttling, daily run cap, Budgets alarm. No passcode, no Cognito.

### Video outline (5:00)

| Time | Section | What's on screen |
|---|---|---|
| 0:00–0:40 | Persona + need | Dana, Sept 6, 2022, one step from rolling blackouts |
| 0:40–1:40 | Architecture | Walk the diagram: ML forecast → Bedrock agent with tools → simulator → number guard → Dana's approval. Point out which parts are AI and which are plain code. |
| 1:40–3:40 | App + Bedrock in use | Forecast chart · "Make a plan": attempt 1 RED → agent revises → GREEN · Bedrock trace panel · briefing with the draft alert labelled "Not sent" · Dana approves |
| 3:40–4:20 | Fail on purpose | +5°F: the agent returns `CANNOT_MEET_TARGET` with a recommendation |
| 4:20–5:00 | Proof and limits | Forecast error vs CAISO · eval score · assumptions (`firm_mw`, no battery recharge, simulated only) · how we built it with Kiro specs and hooks |

---

## Shared contracts (agree on these in Block 1)

Everyone builds against these, so they must be written down before real work starts. Put them in `docs/contracts.md`. Changes after Block 1 need a heads-up in the team chat.

| ID | Contract | Owner | Used by |
|---|---|---|---|
| C1 | Forecast JSON (`hour, p10, p50, p90, actual, caiso_forecast, temp_avg`) | P1 | P2, P3, P4 |
| C2 | Scenario JSON (schema by P3, values by P5). Details below. | P3 + P5 | P2, P4 |
| C3 | Plan JSON + pre-cool math. Details below. | P3 | P2, P4 |
| C4 | Simulator result JSON + plan hash. Details below. | P3 (+ P2 for hashing) | P2, P4 |
| C5 | Run record + API responses (`/runs`, `/runs/{id}`, `/audit`) | P3 | P4 |
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

## Timeline and checkpoints

The blocks are sized for about 18 build hours ending at the 7:30 AM freeze. If a block finishes early, start the next one.

| Block | Build hours | Checkpoint at the end |
|---|---|---|
| 1. Setup and unblock | ≈ h0–3 | **Stub data and contracts done:** C1–C6 written, stub forecast + simulator stub exist, SAM skeleton deployed, Amplify URL live, Budgets alarm on, Claude + Guardrails confirmed in `us-east-1`, first `firm_mw` estimate. |
| 2. Core pieces | ≈ h3–7 | **Simulator tests passing.** Real forecasts in S3. `firm_mw` confirmed (no-action plan RED around 6–7 p.m. at P90). Agent makes one real tool call. |
| 3. Connect it | ≈ h7–11 | **End-to-end happy path in the UI:** "Make a plan" → attempts + Bedrock trace appear → final plan. Throttling + run cap active. |
| 4. Safety, approval, eval | ≈ h11–14 | **Eval #1** done, failures logged. Approval + audit panel working. |
| 5. Fix, polish, docs | ≈ h14–18 | **Code freeze, Saturday 7:30 AM.** README, diagram and video script ready. |
| 6. Video | Sat 7:30–10:00 AM | Record + edit 7:30–9:30. **Upload by 10:00 AM** (30-minute buffer before 10:30). |

---

## Block 1: Setup and unblock (≈ h0–3)

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
  - Done when: a results table P5 can paste into the README and the video.
- [ ] **P1-09** `export_forecasts.py`: `sept6_2022` and `sept6_2022_plus5F`. Add `normal_summer_day` only if time allows.
  - Needs: P1-08 (and P5-03 if we do the normal day)

### P2 (Agent)
- [ ] **P2-01** Turn on Claude model access in the hackathon Bedrock account. Record region + model ID in config.
- [ ] **P2-02** Test one Converse API call with a dummy tool from a laptop.
  - Done when: the model calls the tool and we read the result back.
- [ ] **P2-03** With P3 (see P3-16): confirm the Claude model, Bedrock Guardrails and the contextual grounding check are all available in our region. Check quotas.
  - The event account only allows `us-east-1` (Workshop Studio, Team 31), so that's the region.
- [ ] **P2-04** Draft system prompt v0 and JSON schemas for the 5 tools. Write C6.

### P3 (Simulator + backend)
- [x] **P3-01** Create the repo structure (folder layout from section 6), open it in Kiro, and push it to GitHub.
  - Done: [shivani-jariwala/grid-guard](https://github.com/shivani-jariwala/grid-guard), commit `737b876` on `main`.
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
- [ ] **P4-01** Vite + React + TS app, Recharts, routes for the 3 screens (empty pages): Forecast, Agent run, Briefing + approval.
- [ ] **P4-02** Amplify Hosting deploy from the repo. Send the URL to P3 for CORS.
- [ ] **P4-03** Mock API layer that reads stub JSON, so screens can be built before the backend is ready.
  - Needs: P1-02, C3–C5
- [ ] **P4-04** Status style guide: GREEN / YELLOW / RED with text labels, colorblind-safe colors.
- [ ] **P4-05** Kiro hook: lint frontend on save.

### P5 (Data, story, eval)
- [ ] **P5-01** Verify section 2 facts. Write each with its link in `docs/sources.md`.
  - Must resolve: the "2,100+ MW" alert claim (confirm or remove), 51,426 MW, EEA 3 timeline, 6.2% reserve.
- [ ] **P5-02** Fill values for the scenario files (`sept6_2022`, `sept6_2022_plus5F`). Every field gets a source or an `ASSUMPTION` label + reason.
  - Needs: C2, P5-11
- [ ] **P5-03** *Only if time allows:* pick the date for `normal_summer_day` (a 2022 summer day with no alert). Tell P1.
- [x] **P5-04** Ask organizers for the exact wording of the hidden judging line.
  - Done: "You understand what you built" (15 pts). Covered by the architecture section of the video (P5-13, P5-14).
- [ ] **P5-05** Confirm Kiro specs, steering and hooks work as described in section 13.
- [ ] **P5-11** Compute `firm_mw` (decision 5) and give it to P3. **Must finish before P5-06.**
  - At the Sept 6, 2022 peak hour: actual demand − solar − wind, plus a small cushion.
  - Write in `docs/sources.md`: the EIA numbers used, the cushion value and why it was chosen.
  - Tune the cushion so the no-action plan is RED around 6–7 p.m. at P90. Use the stub P90 in Block 1, then re-check on real P90.
  - Needs: P1-09 for the final check
  - Done when: P3 has the value, and the no-action plan is RED around 6–7 p.m. on the real P90.

---

## Block 2: Core pieces (≈ h3–7)

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
- [ ] **P4-06** Screen 1 (Forecast): scenario switch (`sept6_2022` ↔ `sept6_2022_plus5F`), P10–P90 band, P50 line, actual, CAISO forecast, available supply, risk window banner, source tooltips.

### P5
- [ ] **P5-06** `eval/agent_cases.yaml`: 10 cases (the 2 scenarios × a few margin targets), each with the expected result (all green / cannot meet target / alert expected or not).
  - Needs: P5-11 (`firm_mw` final)

---

## Block 3: Connect it end to end (≈ h7–11)

### P1
- [ ] **P1-13** Pair with P2 on forecast tools. Check the agent sees the right numbers.

### P2
- [ ] **P2-08** Agent loop in `agent_worker`: max 5 `simulate` calls, max 12 tool calls total, `CANNOT_MEET_TARGET` path, model ID from config.
- [ ] **P2-09** Save every tool call + result to the `Runs` trace as it happens (UI shows attempts live).
- [ ] **P2-15** Bedrock trace panel on the Agent run screen: each tool call in order (tool name, short input, short result), so the video clearly shows Bedrock doing the work. Build it with P4 inside Screen 2.
  - Needs: P2-09, P4-07
  - Done when: a full run shows every `get_forecast` / `get_resources` / `simulate` / `draft_alert` / `submit_plan` call, readable on a screen recording.

### P3
- [ ] **P3-10** APIs: `GET /scenarios`, `GET /resources`, `POST /simulate`.
- [ ] **P3-11** `POST /runs` (`agent_start`: save "running", invoke worker async) and `GET /runs/{run_id}`.
- [ ] **P3-12** Worker timeout 5 min, least-privilege IAM per Lambda, CORS limited to the Amplify domain.
- [ ] **P3-17** Cost protection (decision 7):
  - HTTP API stage throttling, set very low on `POST /runs`;
  - a daily run cap of 100, counted in DynamoDB and checked in `agent_start`. Over the cap, return a clear error.
  - Done when: run #101 of the day is refused with a clear message, and throttling shows up under load.

### P4
- [ ] **P4-07** Screen 2 (Agent run): "Make a plan" button, poll every 2 s, attempts timeline, 24-hour strip per attempt with labels, room for the Bedrock trace panel (P2-15), loading and error states (including "daily limit reached").

### P5
- [ ] **P5-07** Write the story for the persona + need section of the video (0:00–0:40). Make the "so what" clear: rolling blackouts cut power to homes and businesses in a heat wave. Use the "under heavy time pressure" framing, not "CAISO has no tools".
- [ ] **P5-13** Clean architecture diagram for the video and README. It should show ML forecast → Bedrock agent with tools → simulator → number guard → Dana's approval → audit log, plus the AWS services. Mark which parts are AI and which are plain code.
  - Done when: the team can explain every box in 1 minute (rubric #4).

**Checkpoint: end-to-end happy path works from the UI.**

---

## Block 4: Safety, approval, eval (≈ h11–14)

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
- [ ] **P4-08** Screen 3 (Briefing + approval): status, action table (from simulator), before/after margin chart, "Why this plan", "What could go wrong", editable draft alert with character count and **"Draft only. Not sent."**, Approve / Edit / Reject + note, and a **small audit log panel** (who, when, decision, note) from `GET /audit`.
- [ ] **P4-13** Operator name field on the approval screen. It's required and saved with each decision for the audit log.
  - Needs: P3-13

### P5
- [ ] **P5-08** Run eval #1 with `eval/run_agent_eval.py`. Log the score and each failure's cause. Hand failures to P2.

---

## Block 5: Fix, polish, docs (≈ h14–18, until the 7:30 AM freeze)

### P2
- [ ] **P2-12** Fix eval failures (prompt + tool tweaks). Re-run the eval with P5.

### P3
- [ ] **P3-14** Hardening: Bedrock slow/down → clear error + "try again", input validation everywhere, CloudWatch logs readable.

### P4
- [ ] **P4-09** "Fail on purpose" flow: switching to `sept6_2022_plus5F` and clicking "Make a plan" shows the `CANNOT_MEET_TARGET` result and the agent's recommendation clearly on Screens 2 and 3.
- [ ] **P4-11** Polish checklist: loading states, errors, large readable numbers, consistent colors, every number has a source tooltip, assumptions labelled next to the numbers they affect. Check that it looks clean at the screen-recording resolution (1080p).

### P5
- [ ] **P5-09** Final eval run. Put the score and the forecast error into the README and the video.
- [ ] **P5-12** README:
  - what it does, and the persona and need;
  - the architecture diagram (P5-13);
  - data sources;
  - assumptions: `firm_mw` estimated from EIA actuals; batteries assumed charged from midday solar, no recharge modeled; simulated only;
  - limits, the forecast error and the eval score;
  - security: "Cost protection only (throttling, daily run cap, Budgets alarm). Production would add Cognito login.";
  - how to run it.
- [ ] **P5-14** Video script and storyboard following the video outline above (5:00 max). With all: each owner writes 2–3 lines on their part.
  - Needs: P5-07, P5-13
  - Done when: every section has a script, the screens to record and a time budget.

---

## Block 6: Freeze and video (Saturday 7:30–10:00 AM)

- [ ] **ALL-02** Dry run just before the freeze: click through the full video flow once (forecast → plan → fix → approve → +5°F). Fix anything that breaks.
- [ ] **ALL-01** Code freeze, **Saturday 7:30 AM**.
- [ ] **P5-15** (lead, with all) Record the screen and the voice-over, about 7:30–8:45 AM. Re-record any section that goes wrong.
- [ ] **P5-16** (lead) Edit the video by 9:30 AM, then upload. **Uploaded by 10:00 AM** (30-minute buffer before 10:30).
- [ ] **P5-17** Add the team contact info required by the organizers to the submission.
- [ ] **ALL-03** Final check before upload:
  - the video shows the architecture, the app plus persona and need, and Bedrock working in the app;
  - it's 5:00 or shorter;
  - the facts are verified (P5-01).

---

## Removed tasks

These IDs are retired and won't be reused.

- **P1-15** What-if forecasts for +0 to +8 °F. The slider became the `sept6_2022_plus5F` switch, which P1-09 covers.
- **P2-14**, **P4-14** "Replay last good run". There's no live demo, so we re-record instead.
- **P4-10** Separate Audit log and About screens. The audit log is now a panel on Screen 3 (P4-08). About and limits moved to the README (P5-12) and the video.
- **P4-12** Passcode entry. There's no passcode anymore.
- **P5-10** Backup demo video. The video is now the main deliverable (P5-14 to P5-16).

---

## Open questions

### Resolved

1. ~~**Pre-cool math**~~ **Resolved:** pre-cooling moves energy instead of removing it. `from_hours` lose `shift_mw` each, `to_hours` share the shifted energy equally, and the rebound is added after. Written in C3 and tested in P3-09.
2. ~~**Pre-cool field names**~~ **Resolved:** the scenario has `allowed_from_hours`, `allowed_to_hours`, `max_shift_mw`, `rebound_pct`. The plan has `shift_mw`, `from_hours`, `to_hours`. Hours outside the allowed lists are rejected. Written in C2 and C3.
3. ~~**Rebound timing**~~ **Resolved:** the rebound goes in the hour right after the last `from_hours` hour. Past hour 23, it's ignored. Written in C3 and tested in P3-09.
4. ~~**Battery charging**~~ **Resolved:** not modeled. Batteries start at `start_soc_pct` with no recharge. Labelled as an ASSUMPTION in the README and the video: "batteries assumed charged from midday solar" (P5-12, P5-14).
5. ~~**`firm_mw`**~~ **Resolved:** estimated from EIA actuals at the Sept 6, 2022 peak (demand − solar − wind + a small cushion), tuned so the no-action plan is RED around 6–7 p.m. at P90. Labelled "estimated from EIA actuals". P5-11 must finish before eval expectations (P5-06).
6. ~~**Exact-plan check**~~ **Resolved:** the server hashes the plan (SHA-256 of canonical JSON) in `simulate` and again in `submit_plan`. The hash must match a plan simulated in this run, with an all-GREEN result or `CANNOT_MEET_TARGET`. The model's hash is never trusted. Written in C4 and tested in P2-06.
7. ~~**API security**~~ **Resolved (updated):** judges only watch the video, so this is cost protection only: HTTP API stage throttling (very low on `POST /runs`), a daily cap of 100 runs in DynamoDB, and an AWS Budgets alarm. No passcode, no Cognito. The README says "production would add Cognito login." (P3-16, P3-17, P5-12)

### New (small, settle in Block 2)

8. **Battery double counting in `firm_mw` (P5).** Peak-hour demand − solar − wind also includes whatever CAISO batteries were discharging at that hour. If we then add our battery resource on top, that battery output is counted twice. If EIA or CAISO has battery output for that hour, subtract it from `firm_mw`. If not, note it as a limitation.
9. **Hash normalization (P2 + P3).** Sorted keys don't make `500` and `500.0` hash the same, or `[17,18,19]` and `[19,18,17]`. Proposal: hash the plan after it's parsed into the `models.py` plan object (numbers normalized, hour lists sorted), not the raw model output.
10. **Overlapping hours (P3).** Should a plan be rejected if `from_hours` and `to_hours` overlap, or if the rebound hour falls inside `to_hours`? Proposal: reject overlaps. The allowed lists in C2 don't overlap anyway.
