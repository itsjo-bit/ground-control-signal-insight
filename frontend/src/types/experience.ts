/**
 * Experience manifest TypeScript types — mirrors backend/app/models/experience.py.
 * Presentation metadata only. Never feed these into evaluators or simulators.
 */

export interface ExperienceDisplay {
  mission_name: string;
  scenario_name: string;
  spacecraft_name: string;
  ground_station_name: string;
  ground_station_description: string;
  disclaimer: string;
}

export interface ExperienceSchedule {
  next_contact_in_s: number;
  plan_uplink_margin_s: number;
  contact_duration_s: number;
  one_way_signal_s_note: string;
}

export interface SubsystemStatus {
  status: string;   // e.g. "degraded", "nominal", "stable"
  trend: string;    // e.g. "rising", "falling", "stable"
  label: string;    // e.g. "▲ DEGRADED"
  note: string;
}

export interface SubsystemStatusMap {
  thermal: SubsystemStatus;
  communications: SubsystemStatus;
  power: SubsystemStatus;
  propulsion: SubsystemStatus;
}

export interface HistoricalSnrPoint {
  offset_s: number;   // negative = past seconds from now
  snr_db: number;
}

export interface HistoricalThermalPoint {
  offset_s: number;
  temp_c: number;
}

export interface IngestBatchProduct {
  type: string;
  count: number;
}

export interface IngestBatch {
  offset_ms: number;
  products: IngestBatchProduct[];
}

export interface IngestReplay {
  total_products: number;
  total_bytes: number;
  batches: IngestBatch[];
}

export interface ExperiencePlaybackConfig {
  ingest_duration_ms: number;
  uplink_duration_ms: number;
  contact_acquisition_ms: number;
  transmission_min_duration_ms: number;
  propagation_duration_ms: number;
  ground_receive_interval_ms: number;
}

export interface ExperienceManifest {
  schema_version: string;
  scenario_id: string;
  display: ExperienceDisplay;
  schedule: ExperienceSchedule;
  subsystem_status: SubsystemStatusMap;
  snr_history: HistoricalSnrPoint[];
  thermal_history: HistoricalThermalPoint[];
  ingest_replay: IngestReplay;
  ground_information_objectives: Record<string, string[]>;
  curated_candidate_ids: string[];
  playback: ExperiencePlaybackConfig;
}

export interface ExperienceResponse {
  available: boolean;
  manifest: ExperienceManifest | null;
}
