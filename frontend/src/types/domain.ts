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

// Transmission attempt event (Phase 4.2B)
export interface TransmissionAttemptEvent {
  packet_id: string;
  attempt_number: number;
  start_elapsed_s: number;
  end_elapsed_s: number;
  status: 'success' | 'failure';
  packet_size_bits?: number;
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
  /** Phase 4.2B: additive event log — backwards-compatible default [] */
  attempt_events?: TransmissionAttemptEvent[];
}

// AI recommendation
export interface EvidenceItem {
  source: string;
  field: string;
  value: unknown;
  interpretation: string;
}

/** Phase 4.1: typed confidence semantics — assigned by the backend, not the provider. */
export type ConfidenceSemantics =
  | 'heuristic'               // deterministic risk-gap (LocalRuleBasedProvider)
  | 'uncalibrated_llm'        // LLM self-report, not a calibrated probability
  | 'unspecified_uncalibrated'; // fail-safe default — uncalibrated, provenance unknown

export interface AIRecommendation {
  recommended_plan_id: string;
  packet_actions: Array<{ packet_id: string; action: string; rank: number }>;
  /** Deterministic risk score from PlanEvaluator — always authoritative. */
  risk_score: number;
  /** Categorical risk level from PlanEvaluator — always authoritative. */
  risk_level: RiskLevel;
  /** Provider self-reported confidence. Advisory only — see confidence_semantics. */
  confidence: number;
  /**
   * How confidence was produced. Assigned by the backend — NOT by the provider.
   * 'heuristic'              — deterministic risk-gap (LocalRuleBasedProvider)
   * 'uncalibrated_llm'       — LLM self-report, not a calibrated probability
   * 'unspecified_uncalibrated' — fail-safe default, provenance unknown
   */
  confidence_semantics: ConfidenceSemantics;
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
  /** Backwards-compatible: equals actual_provider. */
  provider: string;
  /** The provider originally selected by configuration. */
  requested_provider: string;
  /** The provider that produced the final recommendation (may be 'local' on fallback). */
  actual_provider: string;
  /**
   * Phase 4.1: The actual provider that produced Stage-1 ranking.
   * null for legacy scenarios that skip Stage-1 prioritization.
   */
  prioritization_provider: string | null;
  /**
   * Phase 4.1: The actual provider that produced the final Stage-2 recommendation.
   * Equals actual_provider for backwards compatibility.
   */
  recommendation_provider: string;
  recommendation: AIRecommendation;
  /** Phase 2C/2D: structured AI prioritization result (v2 scenarios only). */
  prioritization: CandidatePrioritization | null;
  candidate_count: number | null;
  /** Set when Stage 1 (candidate prioritization) fell back to Local provider. */
  prioritization_fallback_reason: string | null;
  /** Set when Stage 2 (plan recommendation) fell back to Local provider. */
  recommendation_fallback_reason: string | null;
  /** Backwards-compatible alias for prioritization_fallback_reason. */
  prioritization_error: string | null;
  /**
   * The AI-prioritized transmission plan (v2/v3 path only).
   * null for legacy scenarios.
   */
  ai_plan: CandidatePlan | null;
  /**
   * Deterministic evaluation of ai_plan using the same PlanEvaluator as
   * the four deterministic baselines (v2/v3 path only). null for legacy scenarios.
   */
  ai_evaluation: EvaluationResult | null;
}

/** Approval provenance trace returned by POST /approve and POST /approve/custom. */
export interface ApprovalTrace {
  approval_id: string;
  timestamp_utc: string;
  scenario_id: string;
  plan_id: string;
  decision: string;
  /** Trust source: deterministic_generated | ai_generated | operator_custom | legacy_regenerated | client_intent */
  plan_source: string;
  operator_notes: string;
  /** True when all packet facts were rebound from the authoritative scenario. */
  authoritative_reconstruction: boolean;
  /** True when the plan matched a server-issued plan in the registry. */
  issued_plan_verified: boolean;
  packet_count: number;
  packet_order_sha256: string;
  canonical_plan_sha256: string;
}

export interface ApproveResponse {
  status: string;
  simulation_result: SimulationResult;
  /** Phase 4: approval provenance record. */
  approval_trace: ApprovalTrace;
  /** Phase 4: the authoritative CandidatePlan that was actually simulated. */
  executed_plan: CandidatePlan;
}

// What-if link context (Phase 3)
export interface WhatIfLinkContext {
  base_snr_db: number | null;
  base_ber: number | null;
  requested_snr_db: number | null;
  requested_ber: number | null;
  effective_snr_db: number | null;
  effective_eb_n0_db: number | null;
  derived_ber_before_override: number | null;
  effective_ber: number;
  snr_override_applied: boolean;
  ber_override_applied: boolean;
}

// What-if evaluation response (Feature 5 / Phase 3)
export interface WhatIfEvalResponse {
  /** Phase 3: full provenance of what the backend evaluated. */
  what_if_context: WhatIfLinkContext;
  /** Phase 3: the hypothetical LinkState used for evaluation. */
  hypothetical_link_state: LinkState;
  evaluations: EvaluationResult[];
  risk_weights: {
    w_deadline_miss: number;
    w_critical_deficit: number;
    w_window_pressure: number;
  };
}
