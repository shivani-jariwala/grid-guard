import { useState } from "react";
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
import Agent from "./Agent";

/**
 * Home / Forecast screen.
 * Top: the pre-computed forecast (contract C1) — risk-window banner, P10-P90
 * band with the P50 line and CAISO day-ahead, and an hourly table.
 * Below: the live agent flow (Make a plan, Operator briefing, Your decision),
 * embedded from the Agent component.
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
  const [showHourly, setShowHourly] = useState(false);

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
      <h1>Home</h1>
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
            <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" />
            <XAxis dataKey="label" stroke="#5b6b7b" fontSize={11} interval={2} />
            <YAxis
              stroke="#5b6b7b"
              fontSize={11}
              tickFormatter={(v) => `${Math.round(v / 1000)}k`}
              domain={["dataMin - 2000", "dataMax + 2000"]}
            />
            <Tooltip
              contentStyle={{
                background: "#ffffff",
                border: "1px solid #e2e8f0",
                borderRadius: 8,
                color: "#1b2430",
                boxShadow: "0 1px 3px rgba(16,24,40,.12)",
              }}
              formatter={(value: number, name: string) => {
                if (name === "band") return [null, null];
                return [fmtMw(value), name.toUpperCase()];
              }}
            />
            <Legend
              payload={[
                { value: "P10–P90 band", type: "rect", id: "band", color: "#1f6feb" },
                { value: "P50 (median)", type: "line", id: "p50", color: "#1f6feb" },
                { value: "CAISO day-ahead", type: "line", id: "caiso", color: "#5b6b7b" },
              ]}
            />
            <Area dataKey="p10" stackId="band" stroke="none" fill="none" />
            <Area
              dataKey="band"
              stackId="band"
              stroke="none"
              fill="#1f6feb"
              fillOpacity={0.14}
              name="band"
            />
            <Line dataKey="p50" stroke="#1f6feb" strokeWidth={2} dot={false} name="p50" />
            <Line
              dataKey="caiso"
              stroke="#5b6b7b"
              strokeWidth={1.5}
              strokeDasharray="6 4"
              dot={false}
              name="caiso"
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <button
        type="button"
        className="collapse-toggle"
        onClick={() => setShowHourly((v) => !v)}
        aria-expanded={showHourly}
        aria-controls="hourly-detail"
      >
        <span className={`chevron${showHourly ? " open" : ""}`} aria-hidden="true">
          ▶
        </span>
        <h2>Hourly detail</h2>
      </button>

      {showHourly && (
        <div className="table-wrap" id="hourly-detail">
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
      )}

      <p className="source-note">
        <strong>Source:</strong> {data.description}
        {data.stub ? " (stub data)" : ""}
      </p>

      <hr className="section-divider" />

      <Agent />
    </section>
  );
}
