# Product: GridGuard

## What it is

GridGuard helps keep the power on during California heat waves.

1. An ML model (XGBoost) forecasts California's electricity demand for tomorrow, hour by hour (P10 / P50 / P90).
2. An Amazon Bedrock agent makes a plan to avoid blackouts. In order of preference: batteries, pre-cooling buildings, demand response programs, and asking the public to save power (last resort).
3. The agent tests the plan in a plain-code simulator and revises it until every hour is safe (max 5 tries).
4. A human operator reads a short briefing and approves, edits or rejects the plan.
5. The AI never controls the real grid and never sends alerts. Everything is simulated. Alerts are drafts only.

One line: "The model predicts the peak. Bedrock plans, checks and fixes. Dana approves."

## The real event (verified facts only)

- Sept 6, 2022: California demand hit a record 51,426 MW (EIA). CAISO's own figure is about 52,000 MW.
- CAISO declared Energy Emergency Alert 3 (EEA 3), the last step before rolling blackouts.
- About an hour after the peak, Cal OES sent a statewide text alert asking people to save power. There were no rolling blackouts.
- Grid reserves stayed around 6.2% or more of demand even at the worst moment (Cal Advocates).
- Evenings are the danger: it's still hot, but solar fades at sunset.
- NOT VERIFIED: "the text alert cut demand by 2,100+ MW". Don't use this number until P5-01 confirms it.
- Never say CAISO has "almost no decision support". Say operators work "under heavy time pressure".

## Persona

Dana Ruiz (fictional) is the heat-wave shift lead at a California grid operator. On a hot morning she has a few hours to decide when to use batteries, which buildings to pre-cool through utility programs, and whether to request a public alert (Cal OES actually sends it).

- Too little action risks rolling blackouts. Too much and people stop trusting alerts.
- Dana needs an early warning, a clear plan with numbers, the reasons behind it, and the final say on anything public.

## Flow

```
Weather + past demand
  → [1] ML forecast (XGBoost): hourly P10 / P50 / P90
  → [2] Bedrock agent (Converse API + tools):
        get_forecast, get_resources, simulate (revise, max 5), draft_alert, submit_plan
  → [3] Number guard: every number in the briefing must come from a tool output
  → [4] Dashboard: Dana approves / edits / rejects
  → [5] Audit log: who approved what, and when
```

## Goals for the demo

- Working, deployed app with a real agent, real simulator and real forecasts.
- Show where it fails: forecast error, agent eval score, a "CANNOT_MEET_TARGET" case, and an About and limits page.
- Something an operator would trust: number provenance, "Draft only. Not sent." labels, audit log, labelled assumptions.
