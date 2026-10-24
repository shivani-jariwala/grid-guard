import { useEffect, useRef, useState } from "react";
import {
  getRun,
  listScenarios,
  sendDecision,
  startRun,
  type Briefing,
  type HourStatus,
  type Run,
  type Scenario,
} from "../api/agent";

/**
 * Agent run + briefing + decision, ported from demo/index.html.
 * Three sections, all backed by the live GridGuard API:
 *   1. Make a plan  — start a run, poll it, show the attempts timeline.
 *   2. Operator briefing — status badge, actions, rationale, risks, draft alert.
 *   3. Your decision — operator name + note, Approve / Reject.
 */

type MsgKind = "info" | "error" | "success";
interface Msg {
  kind: MsgKind;
  text: string;
}

function fmtHours(hours?: number[]): string {
  if (!hours || !hours.length) return "—";
  const h = [...hours].sort((a, b) => a - b);
  const contiguous = h.every((v, i) => i === 0 || v === h[i - 1] + 1);
  return contiguous && h.length > 1
    ? `${h[0]}:00–${h[h.length - 1]}:00`
    : h.map((x) => `${x}:00`).join(", ");
}

function Message({ msg }: { msg: Msg | null }) {
  if (!msg) return null;
  return <div className={`msg ${msg.kind}`}>{msg.text}</div>;
}

function HourStrip({ hours }: { hours: HourStatus[] }) {
  const sorted = [...hours].sort((a, b) => a.hour - b.hour);
  return (
    <>
      <div className="hourstrip" role="img" aria-label="Hourly status">
        {sorted.map((h) => (
          <div
            key={h.hour}
            className={`hourbox ${h.status}`}
            title={`Hour ${h.hour}: ${h.status} (margin ${h.margin_pct}%)`}
          >
            {h.hour}
          </div>
        ))}
      </div>
      <div className="hourstrip-legend">
        <span className="status status-green">GREEN</span>
        <span className="status status-yellow">YELLOW</span>
        <span className="status status-red">RED</span>
      </div>
    </>
  );
}

function Timeline({ run }: { run: Run }) {
  const trace = run.trace ?? [];
  const sims = run.simulations ?? [];
  let simIndex = 0;
  return (
    <ul className="timeline agent-timeline">
      {trace.map((step) => {
        let strip: HourStatus[] | undefined;
        if (step.tool === "simulate") {
          const sim = sims[simIndex++];
          strip = sim?.result?.hours;
        }
        return (
          <li key={step.step}>
            <div className="step-head">
              <span className="step-n">#{step.step}</span>
              <span className="step-tool">{step.tool}</span>
            </div>
            {step.output_summary && (
              <div className="step-summary">{step.output_summary}</div>
            )}
            {strip && strip.length > 0 && <HourStrip hours={strip} />}
          </li>
        );
      })}
    </ul>
  );
}

function BriefingPanel({ final }: { final: NonNullable<Run["final"]> }) {
  const b: Briefing = final.briefing ?? {};
  const status = final.status || b.status || "CANNOT_MEET_TARGET";
  const label = status === "ALL_GREEN" ? "All hours GREEN" : "Cannot meet target";
  const actions = b.actions ?? [];

  return (
    <section className="panel">
      <h2>Operator briefing</h2>
      <div className={`badge ${status === "ALL_GREEN" ? "badge-green" : "badge-red"}`}>
        {label}
      </div>

      <h3 className="sub-h">Planned actions</h3>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Action</th>
              <th>Hours</th>
              <th>MW</th>
            </tr>
          </thead>
          <tbody>
            {actions.length === 0 ? (
              <tr>
                <td colSpan={3} className="muted">
                  No actions — the no-action plan was used.
                </td>
              </tr>
            ) : (
              actions.map((a, i) => (
                <tr key={i}>
                  <td>{a.label || a.action}</td>
                  <td>{fmtHours(a.hours)}</td>
                  <td>{a.mw != null ? Math.round(a.mw).toLocaleString() : "—"}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <h3 className="sub-h">Why this plan</h3>
      <ul className="bullets">
        {(b.rationale && b.rationale.length ? b.rationale : ["No rationale provided."]).map(
          (t, i) => (
            <li key={i}>{t}</li>
          )
        )}
      </ul>

      <h3 className="sub-h">What could go wrong</h3>
      <ul className="bullets">
        {(b.risks && b.risks.length ? b.risks : ["No risks listed."]).map((t, i) => (
          <li key={i}>{t}</li>
        ))}
      </ul>

      {b.alert_text && (
        <div className="alert-draft">
          <span className="label">{b.alert_label || "Draft only. Not sent."}</span>
          <br />
          {b.alert_text}
        </div>
      )}
    </section>
  );
}

export default function Agent() {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [scenarioId, setScenarioId] = useState("");
  const [topMsg, setTopMsg] = useState<Msg | null>(null);

  const [run, setRun] = useState<Run | null>(null);
  const [runMsg, setRunMsg] = useState<Msg | null>(null);
  const [planning, setPlanning] = useState(false);

  const [operator, setOperator] = useState("");
  const [note, setNote] = useState("");
  const [decisionMsg, setDecisionMsg] = useState<Msg | null>(null);
  const [deciding, setDeciding] = useState(false);

  const runIdRef = useRef<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Load scenarios once.
  useEffect(() => {
    listScenarios()
      .then((d) => {
        setScenarios(d.scenarios ?? []);
        if (d.scenarios?.length) setScenarioId(d.scenarios[0].id);
      })
      .catch((e) =>
        setTopMsg({ kind: "error", text: `Could not load scenarios: ${e.message}` })
      );
  }, []);

  // Stop polling on unmount.
  useEffect(() => () => stopPolling(), []);

  function stopPolling() {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    setPlanning(false);
  }

  function resetRun() {
    stopPolling();
    runIdRef.current = null;
    setRun(null);
    setRunMsg(null);
    setDecisionMsg(null);
  }

  async function poll() {
    const id = runIdRef.current;
    if (!id) return;
    try {
      const r = await getRun(id);
      setRun(r);
      if (r.status === "done") {
        stopPolling();
        setRunMsg({ kind: "success", text: "Plan ready. Review the briefing below." });
      } else if (r.status === "failed") {
        stopPolling();
        const err = r.error || {};
        setRunMsg({
          kind: "error",
          text: `The agent run failed: ${err.message || err.code || "unknown error"}.`,
        });
      }
    } catch (e) {
      stopPolling();
      setRunMsg({ kind: "error", text: `Lost contact with the run: ${(e as Error).message}` });
    }
  }

  async function makePlan() {
    resetRun();
    setPlanning(true);
    setRunMsg({ kind: "info", text: "Starting an agent run…" });
    try {
      const res = await startRun(scenarioId);
      runIdRef.current = res.run_id;
      setRunMsg({ kind: "info", text: "Agent is working… (polling every 2s)" });
      poll();
      pollRef.current = setInterval(poll, 2000);
    } catch (e) {
      const err = e as { message: string; code?: string };
      const extra = err.code === "DAILY_LIMIT_REACHED" ? " (daily run limit reached)" : "";
      setRunMsg({ kind: "error", text: `Could not start a run: ${err.message}${extra}` });
      setPlanning(false);
    }
  }

  async function decide(decision: "APPROVE" | "REJECT") {
    const id = runIdRef.current;
    if (!id) {
      setDecisionMsg({ kind: "error", text: "No run to decide on yet." });
      return;
    }
    if (!operator.trim()) {
      setDecisionMsg({ kind: "error", text: "Please enter the operator name first." });
      return;
    }
    setDeciding(true);
    setDecisionMsg({ kind: "info", text: "Recording your decision…" });
    try {
      const res = await sendDecision(id, {
        operator_name: operator.trim(),
        decision,
        note: note.trim(),
      });
      const verb = decision === "APPROVE" ? "approved" : "rejected";
      setDecisionMsg({
        kind: "success",
        text: `Plan ${verb} by ${res.operator_name || operator.trim()} and recorded in the audit log${
          res.timestamp ? ` at ${res.timestamp}` : ""
        }.`,
      });
    } catch (e) {
      const err = e as { message: string; code?: string };
      const extra = err.code === "RUN_NOT_DONE" ? " (the run isn't finished yet)" : "";
      setDecisionMsg({ kind: "error", text: `Could not record the decision: ${err.message}${extra}` });
    } finally {
      setDeciding(false);
    }
  }

  const done = run?.status === "done";
  const counts = run?.counts;

  return (
    <div className="agent">
      <h2>Make a plan</h2>
      <Message msg={topMsg} />

      <div className="toolbar">
        <label htmlFor="agent-scenario" className="muted small">
          Scenario
        </label>
        <select
          id="agent-scenario"
          className="select"
          value={scenarioId}
          onChange={(e) => setScenarioId(e.target.value)}
          disabled={scenarios.length === 0 || planning}
        >
          {scenarios.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name} — {s.date} (target {s.margin_target_pct}%)
            </option>
          ))}
        </select>
      </div>

      {/* 1. Make a plan */}
      <section className="panel">
        <p className="hint">
          The agent forecasts the risk, drafts a plan, tests it in the simulator, and
          revises until every hour is safe.
        </p>
        <button className="btn" onClick={makePlan} disabled={planning || !scenarioId}>
          {planning ? (
            <>
              <span className="spinner" /> Planning…
            </>
          ) : (
            "Make a plan"
          )}
        </button>
        <div className="bedrock">Powered by Amazon Bedrock (Claude)</div>
        <Message msg={runMsg} />
        {counts && (
          <div className="counts">
            Tool calls: {counts.tool_calls ?? 0} · Simulations: {counts.simulate_calls ?? 0} ·
            Status: {run?.status}
          </div>
        )}
        {run && <Timeline run={run} />}
      </section>

      {/* 2. Operator briefing */}
      {done && run?.final && <BriefingPanel final={run.final} />}

      {/* 3. Your decision */}
      {done && run?.final && (
        <section className="panel">
          <h2>Your decision</h2>
          <p className="hint">
            You have the final say. Nothing here is sent to the public — alerts are drafts
            only.
          </p>
          <div className="field">
            <label htmlFor="operator">Operator name</label>
            <input
              id="operator"
              className="input"
              type="text"
              maxLength={60}
              placeholder="e.g. Dana Ruiz"
              value={operator}
              onChange={(e) => setOperator(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="note">Note (optional)</label>
            <textarea
              id="note"
              className="input"
              maxLength={500}
              placeholder="Anything to record with this decision"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </div>
          <div className="row">
            <button className="btn btn-approve" onClick={() => decide("APPROVE")} disabled={deciding}>
              Approve plan
            </button>
            <button className="btn btn-reject" onClick={() => decide("REJECT")} disabled={deciding}>
              Reject plan
            </button>
          </div>
          <Message msg={decisionMsg} />
        </section>
      )}
    </div>
  );
}
