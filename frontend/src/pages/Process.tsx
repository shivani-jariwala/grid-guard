import { Link } from "react-router-dom";

/**
 * Process showcase page. Walks through the GridGuard flow (plan section 4),
 * step by step, so a viewer can follow exactly what happens and why. Links into
 * the live screens where they exist.
 */

interface FlowStep {
  n: number;
  title: string;
  body: string;
  to?: string;
  cta?: string;
}

const FLOW: FlowStep[] = [
  {
    n: 1,
    title: "ML forecast",
    body:
      "An XGBoost model turns weather and past demand into tomorrow's hourly demand at P10 / P50 / P90. We plan against P90, the high case.",
    to: "/forecast",
    cta: "See the forecast",
  },
  {
    n: 2,
    title: "Bedrock agent plans",
    body:
      "A Bedrock (Claude) agent calls tools: get_forecast, get_resources, simulate, draft_alert, submit_plan. It prefers the least disruptive levers first: batteries, pre-cooling, demand response, then a public alert as a last resort.",
  },
  {
    n: 3,
    title: "Simulate and revise",
    body:
      "A plain-code simulator (no AI) scores every hour GREEN / YELLOW / RED. If any hour is short, the agent revises and simulates again, up to five tries.",
  },
  {
    n: 4,
    title: "Number check",
    body:
      "Every number in the briefing is traced back to a tool output. If a number doesn't match, it's flagged and the tool numbers are shown instead.",
  },
  {
    n: 5,
    title: "Operator approves",
    body:
      "A human operator reads a short briefing and approves, edits or rejects. Any public alert is a draft, clearly labelled \"Not sent.\"",
  },
  {
    n: 6,
    title: "Audit log",
    body: "Who approved what, and when. Every decision is recorded.",
  },
];

export default function Process() {
  return (
    <section className="page process">
      <h1>How GridGuard works</h1>
      <p className="muted">The full flow, step by step.</p>

      <h2>The flow</h2>
      <ol className="process-flow">
        {FLOW.map((s) => (
          <li key={s.n}>
            <span className="step-num">{s.n}</span>
            <div>
              <h3>{s.title}</h3>
              <p>{s.body}</p>
              {s.to && s.cta && (
                <Link to={s.to} className="link-btn">
                  {s.cta} →
                </Link>
              )}
            </div>
          </li>
        ))}
      </ol>

      <p className="tagline">
        "The model predicts the peak. Bedrock plans, checks and fixes. Dana approves."
      </p>
    </section>
  );
}
