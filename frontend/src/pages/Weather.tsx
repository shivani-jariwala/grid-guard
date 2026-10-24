import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { getWeather } from "../api/weather";
import type { WeatherResult } from "../api/weather";

/**
 * 7-day weather forecast with peak-temperature detection.
 * Shows a week overview of daily peak temperatures (hottest day flagged), then
 * a day selector that drives the hourly chart, the peak-hour marker, and the
 * per-city peaks for the chosen day. Evening heat is what stresses the grid
 * once solar fades, so the peak hour matters.
 */

function hourLabel(hour: number): string {
  if (hour === 0) return "12 a.m.";
  if (hour === 12) return "12 p.m.";
  return hour < 12 ? `${hour} a.m.` : `${hour - 12} p.m.`;
}

export default function Weather() {
  const [data, setData] = useState<WeatherResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedDay, setSelectedDay] = useState(0);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const result = await getWeather();
      setData(result);
      setSelectedDay(result.hottestDayIndex); // open on the hottest day
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the weather.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const weekData = useMemo(
    () =>
      data?.days.map((d) => ({
        label: `${d.weekday} ${d.date.slice(5)}`,
        peak: d.peakAvgTemp,
        dayIndex: d.dayIndex,
      })) ?? [],
    [data]
  );

  const day = data?.days[selectedDay];
  const hourData =
    day?.avgByHour.map((t, h) => ({ label: hourLabel(h), temp: t })) ?? [];

  return (
    <section className="page weather">
      <h1>Weather forecast — 7 days</h1>
      <p className="muted">
        A week of hourly temperature across California, with the peak hour flagged
        each day.
      </p>

      <div className="toolbar">
        <button className="btn btn-ghost" onClick={load} disabled={loading}>
          {loading ? "Loading…" : "Refresh live data"}
        </button>
        {data && (
          <span className="muted small">
            Fetched {new Date(data.fetchedAt).toLocaleTimeString()} · live
          </span>
        )}
      </div>

      {loading && <p className="muted">Pulling a 7-day forecast from Open-Meteo…</p>}

      {error && (
        <div className="notice notice-error" role="alert">
          <strong>Couldn't load the weather.</strong> {error}{" "}
          <button className="link-btn" onClick={load}>
            Try again
          </button>
        </div>
      )}

      {data && day && !loading && (
        <>
          <div className="notice notice-risk" role="status">
            <span className="status status-red">Hottest day</span>
            <span>
              This week peaks on{" "}
              <strong>
                {data.days[data.hottestDayIndex].weekday}{" "}
                {data.days[data.hottestDayIndex].date}
              </strong>{" "}
              at{" "}
              <strong>
                {data.days[data.hottestDayIndex].peakAvgTemp.toFixed(1)}°F
              </strong>{" "}
              around {hourLabel(data.days[data.hottestDayIndex].peakAvgHour)}.
              Evenings are the danger: it stays hot while solar fades at sunset.
            </span>
          </div>

          <div className="chart-pair">
            <div className="chart-col">
              <h2>Week overview</h2>
              <div className="chart-wrap">
                <ResponsiveContainer width="100%" height={280}>
                  <BarChart
                    data={weekData}
                    margin={{ top: 10, right: 20, bottom: 0, left: 0 }}
                  >
                    <CartesianGrid stroke="#334155" strokeDasharray="3 3" />
                    <XAxis dataKey="label" stroke="#94a3b8" fontSize={11} />
                    <YAxis
                      stroke="#94a3b8"
                      fontSize={11}
                      tickFormatter={(v) => `${Math.round(v)}°`}
                      domain={["dataMin - 5", "dataMax + 3"]}
                    />
                    <Tooltip
                      contentStyle={{
                        background: "#1e293b",
                        border: "1px solid #334155",
                        borderRadius: 8,
                        color: "#e2e8f0",
                      }}
                      formatter={(value: number) => [`${value.toFixed(1)}°F`, "Peak"]}
                    />
                    <Bar
                      dataKey="peak"
                      radius={[4, 4, 0, 0]}
                      onClick={(_, index) => setSelectedDay(index)}
                      cursor="pointer"
                    >
                      {weekData.map((d) => (
                        <Cell
                          key={d.dayIndex}
                          fill={
                            d.dayIndex === selectedDay
                              ? "#38bdf8"
                              : d.dayIndex === data.hottestDayIndex
                              ? "#d73027"
                              : "#475569"
                          }
                        />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <p className="muted small">Tip: click a bar to see that day's detail.</p>
            </div>

            <div className="chart-col">
              <h2>
                Hourly — {day.weekday} {day.date}
              </h2>
              <div className="chart-wrap">
                <ResponsiveContainer width="100%" height={280}>
                  <LineChart data={hourData} margin={{ top: 10, right: 20, bottom: 0, left: 0 }}>
                    <CartesianGrid stroke="#334155" strokeDasharray="3 3" />
                    <XAxis dataKey="label" stroke="#94a3b8" fontSize={11} interval={3} />
                    <YAxis
                      stroke="#94a3b8"
                      fontSize={11}
                      tickFormatter={(v) => `${Math.round(v)}°`}
                      domain={["dataMin - 3", "dataMax + 3"]}
                    />
                    <Tooltip
                      contentStyle={{
                        background: "#1e293b",
                        border: "1px solid #334155",
                        borderRadius: 8,
                        color: "#e2e8f0",
                      }}
                      formatter={(value: number) => [`${value.toFixed(1)}°F`, "Avg temp"]}
                    />
                    <Line dataKey="temp" stroke="#f97316" strokeWidth={2} dot={false} name="temp" />
                    <ReferenceDot
                      x={hourLabel(day.peakAvgHour)}
                      y={day.peakAvgTemp}
                      r={6}
                      fill="#d73027"
                      stroke="#fff"
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
              <div className="toolbar">
                <label htmlFor="day" className="muted small">
                  Day
                </label>
                <select
                  id="day"
                  className="select"
                  value={selectedDay}
                  onChange={(e) => setSelectedDay(Number(e.target.value))}
                >
                  {data.days.map((d) => (
                    <option key={d.dayIndex} value={d.dayIndex}>
                      {d.weekday} {d.date}
                      {d.dayIndex === data.hottestDayIndex ? " — hottest" : ""}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          <h2>Peak by city — {day.weekday} {day.date}</h2>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>City</th>
                  <th>Peak temp (°F)</th>
                  <th>Peak hour</th>
                </tr>
              </thead>
              <tbody>
                {day.cityPeaks.map((c) => (
                  <tr key={c.city}>
                    <td>{c.city}</td>
                    <td>{c.peakTemp.toFixed(1)}</td>
                    <td>{hourLabel(c.peakHour)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="source-note">
            <strong>Source:</strong> {data.source}
          </p>
        </>
      )}
    </section>
  );
}
