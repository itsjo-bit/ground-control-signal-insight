/**
 * missionExperienceReducer.ts
 *
 * Mission experience state machine for GCSI Phase 4.2F.
 * Manages the full end-to-end mission journey from brief through
 * ground reception without XState or large external dependencies.
 *
 * PRESENTATION STATE ONLY — no scientific formulas here.
 */

import { useReducer, useCallback } from 'react';
import type { ExperienceManifest } from '../types/experience';
import type { ApproveResponse, CandidatePlan, EvaluationResult } from '../types/domain';

// ── Phase definitions ─────────────────────────────────────────────────────────

export type MissionExperiencePhase =
  | 'mission_brief'        // Initial state — ASTERIA hero, ingest replay
  | 'planning'             // Operator choosing Manual or AI mode
  | 'ai_analyzing'         // AI triage in progress
  | 'plan_review'          // Human decision: approve / modify / reject
  | 'plan_uplink'          // Earth → spacecraft command uplink (visualization)
  | 'contact_wait'         // Acquiring high-rate contact
  | 'transmitting'         // Actual downlink transmission in progress
  | 'signal_in_transit'    // Signal propagating to Earth
  | 'ground_receiving'     // Ground station receiving data
  | 'contact_complete'     // Full mission cycle complete
  | 'rejected';            // Operator rejected AI recommendation

// ── Ingest replay state ───────────────────────────────────────────────────────

export type IngestReplayState =
  | 'idle'
  | 'running'
  | 'complete'
  | 'skipped';

// ── Session event log ─────────────────────────────────────────────────────────

export interface SessionEvent {
  id: string;
  timestamp: number;  // Date.now()
  type:
    | 'asteria_initialized'
    | 'ingest_replay_completed'
    | 'ingest_replay_skipped'
    | 'manual_mode_selected'
    | 'ai_mode_selected'
    | 'manual_plan_assessed'
    | 'ai_analysis_requested'
    | 'ai_analysis_completed'
    | 'recommendation_approved'
    | 'recommendation_modified'
    | 'recommendation_rejected'
    | 'plan_uplink_started'
    | 'contact_acquired'
    | 'approval_executed'
    | 'transmission_attempt'
    | 'retransmission'
    | 'transmission_completed'
    | 'signal_in_transit'
    | 'ground_reception_completed'
    | 'scenario_reset';
  detail?: string;
}

// ── Playback state ────────────────────────────────────────────────────────────

export interface PlaybackCursor {
  attemptIndex: number;      // current attempt_events index being visualized
  elapsedVisualizationMs: number;
  compressionFactor: number;
}

export interface GroundReceptionCursor {
  deliveredSoFar: string[];  // product IDs received so far
  complete: boolean;
}

// ── Manual assessment result ──────────────────────────────────────────────────

export interface ManualAssessmentResult {
  plan: CandidatePlan;
  evaluation: EvaluationResult;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  mission_outcome: any | null;
  capacity_summary: {
    available_capacity_bits: number;
    selected_bits: number;
    selected_count: number;
    exceeds_capacity: boolean;
    window_s: number;
  };
  orderFingerprint: string;  // join of ordered IDs at assessment time
}

// ── State type ────────────────────────────────────────────────────────────────

export interface MissionExperienceState {
  phase: MissionExperiencePhase;
  planningMode: 'unselected' | 'manual' | 'ai';
  introReplayState: IngestReplayState;
  introReplayProductCount: number;  // products loaded so far during replay
  pendingExecution: CandidatePlan | null;  // plan to execute after uplink
  playbackCursor: PlaybackCursor | null;
  groundReceptionCursor: GroundReceptionCursor | null;
  rejectedRecommendation: boolean;
  sessionEvents: SessionEvent[];
  // Manual planning
  manualAssessment: ManualAssessmentResult | null;
  manualAssessmentLoading: boolean;
  manualAssessmentError: string | null;
  manualAssessmentStale: boolean;
  manualAssessmentOrderFingerprint: string | null;
  // Experience manifest
  experienceManifest: ExperienceManifest | null;
  experienceAvailable: boolean;
  experienceLoading: boolean;
}

const _initialState: MissionExperienceState = {
  phase: 'mission_brief',
  planningMode: 'unselected',
  introReplayState: 'idle',
  introReplayProductCount: 0,
  pendingExecution: null,
  playbackCursor: null,
  groundReceptionCursor: null,
  rejectedRecommendation: false,
  sessionEvents: [],
  manualAssessment: null,
  manualAssessmentLoading: false,
  manualAssessmentError: null,
  manualAssessmentStale: false,
  manualAssessmentOrderFingerprint: null,
  experienceManifest: null,
  experienceAvailable: false,
  experienceLoading: false,
};

// ── Actions ───────────────────────────────────────────────────────────────────

export type MissionExperienceAction =
  | { type: 'EXPERIENCE_LOADING' }
  | { type: 'EXPERIENCE_LOADED'; manifest: ExperienceManifest | null; available: boolean }
  | { type: 'INGEST_REPLAY_START' }
  | { type: 'INGEST_REPLAY_TICK'; productCount: number }
  | { type: 'INGEST_REPLAY_COMPLETE' }
  | { type: 'INGEST_REPLAY_SKIP' }
  | { type: 'SET_PLANNING_MODE'; mode: 'manual' | 'ai' }
  | { type: 'MANUAL_ASSESS_START'; orderFingerprint: string }
  | { type: 'MANUAL_ASSESS_SUCCESS'; result: ManualAssessmentResult }
  | { type: 'MANUAL_ASSESS_ERROR'; error: string }
  | { type: 'MANUAL_SELECTION_CHANGED'; orderFingerprint: string }
  | { type: 'MANUAL_TRANSMIT'; plan: CandidatePlan }
  | { type: 'AI_ANALYSIS_REQUESTED' }
  | { type: 'AI_ANALYSIS_COMPLETED' }
  | { type: 'PLAN_REVIEW'; plan: CandidatePlan }
  | { type: 'APPROVE'; plan: CandidatePlan }
  | { type: 'MODIFY' }
  | { type: 'REJECT' }
  | { type: 'PLAN_UPLINK_COMPLETE' }
  | { type: 'CONTACT_ACQUIRED' }
  | { type: 'APPROVAL_EXECUTED'; result: ApproveResponse }
  | { type: 'TRANSMISSION_ATTEMPT'; packetId: string; attemptNumber: number }
  | { type: 'RETRANSMISSION'; packetId: string }
  | { type: 'TRANSMISSION_COMPLETE' }
  | { type: 'SIGNAL_IN_TRANSIT' }
  | { type: 'GROUND_RECEPTION_TICK'; deliveredIds: string[] }
  | { type: 'GROUND_RECEPTION_COMPLETE' }
  | { type: 'RESET' };

// ── Event log helper ──────────────────────────────────────────────────────────

let _eventCounter = 0;

function mkEvent(
  type: SessionEvent['type'],
  detail?: string,
): SessionEvent {
  return {
    id: `ev-${++_eventCounter}`,
    timestamp: Date.now(),
    type,
    detail,
  };
}

function appendEvent(
  events: SessionEvent[],
  type: SessionEvent['type'],
  detail?: string,
): SessionEvent[] {
  return [...events, mkEvent(type, detail)];
}

// ── Reducer ───────────────────────────────────────────────────────────────────

export function missionExperienceReducer(
  state: MissionExperienceState,
  action: MissionExperienceAction,
): MissionExperienceState {
  switch (action.type) {
    case 'EXPERIENCE_LOADING':
      return { ...state, experienceLoading: true };

    case 'EXPERIENCE_LOADED':
      return {
        ...state,
        experienceLoading: false,
        experienceAvailable: action.available,
        experienceManifest: action.manifest,
      };

    case 'INGEST_REPLAY_START':
      return {
        ...state,
        introReplayState: 'running',
        introReplayProductCount: 0,
      };

    case 'INGEST_REPLAY_TICK':
      return {
        ...state,
        introReplayProductCount: action.productCount,
      };

    case 'INGEST_REPLAY_COMPLETE':
      return {
        ...state,
        introReplayState: 'complete',
        introReplayProductCount:
          state.experienceManifest?.ingest_replay.total_products ??
          state.introReplayProductCount,
        sessionEvents: appendEvent(state.sessionEvents, 'ingest_replay_completed'),
      };

    case 'INGEST_REPLAY_SKIP':
      return {
        ...state,
        introReplayState: 'skipped',
        introReplayProductCount:
          state.experienceManifest?.ingest_replay.total_products ?? 0,
        sessionEvents: appendEvent(state.sessionEvents, 'ingest_replay_skipped'),
      };

    case 'SET_PLANNING_MODE':
      return {
        ...state,
        phase: 'planning',
        planningMode: action.mode,
        sessionEvents: appendEvent(
          state.sessionEvents,
          action.mode === 'manual' ? 'manual_mode_selected' : 'ai_mode_selected',
        ),
      };

    case 'MANUAL_ASSESS_START':
      return {
        ...state,
        manualAssessmentLoading: true,
        manualAssessmentError: null,
        manualAssessmentStale: false,
        manualAssessmentOrderFingerprint: action.orderFingerprint,
      };

    case 'MANUAL_ASSESS_SUCCESS':
      return {
        ...state,
        manualAssessmentLoading: false,
        manualAssessment: action.result,
        manualAssessmentStale: false,
        sessionEvents: appendEvent(state.sessionEvents, 'manual_plan_assessed'),
      };

    case 'MANUAL_ASSESS_ERROR':
      return {
        ...state,
        manualAssessmentLoading: false,
        manualAssessmentError: action.error,
      };

    case 'MANUAL_SELECTION_CHANGED':
      // Mark previous assessment as stale if it exists and fingerprint differs
      if (
        state.manualAssessment !== null &&
        state.manualAssessmentOrderFingerprint !== action.orderFingerprint
      ) {
        return { ...state, manualAssessmentStale: true, manualAssessmentOrderFingerprint: action.orderFingerprint };
      }
      return { ...state, manualAssessmentOrderFingerprint: action.orderFingerprint };

    case 'MANUAL_TRANSMIT':
      return {
        ...state,
        phase: 'plan_uplink',
        pendingExecution: action.plan,
        sessionEvents: appendEvent(state.sessionEvents, 'plan_uplink_started', 'manual'),
      };

    case 'AI_ANALYSIS_REQUESTED':
      return {
        ...state,
        phase: 'ai_analyzing',
        sessionEvents: appendEvent(state.sessionEvents, 'ai_analysis_requested'),
      };

    case 'AI_ANALYSIS_COMPLETED':
      return {
        ...state,
        phase: 'plan_review',
        sessionEvents: appendEvent(state.sessionEvents, 'ai_analysis_completed'),
      };

    case 'PLAN_REVIEW':
      return {
        ...state,
        phase: 'plan_review',
        pendingExecution: action.plan,
      };

    case 'APPROVE':
      return {
        ...state,
        phase: 'plan_uplink',
        pendingExecution: action.plan,
        sessionEvents: appendEvent(state.sessionEvents, 'recommendation_approved'),
      };

    case 'MODIFY':
      return {
        ...state,
        phase: 'planning',
        planningMode: 'manual',
        sessionEvents: appendEvent(state.sessionEvents, 'recommendation_modified'),
      };

    case 'REJECT':
      return {
        ...state,
        phase: 'rejected',
        pendingExecution: null,
        rejectedRecommendation: true,
        sessionEvents: appendEvent(state.sessionEvents, 'recommendation_rejected'),
      };

    case 'PLAN_UPLINK_COMPLETE':
      return {
        ...state,
        phase: 'contact_wait',
        sessionEvents: appendEvent(state.sessionEvents, 'plan_uplink_started'),
      };

    case 'CONTACT_ACQUIRED':
      return {
        ...state,
        phase: 'transmitting',
        sessionEvents: appendEvent(state.sessionEvents, 'contact_acquired'),
      };

    case 'APPROVAL_EXECUTED':
      return {
        ...state,
        sessionEvents: appendEvent(state.sessionEvents, 'approval_executed'),
      };

    case 'TRANSMISSION_ATTEMPT':
      return {
        ...state,
        sessionEvents: appendEvent(
          state.sessionEvents,
          action.attemptNumber > 1 ? 'retransmission' : 'transmission_attempt',
          `${action.packetId} attempt #${action.attemptNumber}`,
        ),
      };

    case 'RETRANSMISSION':
      return {
        ...state,
        sessionEvents: appendEvent(state.sessionEvents, 'retransmission', action.packetId),
      };

    case 'TRANSMISSION_COMPLETE':
      return {
        ...state,
        phase: 'signal_in_transit',
        sessionEvents: appendEvent(state.sessionEvents, 'transmission_completed'),
      };

    case 'SIGNAL_IN_TRANSIT':
      return {
        ...state,
        phase: 'signal_in_transit',
        sessionEvents: appendEvent(state.sessionEvents, 'signal_in_transit'),
      };

    case 'GROUND_RECEPTION_TICK':
      return {
        ...state,
        phase: 'ground_receiving',
        groundReceptionCursor: {
          deliveredSoFar: action.deliveredIds,
          complete: false,
        },
      };

    case 'GROUND_RECEPTION_COMPLETE':
      return {
        ...state,
        phase: 'contact_complete',
        groundReceptionCursor: state.groundReceptionCursor
          ? { ...state.groundReceptionCursor, complete: true }
          : null,
        sessionEvents: appendEvent(state.sessionEvents, 'ground_reception_completed'),
      };

    case 'RESET':
      return {
        ..._initialState,
        // Preserve experience data on reset — it will be refetched by the caller
        experienceManifest: state.experienceManifest,
        experienceAvailable: state.experienceAvailable,
        sessionEvents: [mkEvent('scenario_reset')],
      };

    default:
      return state;
  }
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useMissionExperience() {
  const [state, dispatch] = useReducer(missionExperienceReducer, _initialState);
  const reset = useCallback(() => dispatch({ type: 'RESET' }), []);
  return { state, dispatch, reset };
}
