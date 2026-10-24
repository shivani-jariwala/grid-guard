# GridGuard

GridGuard helps a grid operator get through a California heat wave. An XGBoost model forecasts tomorrow's hourly demand (P10 / P50 / P90). An Amazon Bedrock agent drafts a plan (batteries, pre-cooling, demand response, and a public alert only as a last resort), tests it in a plain-code simulator, and revises it until every hour is safe. A human operator approves the plan.

**Simulated only.** GridGuard never controls real equipment and never sends alerts. Alerts are drafts.

Built for the SJSU AI Hackathon (Oct 2026, AWS track) with Amazon Bedrock and Kiro.

## Where to start

- **[`tasks.md`](tasks.md)**: who owns what, task IDs, dependencies, and the shared contracts (C1–C6). Find your tasks there first.
- **`.kiro/steering/`**: project context and rules. Kiro loads these into every session.
- **`.kiro/specs/`**: one spec per area. Each owner runs Kiro's spec flow for their own area (P3-03).

## Repo layout

```
.kiro/
  steering/        product.md, tech.md, rules.md
  specs/           01-data-forecast … 05-dashboard (one per owner)
  hooks/           pytest on backend save, lint on frontend save
ml/                data fetch, features, training, evaluation, forecast export   (P1)
scenarios/         scenario JSON files with sourced values                       (P5)
backend/
  template.yaml    AWS SAM template                                             (P3)
  shared/          simulator.py, models.py, number_guard.py                      (P3, P2)
  functions/       one folder per Lambda                                         (P1, P2, P3)
frontend/          React + Vite + TypeScript dashboard                           (P4)
eval/              agent eval cases + runner                                     (P5)
tests/             pytest: simulator, number guard, API
docs/              contracts, sources, limits, demo script
```

## Prerequisites

- Python 3.12 (Lambda runtime and local dev)
- Node.js LTS + npm (frontend)
- AWS CLI v2 and AWS SAM CLI (backend deploy)
- Access to the hackathon AWS account, with Bedrock model access enabled (P2-01)

## Backend setup and tests

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements-dev.txt
cp .env.example .env         # then fill in your values (.env is git-ignored)
python -m pytest -q          # simulator, model and config tests
```

- **Settings:** `backend/shared/config.py` reads every setting from environment variables. It loads `.env` automatically when you run locally. In AWS, the SAM template sets the same variables.
- **Credentials:** AWS keys never go in `.env`. Use the `~/.aws` profile (`AWS_PROFILE=gridguard`). The EIA key lives in SSM Parameter Store.

The Kiro hook in `.kiro/hooks/` reruns these tests whenever you save a `backend/` or `tests/` Python file.

## Deploy the backend (AWS SAM)

```bash
cd backend
sam build && sam deploy      # stack "gridguard" in us-east-1, uses the gridguard AWS profile
```

Settings live in `backend/samconfig.toml`. The API URL and bucket name are printed as stack outputs.

Useful switches (add to `sam deploy`):
- `--parameter-overrides AgentWorker=bedrock`: use the real Bedrock agent (`functions/agent_worker`) instead of the rule-based stub.
- `--parameter-overrides "AllowedOrigins=http://localhost:5173,https://<your-app>.amplifyapp.com"`: allow the deployed dashboard in CORS.

Pass both in one `--parameter-overrides` if you need both.

| Endpoint | What it does |
|---|---|
| `GET /scenarios` | List scenarios |
| `GET /forecast?scenario=` | Hourly P10/P50/P90, actual, CAISO forecast |
| `GET /resources?scenario=` | Full scenario with sources |
| `POST /simulate` | What-if check of a plan (plain code) |
| `POST /runs` | Start an agent run → `202 {run_id}` |
| `GET /runs/{run_id}` | Poll run status, trace, attempts, briefing |
| `POST /runs/{run_id}/decision` | Approve / Edit / Reject with operator name |
| `GET /audit` | Decisions, newest first |

## Team workflow

- `main` is protected. Never push to it directly.
- Use one branch per task, named `<person>/<task-id>-short-name`. Example: `p3/P3-08-simulator`.
- Open a pull request into `main`. Get at least one teammate's review before merging.
- If you change a shared contract (C1–C6), post in the team chat before merging.
- **No secrets in the repo.** The EIA key lives in SSM Parameter Store. `.env` files are git-ignored.

## Data sources

- [EIA API v2](https://www.eia.gov/opendata/): CAISO hourly demand, day-ahead forecast, generation by fuel type
- [Open-Meteo historical weather API](https://open-meteo.com/): hourly temperatures
