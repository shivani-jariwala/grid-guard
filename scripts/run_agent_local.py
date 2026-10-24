"""Run the real Bedrock agent locally against the repo's scenarios/ and forecasts/ folders.

Uses LocalStore (no S3) and an in-memory run store (no DynamoDB), but a REAL Bedrock
client, so this does call Amazon Bedrock and costs a little. Prints each trace step, the
agent's text notes and the final briefing.

Usage (from the repo root, with the venv active and AWS creds configured):
    python scripts/run_agent_local.py --scenario sept6_2022
    python scripts/run_agent_local.py --scenario sept6_2022_plus5f --target 6
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from shared.config import REPO_ROOT as CFG_ROOT, get_settings  # noqa: E402
from shared.data import LocalStore  # noqa: E402
from shared.runs import new_run  # noqa: E402

from functions.agent_worker.app import run_real_agent  # noqa: E402


class InMemoryRunStore:
    """Holds runs in a dict; mimics the RunStore interface the worker needs."""

    def __init__(self) -> None:
        self.runs: dict = {}

    def create(self, run: dict) -> None:
        self.runs[run["run_id"]] = copy.deepcopy(run)

    def get(self, run_id: str):
        run = self.runs.get(run_id)
        return copy.deepcopy(run) if run is not None else None

    def save(self, run: dict) -> None:
        self.runs[run["run_id"]] = copy.deepcopy(run)

    def fail(self, run: dict, code: str, message: str) -> None:
        run["status"] = "failed"
        run["error"] = {"code": code, "message": message}
        self.save(run)


def _print_trace(run: dict) -> None:
    print("\n--- Trace ---")
    for step in run["trace"]:
        print(f"  {step['step']:>2}. {step['tool']:<13} {step['output_summary']}")


def _print_notes(run: dict) -> None:
    notes = run.get("agent_notes", [])
    print("\n--- Agent notes ---")
    if not notes:
        print("  (none)")
    for i, note in enumerate(notes, 1):
        print(f"  [{i}] {note}")


def _print_final(run: dict) -> None:
    print("\n--- Final ---")
    if run.get("error"):
        print(f"  FAILED {run['error']['code']}: {run['error']['message']}")
        return
    final = run.get("final")
    if not final:
        print("  (no final plan)")
        return
    b = final["briefing"]
    print(f"  status: {final['status']}")
    print(f"  plan_hash: {final['plan_hash']}")
    print("  actions:")
    for a in b["actions"]:
        print(f"    - {a['label']}: hours {a['hours']}, {a['mw']} MW")
    print("  rationale:")
    for r in b["rationale"]:
        print(f"    - {r}")
    print("  risks:")
    for r in b["risks"]:
        print(f"    - {r}")
    print(f"  alert_text: {b['alert_text']!r}  ({b['alert_label']})")
    print(f"  number_check: {b['number_check']}")
    res = final["result"]
    print(f"  result: all_green={res['all_green']} lowest_margin={res['lowest_margin_pct']}% @ hour {res['lowest_margin_hour']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the real Bedrock agent locally.")
    parser.add_argument("--scenario", required=True, help="Scenario id (file in scenarios/).")
    parser.add_argument("--target", type=float, default=None, help="Override the margin target percent.")
    args = parser.parse_args()

    settings = get_settings()
    store = LocalStore(CFG_ROOT / "scenarios", CFG_ROOT / "forecasts")
    runs = InMemoryRunStore()

    # Confirm the scenario exists and get its margin target.
    raw = store.get_scenario_raw(args.scenario)
    target = args.target if args.target is not None else float(raw.get("margin_target_pct", 6))

    run = new_run(args.scenario, target, settings.bedrock_model_id or "us.anthropic.claude-sonnet-5-5")
    runs.create(run)

    model = settings.bedrock_model_id or "us.anthropic.claude-sonnet-5-5 (default)"
    print(f"Running agent on scenario '{args.scenario}' (target {target:g}%) with model {model} ...")
    run_real_agent(run["run_id"], runs, store, settings=settings)

    final_run = runs.get(run["run_id"])
    print(f"\nRun {final_run['run_id']} -> status {final_run['status']}")
    print(f"Counts: {final_run['counts']}")
    _print_trace(final_run)
    _print_notes(final_run)
    _print_final(final_run)


if __name__ == "__main__":
    main()
