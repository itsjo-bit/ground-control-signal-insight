/**
 * transmissionPlayback.ts
 *
 * Production transmission playback mapper for GCSI Phase 5.1G.
 *
 * Converts authoritative SimulationResult.attempt_events into timed
 * visual playback events. Time is compressed proportionally.
 *
 * Phase 5.1F changes:
 *   - VisualAttemptSegment: structured interface with isRetry + authoritativeStatus (WORKSTREAM F/G/H)
 *   - buildVisualAttemptSegments: guarantees non-overlapping sequential timeline (WORKSTREAM G)
 *   - Segment ordering follows authoritative simulator order (WORKSTREAM H)
 *   - Every segment receives a minimum visual duration (WORKSTREAM G/H)
 *   - NO outcome='retry' — retry identity is isRetry, outcome is success|failure (WORKSTREAM F)
 *
 * Phase 5.1G changes (WORKSTREAM B):
 *   - transmission_min_duration_ms is now the TOTAL playback minimum, not per-segment.
 *   - buildVisualAttemptSegments now accepts targetTotalMs and distributes it across segments.
 *   - buildTransmissionPlayback computes bounded targetTotalMs before calling segment builder.
 *   - MAX_TOTAL_PLAYBACK_MS = 15,000 ms; PREFERRED_ATTEMPT_MS = 250 ms.
 *   - 33 attempts produce 7–8 s total, not ≥66 s.
 *
 * Phase 5.1G changes (WORKSTREAM A):
 *   - deriveEarlyExecutionPhase: pure helper for absolute-time early phase derivation.
 *   - Navigation / unmount cannot reset PLAN_UPLINK or CONTACT_WAIT.
 *
 * CRITICAL: This module must NOT modify any SimulationResult fields.
 * It only produces a visualization timeline from them.
 * Tests must import buildTransmissionPlayback and buildVisualAttemptSegments from here.
 */

import type { SimulationResult, TransmissionAttemptEvent } from '../types/domain';
import type { ExperiencePlaybackConfig } from '../types/experience';

// ── Visual attempt segment (Phase 5.1F) ──────────────────────────────────────

/**
 * A single visual representation of one authoritative attempt_event.
 *
 * Key invariants:
 *   visualDurationMs > 0
 *   visualEndMs === visualStartMs + visualDurationMs
 *   for all i: segments[i+1].visualStartMs >= segments[i].visualEndMs
 *   ordering follows authoritative attempt_events order (no reordering by outcome/ID)
 *
 * Retry identity and status are SEPARATE dimensions (WORKSTREAM F):
 *   isRetry = attemptNumber > 1
 *   authoritativeStatus = 'success' | 'failure'
 *   "retry success" → isRetry=true, authoritativeStatus='success'
 */
export interface VisualAttemptSegment {
  /** Authoritative packet ID from the simulator. */
  packetId: string;
  /** 1-based attempt number from the simulator. */
  attemptNumber: number;
  /** True when attemptNumber > 1 (retry identity, NOT outcome). */
  isRetry: boolean;
  /** Authoritative simulator outcome (NEVER 'retry' — use isRetry for retry identity). */
  authoritativeStatus: 'success' | 'failure';
  /** Wall-clock offset in ms from playback start. */
  visualStartMs: number;
  /** Wall-clock end in ms from playback start. */
  visualEndMs: number;
  /** Duration in ms. Always > 0. */
  visualDurationMs: number;
}

// ── Playback event types (legacy — kept for backwards compat) ─────────────────

export type PlaybackEventKind =
  | 'attempt_start'
  | 'attempt_complete_success'
  | 'attempt_complete_failure'
  | 'packet_deferred'
  | 'transmission_complete';

export interface PlaybackEvent {
  kind: PlaybackEventKind;
  packetId: string;
  attemptNumber: number;
  /** Wall-clock offset in ms from playback start (time-compressed). */
  visualOffsetMs: number;
  /** Duration of this attempt in visual ms. */
  visualDurationMs: number;
  /** Actual simulated elapsed_s at end of attempt. */
  simElapsedS: number;
  outcome: 'success' | 'failure' | 'deferred' | 'pending';
}

export interface TransmissionPlayback {
  events: PlaybackEvent[];
  /** Structured visual attempt segments — one per authoritative attempt_event. */
  visualSegments: VisualAttemptSegment[];
  totalVisualDurationMs: number;
  deliveredCount: number;
  failedCount: number;
  deferredCount: number;
  retransmissionTotal: number;
}

// ── Phase 5.1G: Bounded playback constants (WORKSTREAM B) ────────────────────

/**
 * Preferred visual budget per attempt segment when the total is unconstrained.
 * Chosen to be demo-friendly: ~250 ms/attempt feels responsive without being
 * invisible at high attempt counts.
 */
export const PREFERRED_ATTEMPT_MS = 250;

/**
 * Hard upper bound on total visual playback duration.
 * 100 attempts × 250 ms preferred = 25 s, but clamped to 15 s.
 */
export const MAX_TOTAL_PLAYBACK_MS = 15_000;

// ── Phase 5.1G: Absolute-time early phase derivation (WORKSTREAM A) ──────────

export type EarlyExecutionPhase =
  | 'plan_uplink'
  | 'contact_wait'
  | 'awaiting_result'
  | 'ready_for_transmission';

export interface DeriveEarlyPhaseInput {
  /** Current wall-clock time in ms (typically Date.now()). */
  nowMs: number;
  /** Wall-clock ms when operator authorized the execution. */
  authorizedAtMs: number;
  /** Presentation duration for the PLAN_UPLINK phase in ms. */
  uplinkDurationMs: number;
  /** Presentation duration for the CONTACT_ACQUISITION phase in ms. */
  contactAcquisitionMs: number;
  /** True when the authoritative ApproveResponse has been received. */
  resultAvailable: boolean;
}

/**
 * Pure production helper: derive the early execution phase from absolute time.
 *
 * This function is the single authoritative source for early phase calculation.
 * It must be called from production code (MissionControl / TransmissionSequencePanel)
 * and not recreated inside tests.
 *
 * Key invariant: navigation, tab-backgrounding, or component unmount cannot
 * change authorizedAtMs, so this function always returns the correct phase
 * regardless of when it is called.
 *
 * Phase boundary diagram (example: uplink=1500ms, contact=2000ms):
 *
 *   t=0          t=1500       t=3500
 *   ├────────────┼────────────┤───────────────
 *   PLAN_UPLINK  CONTACT_WAIT ←→ depends on resultAvailable
 *
 * @returns
 *   'plan_uplink'          elapsed < uplinkDurationMs
 *   'contact_wait'         uplinkDurationMs <= elapsed < uplinkDurationMs + contactAcquisitionMs
 *   'awaiting_result'      elapsed >= total early duration AND result not yet available
 *   'ready_for_transmission'  elapsed >= total early duration AND result available
 */
export function deriveEarlyExecutionPhase(input: DeriveEarlyPhaseInput): EarlyExecutionPhase {
  const { nowMs, authorizedAtMs, uplinkDurationMs, contactAcquisitionMs, resultAvailable } = input;
  const elapsed = nowMs - authorizedAtMs;

  if (elapsed < uplinkDurationMs) {
    return 'plan_uplink';
  }

  if (elapsed < uplinkDurationMs + contactAcquisitionMs) {
    return 'contact_wait';
  }

  // Early presentation durations have fully elapsed
  return resultAvailable ? 'ready_for_transmission' : 'awaiting_result';
}

/**
 * Compute how many ms remain until the next phase boundary, starting from nowMs.
 * Returns 0 if the boundary has already passed.
 *
 * Useful for setting a setTimeout that fires at the exact next phase boundary
 * rather than using a full-duration timer on every remount.
 */
export function msUntilNextPhaseBoundary(
  nowMs: number,
  authorizedAtMs: number,
  uplinkDurationMs: number,
  contactAcquisitionMs: number,
): number {
  const elapsed = nowMs - authorizedAtMs;

  if (elapsed < uplinkDurationMs) {
    return uplinkDurationMs - elapsed;
  }

  const totalEarlyMs = uplinkDurationMs + contactAcquisitionMs;
  if (elapsed < totalEarlyMs) {
    return totalEarlyMs - elapsed;
  }

  return 0; // all early phases have elapsed
}

// ── Non-overlapping visual segment builder (Phase 5.1G — WORKSTREAM B/G/H) ───

/**
 * Build a non-overlapping sequential visual attempt timeline from attempt_events.
 *
 * Phase 5.1G: targetTotalMs is the TOTAL visual budget for all segments.
 * Segments are distributed proportionally by their authoritative simulator
 * durations, subject to a minimum per-segment floor to ensure pulse visibility.
 *
 * Required properties after this function:
 *   segments[i+1].visualStartMs >= segments[i].visualEndMs  (no overlap)
 *   sum(segment.visualDurationMs) approximately equals targetTotalMs
 *   ordering follows authoritative attempt_events order      (no reordering)
 *
 * This is PRESENTATION-ONLY. Simulator timestamps are never mutated.
 *
 * @param attemptEvents  Authoritative attempt events in simulator order.
 * @param targetTotalMs  Total visual budget for all segments (bounded by caller).
 */
export function buildVisualAttemptSegments(
  attemptEvents: TransmissionAttemptEvent[],
  targetTotalMs: number,
): VisualAttemptSegment[] {
  if (attemptEvents.length === 0) return [];

  const N = attemptEvents.length;

  // Minimum per-segment floor: at least 50 ms so the pulse is never invisible.
  // This is a hard floor — not the playback minimum (which applies to the TOTAL).
  const MIN_SEGMENT_MS = 50;

  // Compute authoritative simulator durations for weighting
  const simDurations = attemptEvents.map(
    (ev) => Math.max(0, (ev.end_elapsed_s - ev.start_elapsed_s) * 1000),
  );

  const totalSimMs = simDurations.reduce((a, b) => a + b, 0);

  // Compute visual durations: proportional to sim duration, floor MIN_SEGMENT_MS,
  // scaled to sum to targetTotalMs.
  // Strategy:
  //   1. Each segment gets at least MIN_SEGMENT_MS.
  //   2. Remaining budget is distributed proportionally by sim duration weight.
  //   3. If totalSimMs == 0, use equal allocation.

  const floorTotal = N * MIN_SEGMENT_MS;
  const remainingBudget = Math.max(0, targetTotalMs - floorTotal);

  let visualDurations: number[];

  if (totalSimMs > 0) {
    // Proportional distribution of remaining budget
    visualDurations = simDurations.map((simMs) => {
      const weight = simMs / totalSimMs;
      return MIN_SEGMENT_MS + weight * remainingBudget;
    });
  } else {
    // Equal allocation when all sim durations are zero
    const perSegment = targetTotalMs / N;
    visualDurations = simDurations.map(() => Math.max(MIN_SEGMENT_MS, perSegment));
  }

  // Build segments with running cursor
  const segments: VisualAttemptSegment[] = [];
  let cursor = 0;

  for (let i = 0; i < N; i++) {
    const ev = attemptEvents[i];
    const visualDur = Math.round(visualDurations[i]); // round to whole ms
    const visualStart = cursor;
    const visualEnd = visualStart + Math.max(MIN_SEGMENT_MS, visualDur);

    segments.push({
      packetId: ev.packet_id,
      attemptNumber: ev.attempt_number,
      isRetry: ev.attempt_number > 1,
      authoritativeStatus: ev.status,
      visualStartMs: visualStart,
      visualEndMs: visualEnd,
      visualDurationMs: visualEnd - visualStart,
    });

    cursor = visualEnd; // advance cursor to enforce non-overlap
  }

  return segments;
}

// ── Builder ───────────────────────────────────────────────────────────────────

/**
 * Build a time-compressed visual playback timeline from actual attempt_events.
 *
 * Phase 5.1G (WORKSTREAM B): transmission_min_duration_ms is the minimum TOTAL
 * visual duration for the entire playback, NOT a per-segment minimum.
 *
 * Algorithm:
 *   N = attempt_events.length
 *   preferredTotal = N × PREFERRED_ATTEMPT_MS (250 ms)
 *   targetTotal = clamp(
 *     max(transmission_min_duration_ms, preferredTotal),
 *     transmission_min_duration_ms,
 *     MAX_TOTAL_PLAYBACK_MS (15,000 ms)
 *   )
 *   Then distribute targetTotal across all attempt segments proportionally.
 *
 * Example results:
 *    1 attempt  → ~2,000 ms  (min total respected)
 *   10 attempts → ~2,500 ms
 *   33 attempts → ~8,250 ms  (NOT 66,000 ms)
 *   50 attempts → ~12,500 ms
 *  100 attempts → ~15,000 ms (capped)
 *
 * @param result        Authoritative SimulationResult — not mutated.
 * @param playbackConfig  Presentation timing configuration from the sidecar.
 * @returns             TransmissionPlayback with visual timeline.
 */
export function buildTransmissionPlayback(
  result: SimulationResult,
  playbackConfig: Pick<ExperiencePlaybackConfig, 'transmission_min_duration_ms'>,
): TransmissionPlayback {
  const attemptEvents = result.attempt_events ?? [];
  const transmissionMinDurationMs = playbackConfig.transmission_min_duration_ms ?? 2000;

  // Phase 5.1G: Compute bounded target total for the whole playback (WORKSTREAM B)
  const N = attemptEvents.length;
  const preferredTotal = N * PREFERRED_ATTEMPT_MS;
  const targetTotalMs = N === 0
    ? Math.max(transmissionMinDurationMs, 500)
    : Math.min(
        MAX_TOTAL_PLAYBACK_MS,
        Math.max(transmissionMinDurationMs, preferredTotal),
      );

  // Build non-overlapping visual segments using bounded total (Phase 5.1G — WORKSTREAM B/G/H)
  const visualSegments = buildVisualAttemptSegments(attemptEvents, targetTotalMs);

  // Total visual duration covers all segments (or minimum if zero attempts)
  const totalVisualDurationMs = visualSegments.length > 0
    ? visualSegments[visualSegments.length - 1].visualEndMs
    : Math.max(transmissionMinDurationMs, 500);

  const deliveredSet = new Set(result.delivered_packets);
  const failedSet = new Set(result.failed_packets);
  const deferredSet = new Set(result.deferred_packets);

  const events: PlaybackEvent[] = [];

  // Map visual segments to legacy PlaybackEvent pairs (attempt_start + attempt_complete_*)
  for (const seg of visualSegments) {
    events.push({
      kind: 'attempt_start',
      packetId: seg.packetId,
      attemptNumber: seg.attemptNumber,
      visualOffsetMs: seg.visualStartMs,
      visualDurationMs: seg.visualDurationMs,
      simElapsedS: attemptEvents.find(
        (e) => e.packet_id === seg.packetId && e.attempt_number === seg.attemptNumber
      )?.start_elapsed_s ?? 0,
      outcome: 'pending',
    });

    events.push({
      kind: seg.authoritativeStatus === 'success'
        ? 'attempt_complete_success'
        : 'attempt_complete_failure',
      packetId: seg.packetId,
      attemptNumber: seg.attemptNumber,
      visualOffsetMs: seg.visualEndMs,
      visualDurationMs: 0,
      simElapsedS: attemptEvents.find(
        (e) => e.packet_id === seg.packetId && e.attempt_number === seg.attemptNumber
      )?.end_elapsed_s ?? 0,
      outcome: seg.authoritativeStatus,
    });
  }

  // Add deferred events at the end (after attempts are done)
  const deferredOffset = totalVisualDurationMs;
  for (const packetId of result.deferred_packets) {
    events.push({
      kind: 'packet_deferred',
      packetId,
      attemptNumber: 0,
      visualOffsetMs: deferredOffset,
      visualDurationMs: 0,
      simElapsedS: result.elapsed_time_s,
      outcome: 'deferred',
    });
  }

  // Transmission complete event
  events.push({
    kind: 'transmission_complete',
    packetId: '',
    attemptNumber: 0,
    visualOffsetMs: totalVisualDurationMs,
    visualDurationMs: 0,
    simElapsedS: result.elapsed_time_s,
    outcome: 'pending',
  });

  // Sort by visual offset (events already in order but sort for safety)
  events.sort((a, b) => a.visualOffsetMs - b.visualOffsetMs);

  // Count retransmissions: any attempt_number > 1
  const retransmissionTotal = attemptEvents.filter((ev) => ev.attempt_number > 1).length;

  return {
    events,
    visualSegments,
    totalVisualDurationMs,
    deliveredCount: deliveredSet.size,
    failedCount: failedSet.size,
    deferredCount: deferredSet.size,
    retransmissionTotal,
  };
}

// ── Attempt summary for a specific packet ────────────────────────────────────

export interface PacketAttemptSummary {
  packetId: string;
  attempts: TransmissionAttemptEvent[];
  finalStatus: 'delivered' | 'failed' | 'deferred';
  retransmissions: number;
}

/**
 * Group attempt events by packet and produce a per-packet summary.
 * Useful for rendering the transmission progress list.
 */
export function groupAttemptsByPacket(
  result: SimulationResult,
): PacketAttemptSummary[] {
  const attemptEvents = result.attempt_events ?? [];
  const deliveredSet = new Set(result.delivered_packets);
  const failedSet = new Set(result.failed_packets);

  const byPacket = new Map<string, TransmissionAttemptEvent[]>();
  for (const ev of attemptEvents) {
    if (!byPacket.has(ev.packet_id)) byPacket.set(ev.packet_id, []);
    byPacket.get(ev.packet_id)!.push(ev);
  }

  // Also include deferred packets (no attempts)
  for (const pid of result.deferred_packets) {
    if (!byPacket.has(pid)) byPacket.set(pid, []);
  }

  const summaries: PacketAttemptSummary[] = [];
  for (const [packetId, attempts] of byPacket.entries()) {
    const finalStatus = deliveredSet.has(packetId)
      ? 'delivered'
      : failedSet.has(packetId)
      ? 'failed'
      : 'deferred';
    summaries.push({
      packetId,
      attempts: attempts.sort((a, b) => a.attempt_number - b.attempt_number),
      finalStatus,
      retransmissions: Math.max(0, attempts.length - 1),
    });
  }

  return summaries;
}
