/**
 * Live weather client for the Weather page.
 *
 * Pulls a 7-day hourly temperature forecast from the Open-Meteo public API
 * (https://open-meteo.com/) for the five California cities named in the project
 * plan. Open-Meteo is free, keyless and CORS-enabled (and supports up to 16
 * forecast days), so it is called straight from the browser. This is the same
 * weather source the ML pipeline uses for features; here we show it raw and
 * detect the peak temperature per day, per city, and for the statewide average.
 */

export interface CityCoord {
  name: string;
  lat: number;
  lon: number;
}

export const FORECAST_DAYS = 7;

// Coordinates from the project plan (section 7).
export const CITIES: CityCoord[] = [
  { name: "Los Angeles", lat: 34.05, lon: -118.24 },
  { name: "Riverside", lat: 33.95, lon: -117.4 },
  { name: "Fresno", lat: 36.74, lon: -119.79 },
  { name: "Sacramento", lat: 38.58, lon: -121.49 },
  { name: "San Jose", lat: 37.34, lon: -121.89 },
];

/** One hour in the flat 7-day series. */
export interface WeatherHour {
  /** Index into the full series. */
  index: number;
  time: string; // ISO local timestamp
  dayIndex: number; // 0..6
  hour: number; // 0..23 local
  avgTemp: number; // statewide average °F
}

/** Per-city peak for a single day. */
export interface CityDayPeak {
  city: string;
  peakTemp: number;
  peakHour: number;
}

/** A single day of the forecast. */
export interface WeatherDay {
  dayIndex: number;
  date: string; // YYYY-MM-DD (local)
  weekday: string; // "Mon"
  /** 24 statewide hourly averages for this day. */
  avgByHour: number[];
  /** Hottest hour of the statewide average this day. */
  peakAvgHour: number;
  peakAvgTemp: number;
  minAvgTemp: number;
  /** Per-city peaks this day. */
  cityPeaks: CityDayPeak[];
}

export interface WeatherResult {
  fetchedAt: string;
  days: WeatherDay[];
  /** The single hottest day across the week. */
  hottestDayIndex: number;
  source: string;
}

const OPEN_METEO = "https://api.open-meteo.com/v1/forecast";

interface OpenMeteoResponse {
  hourly: { time: string[]; temperature_2m: number[] };
}

function buildUrl(c: CityCoord): string {
  const params = new URLSearchParams({
    latitude: String(c.lat),
    longitude: String(c.lon),
    hourly: "temperature_2m",
    temperature_unit: "fahrenheit",
    forecast_days: String(FORECAST_DAYS),
    timezone: "America/Los_Angeles",
  });
  return `${OPEN_METEO}?${params.toString()}`;
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function peakOf(temps: number[]): { peakTemp: number; peakHour: number } {
  let peakTemp = -Infinity;
  let peakHour = 0;
  temps.forEach((t, h) => {
    if (t > peakTemp) {
      peakTemp = t;
      peakHour = h;
    }
  });
  return { peakTemp, peakHour };
}

export async function getWeather(): Promise<WeatherResult> {
  const responses = await Promise.all(
    CITIES.map(async (c) => {
      const res = await fetch(buildUrl(c));
      if (!res.ok) {
        throw new Error(`Open-Meteo request failed for ${c.name} (${res.status}).`);
      }
      return { city: c.name, data: (await res.json()) as OpenMeteoResponse };
    })
  );

  const times = responses[0].data.hourly.time;
  const total = times.length;
  const dayCount = Math.floor(total / 24);

  const days: WeatherDay[] = [];
  let hottestDayIndex = 0;
  let hottestDayTemp = -Infinity;

  for (let d = 0; d < dayCount; d++) {
    const start = d * 24;
    const avgByHour: number[] = [];

    for (let h = 0; h < 24; h++) {
      const i = start + h;
      const sum = responses.reduce(
        (acc, r) => acc + (r.data.hourly.temperature_2m[i] ?? 0),
        0
      );
      avgByHour.push(Math.round((sum / responses.length) * 10) / 10);
    }

    const { peakTemp: peakAvgTemp, peakHour: peakAvgHour } = peakOf(avgByHour);
    const minAvgTemp = Math.min(...avgByHour);

    const cityPeaks: CityDayPeak[] = responses.map((r) => {
      const dayTemps = r.data.hourly.temperature_2m.slice(start, start + 24);
      const { peakTemp, peakHour } = peakOf(dayTemps);
      return { city: r.city, peakTemp, peakHour };
    });

    const dateObj = new Date(times[start]);
    days.push({
      dayIndex: d,
      date: times[start].slice(0, 10),
      weekday: WEEKDAYS[dateObj.getDay()],
      avgByHour,
      peakAvgHour,
      peakAvgTemp,
      minAvgTemp,
      cityPeaks,
    });

    if (peakAvgTemp > hottestDayTemp) {
      hottestDayTemp = peakAvgTemp;
      hottestDayIndex = d;
    }
  }

  return {
    fetchedAt: new Date().toISOString(),
    days,
    hottestDayIndex,
    source:
      `Live ${dayCount}-day hourly forecast from the Open-Meteo public API ` +
      "(no key needed), for Los Angeles, Riverside, Fresno, Sacramento and San Jose.",
  };
}
