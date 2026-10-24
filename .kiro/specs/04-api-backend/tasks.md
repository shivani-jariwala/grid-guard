# Implementation plan: API backend

- [x] 1. HTTP helpers in `backend/shared/api.py`
  - `ok`, `error`, `ApiError`, `json_body`, `query_param`, `valid_id`, `api_handler` decorator
  - Unit tests for base64 bodies, non-object bodies, bad IDs, and unexpected exceptions becoming `500 INTERNAL_ERROR`
  - _Requirements: 9.1, 9.2, 9.3, 9.4_

- [x] 2. Data store in `backend/shared/data.py`
  - `LocalStore` (repo `scenarios/` + `forecasts/`) and `S3Store`, picked by `FORECAST_BUCKET`
  - `NotFound` for missing objects; add `SCENARIO_PREFIX` to config
  - Unit tests for `LocalStore`
  - _Requirements: 11.1, 11.2_

- [x] 3. `GET /scenarios` in `scenarios_api`
  - Lists every scenario, sorted; `DATA_ERROR` on a bad file
  - Handler tests
  - _Requirements: 1.1, 1.2_

- [x] 4. `GET /resources` in `resources_api`
  - Returns the stored C2 scenario after checking it parses
  - Handler tests for 200 / 400 / 404 / 500
  - _Requirements: 3.1, 3.2, 9.2_

- [x] 5. `POST /simulate` in `simulate_api`
  - Validates the body, loads the scenario + forecast, calls `simulate_dict`
  - Invalid plans return 200 with `valid: false`
  - Handler tests, including a bad `margin_target_pct` and net demand ≤ 0 (`DATA_ERROR`)
  - _Requirements: 4.1, 4.2, 4.3, 4.4_

- [ ] 6. `GET /forecast` in `forecast_api` (P1, same pattern as task 4)
  - _Requirements: 2.1, 2.2, 2.3_

- [ ] 7. SAM template + first deploy
  - Layer for `backend/shared`, one function per route, `Runs` / `Decisions` / `Counters` tables, S3 bucket, HTTP API with CORS + throttling
  - Upload `scenarios/*.json` and `forecasts/*.json` to the bucket
  - Smoke test the deployed `GET /scenarios` and `POST /simulate`
  - _Requirements: 8.1, 10.1, 10.2_

- [ ] 8. `POST /runs` and `GET /runs/{run_id}`
  - Atomic daily counter in `Counters`, save the run, async invoke of `agent_worker`, failure path
  - Handler tests with fake DynamoDB / Lambda clients
  - _Requirements: 5.1, 5.2, 5.3, 6.1, 6.2, 8.2_

- [ ] 9. `POST /runs/{run_id}/decision` and `GET /audit`
  - Validation, `409 RUN_NOT_DONE`, newest-first audit with a limit
  - Handler tests
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

- [ ] 10. Hardening pass
  - Check every route against the error table in the design, plus log hygiene (no secrets, no full bodies)
  - _Requirements: 9.1, 9.3, 9.4_
