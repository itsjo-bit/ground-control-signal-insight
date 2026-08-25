/**
 * Domain types mirroring the GCSI backend Pydantic models.
 * Maintained manually — keep in sync with backend/app/models/.
 */

// ── V3.4: Decision mode ───────────────────────────────────────────────────────
/** The operator's chosen decision workflow. */
export type DecisionMode = 'unselected' | 'manual' | 'ai';

/** AI Copilot lifecycle status. */
export type AiLifecycle = 'standby' | 'analyzing' | 'ready' | 'error' | 'stale';

// ── Raw data product (mirrors DataProduct Pydantic model) ─────────────────────
export interface DataProduct {
  product_id: string;
  product_type: string;
  description: string;
  subsystem: string;
  size_bits: number;
  criticality: number;
  mission_relevance: number;
  scientific_value: number;
  deadline_s: number;
  age_s: number;
  anomaly_id: string | null;
  experiment_id: string | null;
  related_ids: string[];
  delivery_requirement: string;
  retry_cost: number;
}

// ── Data products API response ────────────────────────────────────────────────
export interface DataProductsResponse {
  scenario_id: string;
  data_products: DataProduct[];
  total: number;
  has_data_products: boolean;
}

// ── Scenario management ───────────────────────────────────────────────────────
export interface ScenarioInfo {
  filename: string;
  scenario_id: string | null;
  has_data_products: boolean;
  has_anomalies: boolean;
  data_products_count: number;
  anomalies_count: number;
  is_active: boolean;
  label: string;
}

export interface ScenariosResponse {
  scenarios: ScenarioInfo[];
  active_scenario_path: string | null;
}

// Phase 2E-C1/C2/C3-C: authoritative communication budget and geometry from GET /state
export interface StateResponse {
  link_state: LinkState;
  mission_state: MissionState;
  data_products_count: number;
  anomalies_count: number;
  anomalies: AnomalyEvent[];
  /** Maximum bits transmittable in the remaining window at current goodput. */
  available_capacity_bits: number;
  /** Total size of all queued data products (or legacy packets). */
  queued_data_bits: number;
  // Phase 2E-C3-C: spacecraft communication geometry (null for legacy scenarios)
  /** Spacecraft-to-Earth distance in km. null when scenario does not provide it. */
  distance_km: number | null;
  /** One-way signal propagation delay in seconds (distance_km × 1000 / c). null when distance_km is null. */
  propagation_delay_s: number | null;
  /** Round-trip propagation time in seconds (2 × propagation_delay_s). null when distance_km is null. */
  round_trip_time_s: number | null;
}

export interface AnomalyEvent {
  anomaly_id: string;
  subsystem: string;
  severity: number;
  description: string;
  detected_at_s: number;
}

// Enums
export type RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

// Link state
export interface LinkState {
  timestamp: string;
  snr_db: number;
  eb_n0_db: number;
  ber: number;
  rssi_dbm: number;
  nominal_data_rate_bps: number;
  link_goodput_bps: number;
  latency_s: number;
  link_stability: number;
  remaining_window_s: number;
}

// Mission state
export interface MissionState {
  mission_id: string;
  mission_phase: string;
  current_event: string;
  event_time_remaining_s: number;
  comm_window_remaining_s: number;
  risk_score: number;
  risk_level: RiskLevel;
}

// Packet
export interface Packet {
  packet_id: string;
  packet_type: string;
  size_bits: number;
  criticality: number;
  mission_relevance: number;
  deadline_s: number;
  retry_cost: number;
  delivery_requirement: string;
}

// Candidate plan
export interface CandidatePlan {
  plan_id: string;
  strategy: string;
  packets: Packet[];
  generated_by: string;
  metadata: Record<string, unknown>;
}

// Evaluation result — includes risk breakdown components (Feature 2)
export interface EvaluationResult {
  plan_id: string;
  mission_value: number;
  critical_packets_delivered: number;
  total_critical_packets: number;
  deadline_misses: number;
  avg_packet_delay_s: number;
  bandwidth_utilization: number;
  retransmission_overhead: number;
  risk_score: number;
  risk_level: RiskLevel;
  deferred_packets: string[];
  // Risk breakdown components
  deadline_miss_rate: number;
  critical_deficit: number;
  window_pressure: number;
}

// Simulation result
export interface SimulationResult {
  plan_id: string;
  delivered_packets: string[];
  deferred_packets: string[];
  failed_packets: string[];
  elapsed_time_s: number;
  retransmission_counts: Record<string, number>;
  link_state: LinkState;
  mission_state: MissionState;
}

// AI recommendation
export interface EvidenceItem {
  source: string;
  field: string;
  value: unknown;
  interpretation: string;
}

export interface AIRecommendation {
  recommended_plan_id: string;
  packet_actions: Array<{ packet_id: string; action: string; rank: number }>;
  risk_score: number;
  risk_level: RiskLevel;
  confidence: number;
  reasoning: string;
  evidence: EvidenceItem[];
  alternative_plan_id: string | null;
}

// Phase 2D: AI prioritization transparency types
export interface RankedProduct {
  product_id: string;
  priority: number;
  reason: string;
  /** Phase 2E-D3: human-readable product description from DataProduct.description.
   *  Absent or empty string when not available (backwards compatible with existing
   *  serialized responses that do not include this field). */
  description?: string;
  factors: string[];
  anomaly_ids: string[];
  subsystem: string;
  confidence: number | null;
}

export interface CandidatePrioritization {
  ranked_products: RankedProduct[];
  overall_reasoning: string;
  confidence: number;
  decision_factors: string[];
  candidate_count: number | null;
}

export interface RecommendResponse {
  provider: string;
  recommendation: AIRecommendation;
  /** Phase 2C/2D: structured AI prioritization result (v2 scenarios only). */
  prioritization: CandidatePrioritization | null;
  candidate_count: number | null;
  /** Phase 2D: error message if AI prioritization failed (deterministic fallback active). */
  prioritization_error: string | null;
}

export interface ApproveResponse {
  status: string;
  simulation_result: SimulationResult;
}

// What-if evaluation response (Feature 5)
export interface WhatIfEvalResponse {
  evaluations: EvaluationResult[];
  risk_weights: {
    w_deadline_miss: number;
    w_critical_deficit: number;
    w_window_pressure: number;
  };
}
