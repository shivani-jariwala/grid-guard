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

## Team workflow

- `main` is protected. Never push to it directly.
- Use one branch per task, named `<person>/<task-id>-short-name`. Example: `p3/P3-08-simulator`.
- Open a pull request into `main`. Get at least one teammate's review before merging.
- If you change a shared contract (C1–C6), post in the team chat before merging.
- **No secrets in the repo.** The EIA key lives in SSM Parameter Store. `.env` files are git-ignored.

## Data sources

- [EIA API v2](https://www.eia.gov/opendata/): CAISO hourly demand, day-ahead forecast, generation by fuel type
- [Open-Meteo historical weather API](https://open-meteo.com/): hourly temperatures
