# Tech stack and conventions

## Stack

| Part | Choice |
|---|---|
| ML | XGBoost 2.x (Python), `reg:quantileerror` at alpha 0.1 / 0.5 / 0.9. Trained on a laptop. No SageMaker. |
| Forecast storage | Amazon S3: pre-computed `forecasts/{scenario}.json` |
| Agent | Amazon Bedrock (Claude) via the Converse API with tool use |
| AI safety | Amazon Bedrock Guardrails (content filters, denied topic, word filters, contextual grounding) |
| Backend | AWS Lambda (Python 3.12) + API Gateway HTTP API |
| Infrastructure | AWS SAM (`backend/template.yaml`) |
| Database | Amazon DynamoDB: `Runs`, `Decisions`, daily run counter |
| Secrets / config | SSM Parameter Store: EIA API key |
| Frontend | React + Vite + TypeScript, Recharts, hosted on AWS Amplify Hosting |
| Logs | Amazon CloudWatch |
| Tests | pytest (backend, ML, number guard); frontend lint on save |

- AWS region: `us-east-1` (the only region the hackathon account allows). Still confirm the Claude model and Guardrails are enabled there (P3-16).
- Bedrock model ID lives in config, never hard-coded.

## Data

- EIA API v2, CAISO hourly: demand (`type=D`), day-ahead forecast (`type=DF`), generation by fuel type. Max 5,000 rows per call, so paginate with `offset`.
- Open-Meteo historical API, °F: Los Angeles, Riverside, Fresno, Sacramento, San Jose.
- EIA times are UTC. Convert to `America/Los_Angeles` and handle DST.

## Shared contracts

The data shapes between areas (forecast, scenario, plan, simulator result, run record, briefing) are contracts C1–C6 in `docs/contracts.md`. Build against them. Don't change them without telling the team.

## Coding conventions

- Python: 3.12, type hints on public functions, small pure functions, no global state in the simulator.
- Lambda: one folder per function under `backend/functions/`, shared logic in `backend/shared/`.
- Lambda handlers validate input and return clear JSON errors (status code + message).
- IAM: least privilege per Lambda.
- TypeScript: strict mode. Every number shown in the UI has a source tooltip.
- Accessibility: status is shown as a text label plus color (GREEN / YELLOW / RED), never color alone.
- Dependencies: pin exact versions.
