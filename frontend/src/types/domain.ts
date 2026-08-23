/**
 * Domain types mirroring the GCSI backend Pydantic models.
 * Maintained manually — keep in sync with backend/app/models/.
 */

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

export interface RecommendResponse {
  provider: string;
  recommendation: AIRecommendation;
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
