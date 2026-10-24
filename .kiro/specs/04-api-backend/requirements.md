# Requirements: API backend

## Introduction

The API backend is the HTTP layer between the dashboard (P4), the Bedrock agent (P2) and our data. It serves scenarios and forecasts, runs the simulator for what-if checks, starts agent runs and reports their progress, and records the operator's decisions. It runs on API Gateway (HTTP API) + AWS Lambda (Python 3.12), with data in S3 and DynamoDB.

Data shapes and endpoints are defined in the shared contracts:

#[[file:docs/contracts.md]]

Out of scope: user login (judges only watch a video; production would add Cognito), the agent loop itself (spec 03-agent), and the forecast model (spec 01-data-forecast).

## Requirements

### Requirement 1: List scenarios

**User story:** As the dashboard, I want a list of available scenarios, so that Dana can switch between the Sept 6, 2022 day and the +5°F day.

#### Acceptance criteria

1. WHEN a client calls `GET /scenarios` THEN the system SHALL return `200` with `{"scenarios": [{id, name, date, margin_target_pct}]}` for every stored scenario, sorted by `id`.
2. IF a stored scenario file doesn't match contract C2 THEN the system SHALL return `500 DATA_ERROR` and log which file failed, without exposing file paths to the client.

### Requirement 2: Get a forecast

**User story:** As the dashboard, I want the hourly forecast for a scenario, so that the forecast chart can show P10–P90, P50, actual and CAISO's forecast.

#### Acceptance criteria

1. WHEN a client calls `GET /forecast?scenario={id}` THEN the system SHALL return `200` with the C1 forecast named by the scenario's `forecast_id`.
2. IF the `scenario` parameter is missing or not a valid ID THEN the system SHALL return `400 BAD_REQUEST`.
3. IF the scenario or its forecast doesn't exist THEN the system SHALL return `404 SCENARIO_NOT_FOUND` or `404 FORECAST_NOT_FOUND`.

### Requirement 3: Get scenario resources

**User story:** As the dashboard and the agent, I want the full scenario (supply, battery, pre-cooling, programs, alert limits and their sources), so that numbers can be shown with source tooltips.

#### Acceptance criteria

1. WHEN a client calls `GET /resources?scenario={id}` THEN the system SHALL return `200` with the scenario exactly as stored (C2), including `sources`.
2. IF the scenario is missing, invalid or not found THEN the system SHALL respond as in Requirement 2.2 and 2.3.

### Requirement 4: What-if simulation

**User story:** As Dana, I want to test a plan against a scenario, so that I can see each hour's margin and status before approving anything.

#### Acceptance criteria

1. WHEN a client calls `POST /simulate` with `{scenario_id, plan, margin_target_pct?}` THEN the system SHALL run the simulator and return `200` with the C4 result.
2. WHEN the plan breaks a rule or has a bad shape THEN the system SHALL still return `200`, with `valid: false` and every violation listed.
3. IF the body isn't a JSON object, `scenario_id` is missing or invalid, `plan` is missing, or `margin_target_pct` isn't a number from 0 to 100 THEN the system SHALL return `400 BAD_REQUEST` with a message naming the field.
4. The simulator SHALL NOT call any AI service. Results come from plain code only.

### Requirement 5: Start an agent run

**User story:** As Dana, I want to press "Make a plan" and get a run ID right away, so that the UI can show the agent's attempts while it works.

#### Acceptance criteria

1. WHEN a client calls `POST /runs` with `{scenario_id, margin_target_pct?}` THEN the system SHALL save a `Runs` item with `status: running`, start the agent worker asynchronously, and return `202 {run_id, status}` within 3 seconds.
2. IF 100 runs have already started today (Pacific time) THEN the system SHALL return `429 DAILY_LIMIT_REACHED` and SHALL NOT start the worker.
3. IF the worker can't be started THEN the system SHALL mark the run `failed` with an error code and return `500`.

### Requirement 6: Check a run

**User story:** As the dashboard, I want to poll a run every 2 seconds, so that the attempts timeline and Bedrock trace update live.

#### Acceptance criteria

1. WHEN a client calls `GET /runs/{run_id}` THEN the system SHALL return `200` with the full `Runs` item (C5).
2. IF the run doesn't exist THEN the system SHALL return `404 RUN_NOT_FOUND`.

### Requirement 7: Record decisions and show the audit log

**User story:** As Dana, I want to approve, edit or reject a plan with my name and a note, so that there is a record of who decided what, and when.

#### Acceptance criteria

1. WHEN a client calls `POST /runs/{run_id}/decision` with `{operator_name, decision, note?, edits?}` THEN the system SHALL save a `Decisions` item with a server timestamp and the run's final `plan_hash`, and return `201`.
2. IF `operator_name` is empty or over 60 characters, `decision` isn't `APPROVE`/`EDIT`/`REJECT`, `note` is over 500 characters, or `edits.alert_text` is over 160 characters THEN the system SHALL return `400 BAD_REQUEST`.
3. IF the run isn't `done` THEN the system SHALL return `409 RUN_NOT_DONE`.
4. WHEN a client calls `GET /audit?limit=N` THEN the system SHALL return up to N decisions (default 50, max 200), newest first.
5. The system SHALL NOT store personal data other than the operator name.

### Requirement 8: Cost protection

**User story:** As the team, I want Bedrock spending limited, so that a bug or a stranger with the URL can't run up costs on the event account.

#### Acceptance criteria

1. The HTTP API SHALL throttle all routes, with a much lower limit on `POST /runs`.
2. The system SHALL enforce the daily run cap from Requirement 5.2 with an atomic DynamoDB counter.

### Requirement 9: Errors, validation and logging

**User story:** As a developer, I want every failure to return a clear, consistent error, so that the UI can show useful messages and we can debug from CloudWatch.

#### Acceptance criteria

1. The system SHALL return every error as `{"error": {"code", "message"}}` with a matching HTTP status.
2. The system SHALL accept only IDs matching `^[a-z0-9_]{1,64}$` for scenarios and a UUID for runs, before touching storage.
3. IF an unexpected exception occurs THEN the system SHALL return `500 INTERNAL_ERROR` with a generic message and log the details with the request ID.
4. The system SHALL NOT log secrets or full request bodies.

### Requirement 10: Access and permissions

**User story:** As the team, I want each Lambda to have only the permissions it needs, so that a bug in one function can't touch unrelated data.

#### Acceptance criteria

1. Each Lambda SHALL have an IAM role limited to the tables, bucket prefixes and actions it uses.
2. The API SHALL allow CORS only from the Amplify domain (and `http://localhost:5173` for local frontend development).

### Requirement 11: Local development

**User story:** As a backend developer, I want to run and test handlers on my laptop without AWS, so that I can work fast and the pytest hook stays useful.

#### Acceptance criteria

1. WHEN `FORECAST_BUCKET` is empty THEN the system SHALL read scenarios from `scenarios/` and forecasts from `forecasts/` in the repo instead of S3.
2. Every handler SHALL be testable with pytest using fake events, with no network calls.
