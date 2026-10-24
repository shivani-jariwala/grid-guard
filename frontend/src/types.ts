/**
 * Forecast data shapes (contract C1), loaded from the local forecasts JSON.
 */

/** One hour of the forecast (a row in forecasts/{scenario}.json). */
export interface ForecastHour {
  hour: number;
  p10: number;
  p50: number;
  p90: number;
  actual: number;
  caiso_forecast: number;
  temp_avg: number;
}

/** The shape of a forecasts/{scenario}.json file. */
export interface ForecastFile {
  scenario: string;
  description: string;
  stub?: boolean;
  timezone: string;
  units: Record<string, string>;
  rows: ForecastHour[];
}

/** Processed forecast ready for the UI. */
export interface ForecastResult {
  scenario: string;
  description: string;
  stub: boolean;
  hours: ForecastHour[];
  peakHour: number;
  peakP90: number;
}
