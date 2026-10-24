"""Evaluate on 2022: MAPE, Sept 6 peak error, P10-P90 coverage, vs CAISO DF and naive. Owner: P1 (P1-08).

Uses the 2022 test rows only (never seen in training or tuning). Loads the three quantile
models and scores:

  - p50 MAPE for all of 2022 and for summer 2022 (Jun-Sep).
  - Sept 6, 2022 (local) predicted vs actual peak: MW, hour, error in MW and %.
  - P10-P90 coverage (share of hours with actual between p10 and p90), all-2022 and summer.
  - Baselines over the same periods: CAISO day-ahead forecast (caiso_df) MAPE and
    "same hour last week" (demand_lag_168h) MAPE.

Writes ml/artifacts/eval_results.json and docs/forecast_eval.md.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from build_features import FEATURES
from train import ARTIFACT_DIR, load_bundle, load_splits, mape, predict_quantiles

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
EVAL_JSON = ARTIFACT_DIR / "eval_results.json"
EVAL_MD = DOCS_DIR / "forecast_eval.md"

SUMMER_MONTHS = (6, 7, 8, 9)  # Jun-Sep
HONEST_NOTE = (
    "Trained with actual temperatures; real-world error with forecast "
    "temperatures would be higher."
)


def coverage(actual: np.ndarray, p10: np.ndarray, p90: np.ndarray) -> float:
    """Share of hours (%) where actual is within [p10, p90]."""
    inside = (actual >= p10) & (actual <= p90)
    return float(np.mean(inside) * 100.0)


def build_scored_test() -> pd.DataFrame:
    """Return the 2022 test frame with calibrated p10/p50/p90 predictions attached."""
    test = load_splits()["test"].copy()
    bundle = load_bundle()
    preds = predict_quantiles(bundle, test)
    test["p10"] = preds["p10"]
    test["p50"] = preds["p50"]
    test["p90"] = preds["p90"]
    return test


def period_metrics(frame: pd.DataFrame) -> dict:
    actual = frame["demand"].to_numpy()
    return {
        "hours": int(len(frame)),
        "mape_p50": round(mape(actual, frame["p50"].to_numpy()), 3),
        "coverage_p10_p90": round(
            coverage(actual, frame["p10"].to_numpy(), frame["p90"].to_numpy()), 1
        ),
        "mape_caiso_df": round(mape(actual, frame["caiso_df"].to_numpy()), 3),
        "mape_lag_168h": round(mape(actual, frame["demand_lag_168h"].to_numpy()), 3),
    }


def sept6_analysis(test: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    """Peak comparison and the 24-hour local table for 2022-09-06."""
    day = test[test["date_local"] == pd.Timestamp("2022-09-06")].copy()
    day = day.sort_values("hour").reset_index(drop=True)

    actual_idx = day["demand"].idxmax()
    pred_idx = day["p50"].idxmax()
    actual_peak = float(day.loc[actual_idx, "demand"])
    actual_hour = int(day.loc[actual_idx, "hour"])
    pred_peak = float(day.loc[pred_idx, "p50"])
    pred_hour = int(day.loc[pred_idx, "hour"])

    err_mw = pred_peak - actual_peak
    err_pct = err_mw / actual_peak * 100.0

    # p90 at the actual peak hour: does the upper band cover the record peak?
    p90_at_actual_peak = float(day.loc[actual_idx, "p90"])

    summary = {
        "actual_peak_mw": round(actual_peak, 1),
        "actual_peak_hour": actual_hour,
        "predicted_peak_mw": round(pred_peak, 1),
        "predicted_peak_hour": pred_hour,
        "peak_error_mw": round(err_mw, 1),
        "peak_error_pct": round(err_pct, 2),
        "p90_at_actual_peak_hour": round(p90_at_actual_peak, 1),
        "p90_covers_actual_peak": bool(p90_at_actual_peak >= actual_peak),
    }
    table = day[["hour", "demand", "p10", "p50", "p90", "caiso_df"]].copy()
    return summary, table


def render_markdown(results: dict, sept6_table: pd.DataFrame) -> str:
    lines: list[str] = []
    lines.append("# Forecast evaluation (2022 test set)\n")
    lines.append(f"_{HONEST_NOTE}_\n")

    lines.append("## Accuracy and coverage\n")
    lines.append(
        "| Period | Hours | MAPE p50 | P10-P90 coverage | CAISO DF MAPE | "
        "Last-week MAPE |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|")
    for label, key in (("All 2022", "all_2022"), ("Summer 2022 (Jun-Sep)", "summer_2022")):
        m = results[key]
        lines.append(
            f"| {label} | {m['hours']} | {m['mape_p50']}% | "
            f"{m['coverage_p10_p90']}% | {m['mape_caiso_df']}% | {m['mape_lag_168h']}% |"
        )
    lines.append("\n_Coverage target is about 80%._\n")

    s = results["sept6_2022"]
    lines.append("## Sept 6, 2022 peak\n")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Actual peak | {s['actual_peak_mw']:.0f} MW at hour {s['actual_peak_hour']} |")
    lines.append(
        f"| Predicted peak (p50) | {s['predicted_peak_mw']:.0f} MW at hour "
        f"{s['predicted_peak_hour']} |"
    )
    lines.append(f"| Peak error | {s['peak_error_mw']:.0f} MW ({s['peak_error_pct']:+.2f}%) |")
    lines.append(
        f"| P90 at actual peak hour | {s['p90_at_actual_peak_hour']:.0f} MW "
        f"({'covers' if s['p90_covers_actual_peak'] else 'below'} actual) |"
    )
    lines.append("")

    lines.append("## Sept 6, 2022 hourly (local time)\n")
    lines.append("| Hour | Actual | P10 | P50 | P90 | CAISO DF |")
    lines.append("|---:|---:|---:|---:|---:|---:|")
    for _, r in sept6_table.iterrows():
        lines.append(
            f"| {int(r['hour'])} | {r['demand']:.0f} | {r['p10']:.0f} | "
            f"{r['p50']:.0f} | {r['p90']:.0f} | {r['caiso_df']:.0f} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    test = build_scored_test()

    all_2022 = period_metrics(test)
    summer = period_metrics(test[test["date_local"].dt.month.isin(SUMMER_MONTHS)])
    sept6_summary, sept6_table = sept6_analysis(test)

    results = {
        "all_2022": all_2022,
        "summer_2022": summer,
        "sept6_2022": sept6_summary,
        "coverage_target_pct": 80,
        "honest_note": HONEST_NOTE,
    }

    with open(EVAL_JSON, "w") as f:
        json.dump(results, f, indent=2)
    EVAL_MD.write_text(render_markdown(results, sept6_table), encoding="utf-8")

    # Console table.
    print("=== Forecast evaluation (2022 test set) ===")
    print(f"Note: {HONEST_NOTE}\n")
    header = f"{'Period':<22}{'Hours':>7}{'MAPE p50':>11}{'Coverage':>11}{'CAISO DF':>11}{'LastWeek':>11}"
    print(header)
    print("-" * len(header))
    for label, m in (("All 2022", all_2022), ("Summer 2022 (Jun-Sep)", summer)):
        print(
            f"{label:<22}{m['hours']:>7}{m['mape_p50']:>10.3f}%"
            f"{m['coverage_p10_p90']:>10.1f}%{m['mape_caiso_df']:>10.3f}%"
            f"{m['mape_lag_168h']:>10.3f}%"
        )
    print(f"\nCoverage target: ~{results['coverage_target_pct']}%")

    s = sept6_summary
    print("\n--- Sept 6, 2022 peak ---")
    print(f"Actual peak    : {s['actual_peak_mw']:.0f} MW at hour {s['actual_peak_hour']}")
    print(f"Predicted peak : {s['predicted_peak_mw']:.0f} MW at hour {s['predicted_peak_hour']}")
    print(f"Peak error     : {s['peak_error_mw']:.0f} MW ({s['peak_error_pct']:+.2f}%)")
    print(
        f"P90 at actual peak hour {s['actual_peak_hour']}: "
        f"{s['p90_at_actual_peak_hour']:.0f} MW "
        f"({'covers' if s['p90_covers_actual_peak'] else 'below'} actual peak)"
    )

    print(f"\nSaved: {EVAL_JSON}")
    print(f"Saved: {EVAL_MD}")


if __name__ == "__main__":
    main()
