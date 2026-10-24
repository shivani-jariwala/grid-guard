/**
 * Agent-run API client — ports the backend calls from demo/index.html into the
 * React app. Talks to the deployed GridGuard API (API Gateway -> Lambda).
 *
 * Endpoints used:
 *   GET  /scenarios
 *   GET  /forecast?scenario=
 *   POST /runs                      { scenario_id }         -> { run_id }
 *   GET  /runs/{run_id}             -> run (status, trace, simulations, final)
 *   POST /runs/{run_id}/decision    { operator_name, decision, note }
 *
 * The base URL can be overridden with VITE_API_BASE; it defaults to the demo's
 * deployed stage.
 */

const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ??
  "https://ebhacp9l2e.execute-api.us-east-1.amazonaws.com";

export class ApiError extends Error {
  code?: string;
  constructor(message: string, code?: string) {
    super(message);
    this.name = "ApiError";
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(API_BASE + path, init);
  } catch {
    throw new ApiError(`Could not reach the API at ${API_BASE}.`);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const code = data?.error?.code as string | undefined;
    const message =
      data?.error?.message || data?.message || `HTTP ${res.status}`;
    throw new ApiError(message, code);
  }
  return data as T;
}

export interface Scenario {
  id: string;
  name: string;
  date: string;
  margin_target_pct: number;
}

export interface HourStatus {
  hour: number;
  status: "GREEN" | "YELLOW" | "RED";
  margin_pct: number;
}

export interface TraceStep {
  step: number;
  tool: string;
  output_summary?: string;
}

export interface Simulation {
  result?: { hours?: HourStatus[] };
}

export interface BriefingAction {
  label?: string;
  action?: string;
  hours?: number[];
  mw?: number | null;
}

export interface Briefing {
  status?: string;
  actions?: BriefingAction[];
  rationale?: string[];
  risks?: string[];
  alert_text?: string;
  alert_label?: string;
}

export interface Run {
  run_id?: string;
  status: "running" | "done" | "failed" | string;
  counts?: { tool_calls?: number; simulate_calls?: number };
  trace?: TraceStep[];
  simulations?: Simulation[];
  final?: { status?: string; briefing?: Briefing };
  error?: { code?: string; message?: string };
}

export function listScenarios(): Promise<{ scenarios: Scenario[] }> {
  return request("/scenarios");
}

export function startRun(scenarioId: string): Promise<{ run_id: string }> {
  return request("/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scenario_id: scenarioId }),
  });
}

export function getRun(runId: string): Promise<Run> {
  return request(`/runs/${encodeURIComponent(runId)}`);
}

export function sendDecision(
  runId: string,
  body: { operator_name: string; decision: "APPROVE" | "REJECT"; note: string }
): Promise<{ operator_name?: string; timestamp?: string }> {
  return request(`/runs/${encodeURIComponent(runId)}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export { API_BASE };
