# Rules (always apply)

## Safety and honesty

- The AI agent never controls real grid equipment and never sends alerts. Public alerts are drafts for a human operator.
- Every number shown to users must come from the forecast or the simulator. The number guard checks the briefing against tool outputs.
- The simulator is pure Python with unit tests. No AI inside it.
- Plan hashes are computed on the server only (SHA-256 of canonical plan JSON). Never trust a hash from the model.
- Label every assumption in the UI (for example `firm_mw` estimated from EIA actuals; batteries assumed charged from midday solar; no recharge modeled).

## ML

- Never train on 2022 data. Train on 2019–2021, test on 2022.
- No demand lags shorter than 48 hours. The last 24 h aren't known when a day-ahead forecast is made.

## Security

- No secrets in code or in the repo. Use SSM Parameter Store (EIA key).
- Security is cost protection only (judges watch a video, they don't use the app). Keep HTTP API throttling, the daily run cap (100) and the Budgets alarm in place. No login: production would add Cognito.
- No personal data. Store the operator name only.

## Working in this repo

- Never run `git commit` or `git push` without explicit approval from the person you're working with.
- Never push directly to `main`. Use a branch per task and a pull request.
- Check `tasks.md` for task ownership and contracts before changing another area's code.
