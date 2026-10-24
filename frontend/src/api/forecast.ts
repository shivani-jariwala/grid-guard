import forecastJson from "../data/sept6_2022.json";
import type { ForecastFile, ForecastResult } from "../types";

/**
 * Forecast loader.
 *
 * Reads the pre-computed forecast directly from the bundled JSON file
 * (forecasts/{scenario}.json, contract C1). No backend and no network call —
 * the data is imported at build time by Vite.
 */

function process(file: ForecastFile): ForecastResult {
  let peakHour = 0;
  let peakP90 = -Infinity;
  for (const row of file.rows) {
    if (row.p90 > peakP90) {
      peakP90 = row.p90;
      peakHour = row.hour;
    }
  }
  return {
    scenario: file.scenario,
    description: file.description,
    stub: Boolean(file.stub),
    hours: file.rows,
    peakHour,
    peakP90,
  };
}

export function getForecast(): ForecastResult {
  return process(forecastJson as ForecastFile);
}
