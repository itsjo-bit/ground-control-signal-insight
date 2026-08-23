/**
 * Typed API client — one function per backend endpoint.
 * All requests go through the Vite proxy `/api → http://localhost:8000`.
 */

import type {
  ApproveResponse,
  CandidatePlan,
  EvaluationResult,
  LinkState,
  MissionState,
  RecommendResponse,
  SimulationResult,
  WhatIfEvalResponse,
} from '../types/domain';

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

// State
export async function getState(): Promise<{ link_state: LinkState; mission_state: MissionState }> {
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

// Approve
export async function approvePlan(
  plan_id: string,
  operator_notes: string = '',
): Promise<ApproveResponse> {
  return fetchJson(`${BASE}/approve`, {
    method: 'POST',
    body: JSON.stringify({ plan_id, operator_notes }),
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
