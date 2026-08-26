/**
 * Typed API client — one function per backend endpoint.
 * All requests go through the Vite proxy `/api → http://localhost:8000`.
 */

import type {
  ApproveResponse,
  CandidatePlan,
  DataProductsResponse,
  EvaluationResult,
  RecommendResponse,
  ScenariosResponse,
  SimulationResult,
  StateResponse,
  WhatIfEvalResponse,
} from '../types/domain';

// Re-exported for convenience
export type AssessManualPlanResponse = {
  plan: CandidatePlan;
  evaluation: EvaluationResult;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  mission_outcome: any | null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  capacity_summary: any;
};

const BASE = '/api';

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

// State — returns full StateResponse including Phase 2E-C1 communication budget fields
export async function getState(): Promise<StateResponse> {
  return fetchJson(`${BASE}/state`);
}

// Reset scenario — reloads original scenario from disk, discarding simulation mutations
export async function resetScenario(): Promise<{ status: string; scenario_path: string; comm_window_remaining_s: number }> {
  return fetchJson(`${BASE}/state/reset`, { method: 'POST' });
}

// Queue
export async function getQueue(): Promise<CandidatePlan> {
  return fetchJson(`${BASE}/queue`);
}

// Plans
export async function generatePlans(): Promise<CandidatePlan[]> {
  return fetchJson(`${BASE}/plans/generate`, { method: 'POST' });
}

export async function evaluatePlan(plan: CandidatePlan): Promise<EvaluationResult> {
  return fetchJson(`${BASE}/plans/evaluate`, {
    method: 'POST',
    body: JSON.stringify(plan),
  });
}

// What-if evaluation (Feature 5) — pure read-only, never mutates state
export async function whatIfEvaluate(
  snr_db?: number,
  ber?: number,
): Promise<WhatIfEvalResponse> {
  return fetchJson(`${BASE}/plans/what-if`, {
    method: 'POST',
    body: JSON.stringify({ snr_db: snr_db ?? null, ber: ber ?? null }),
  });
}

// Simulate
export async function simulate(
  plan_id: string,
  seed?: number,
): Promise<SimulationResult> {
  return fetchJson(`${BASE}/simulate`, {
    method: 'POST',
    body: JSON.stringify({ plan_id, seed }),
  });
}

export async function simulateWhatIf(
  plan: CandidatePlan,
  seed?: number,
): Promise<SimulationResult> {
  return fetchJson(`${BASE}/simulate/what-if`, {
    method: 'POST',
    body: JSON.stringify({ plan, seed }),
  });
}

// Approve — Phase 2E-D3 (P0-1): send the full recommended CandidatePlan so the
// backend uses it directly without regenerating from _effective_packets().
// plan_id is still required for backend backward compat; plan is authoritative.
export async function approvePlan(
  plan_id: string,
  plan: CandidatePlan,
  operator_notes: string = '',
): Promise<ApproveResponse> {
  return fetchJson(`${BASE}/approve`, {
    method: 'POST',
    body: JSON.stringify({ plan_id, plan, operator_notes }),
  });
}

// Approve a custom (operator-reordered) plan (Feature 3)
export async function approveCustomPlan(
  plan: CandidatePlan,
  operator_notes: string = '',
): Promise<ApproveResponse> {
  return fetchJson(`${BASE}/approve/custom`, {
    method: 'POST',
    body: JSON.stringify({ plan, operator_notes }),
  });
}

// Agent
export async function getRecommendation(): Promise<RecommendResponse> {
  return fetchJson(`${BASE}/agent/recommend`, { method: 'POST' });
}

// Raw data products — full unfiltered list from active scenario
export async function getDataProducts(): Promise<DataProductsResponse> {
  return fetchJson<DataProductsResponse>(`${BASE}/data-products`);
}

// POST /plans/assess — non-mutating manual plan assessment
export async function assessManualPlan(
  productIds: string[],
): Promise<AssessManualPlanResponse> {
  return fetchJson(`${BASE}/plans/assess`, {
    method: 'POST',
    body: JSON.stringify({ product_ids: productIds }),
  });
}

// GET /experience — experience manifest for the active scenario
export async function getExperience(): Promise<{ available: boolean; manifest: unknown | null }> {
  return fetchJson(`${BASE}/experience`);
}

// Scenario management
export async function listScenarios(): Promise<ScenariosResponse> {
  return fetchJson<ScenariosResponse>(`${BASE}/scenarios`);
}

export async function switchScenario(filename: string): Promise<{
  status: string;
  scenario_id: string;
  scenario_path: string;
  data_products_count: number;
  anomalies_count: number;
}> {
  return fetchJson(`${BASE}/scenarios/switch`, {
    method: 'POST',
    body: JSON.stringify({ filename }),
  });
}
