import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { getForecast } from "../api/forecast";

/**
 * Forecast screen (task P4-06).
 * Reads the pre-computed forecast from the bundled forecasts JSON (contract C1)
 * and renders a risk-window banner, a P10-P90 band with the P50 line and the
 * CAISO day-ahead forecast, and an hourly table. No backend.
 */

function hourLabel(hour: number): string {
  if (hour === 0) return "12 a.m.";
  if (hour === 12) return "12 p.m.";
  return hour < 12 ? `${hour} a.m.` : `${hour - 12} p.m.`;
}

function fmtMw(mw: number): string {
  return `${Math.round(mw).toLocaleString("en-US")} MW`;
}

export default function Forecast() {
  const data = getForecast();

  const chartData = data.hours.map((h) => ({
    label: hourLabel(h.hour),
    p10: h.p10,
    p50: h.p50,
    p90: h.p90,
    band: h.p90 - h.p10,
    caiso: h.caiso_forecast,
  }));

  return (
    <section className="page forecast">
      <h1>Forecast</h1>
      <p className="muted">
        Hourly demand for California on Sept 6, 2022, low to high (P10&ndash;P90).
      </p>

      <div className="notice notice-risk" role="status">
        <span className="status status-red">RED risk window</span>
        <span>
          Highest demand is around <strong>{hourLabel(data.peakHour)}</strong> at{" "}
          <strong>{fmtMw(data.peakP90)}</strong> (P90). Evenings are the danger:
          it's still hot but solar fades at sunset.
        </span>
      </div>

      <div className="chart-wrap">
        <ResponsiveContainer width="100%" height={320}>
          <ComposedChart
            data={chartData}
            margin={{ top: 10, right: 20, bottom: 0, left: 0 }}
          >
            <CartesianGrid stroke="#334155" strokeDasharray="3 3" />
            <XAxis dataKey="label" stroke="#94a3b8" fontSize={11} interval={2} />
            <YAxis
              stroke="#94a3b8"
              fontSize={11}
              tickFormatter={(v) => `${Math.round(v / 1000)}k`}
              domain={["dataMin - 2000", "dataMax + 2000"]}
            />
            <Tooltip
              contentStyle={{
                background: "#1e293b",
                border: "1px solid #334155",
                borderRadius: 8,
                color: "#e2e8f0",
              }}
              formatter={(value: number, name: string) => {
                if (name === "band") return [null, null];
                return [fmtMw(value), name.toUpperCase()];
              }}
            />
            <Legend
              payload={[
                { value: "P10–P90 band", type: "rect", id: "band", color: "#38bdf8" },
                { value: "P50 (median)", type: "line", id: "p50", color: "#f8fafc" },
                { value: "CAISO day-ahead", type: "line", id: "caiso", color: "#fbbf24" },
              ]}
            />
            <Area dataKey="p10" stackId="band" stroke="none" fill="none" />
            <Area
              dataKey="band"
              stackId="band"
              stroke="none"
              fill="#38bdf8"
              fillOpacity={0.25}
              name="band"
            />
            <Line dataKey="p50" stroke="#f8fafc" strokeWidth={2} dot={false} name="p50" />
            <Line
              dataKey="caiso"
              stroke="#fbbf24"
              strokeWidth={1.5}
              strokeDasharray="4 3"
              dot={false}
              name="caiso"
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <h2>Hourly detail</h2>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Hour</th>
              <th>Temp (°F)</th>
              <th>P10</th>
              <th>P50</th>
              <th>P90</th>
              <th>Actual</th>
              <th>CAISO</th>
            </tr>
          </thead>
          <tbody>
            {data.hours.map((h) => (
              <tr key={h.hour} className={h.hour === data.peakHour ? "peak-row" : ""}>
                <td>{hourLabel(h.hour)}</td>
                <td>{h.temp_avg}</td>
                <td>{h.p10.toLocaleString("en-US")}</td>
                <td>{h.p50.toLocaleString("en-US")}</td>
                <td>{h.p90.toLocaleString("en-US")}</td>
                <td>{h.actual.toLocaleString("en-US")}</td>
                <td>{h.caiso_forecast.toLocaleString("en-US")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="source-note">
        <strong>Source:</strong> {data.description}
        {data.stub ? " (stub data)" : ""}
      </p>
    </section>
  );
}
