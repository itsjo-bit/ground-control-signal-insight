/**
 * transmissionPlayback.ts
 *
 * Production transmission playback mapper for GCSI Phase 5.1F.
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

// ── Non-overlapping visual segment builder (Phase 5.1F — WORKSTREAM G/H) ──────

/**
 * Build a non-overlapping sequential visual attempt timeline from attempt_events.
 *
 * Required properties after this function:
 *   segments[i+1].visualStartMs >= segments[i].visualEndMs  (no overlap)
 *   every segment.visualDurationMs >= minDurationMs          (full pulse travel time)
 *   ordering follows authoritative attempt_events order      (no reordering)
 *
 * This is PRESENTATION-ONLY. Simulator timestamps are never mutated.
 *
 * @param attemptEvents  Authoritative attempt events in simulator order.
 * @param minDurationMs  Minimum visual duration per segment (for pulse to travel 0→1).
 */
export function buildVisualAttemptSegments(
  attemptEvents: TransmissionAttemptEvent[],
  minDurationMs: number,
): VisualAttemptSegment[] {
  if (attemptEvents.length === 0) return [];

  const segments: VisualAttemptSegment[] = [];
  let cursor = 0; // running visual timeline cursor in ms

  for (const ev of attemptEvents) {
    // Compute simulator duration
    const simDurationMs = (ev.end_elapsed_s - ev.start_elapsed_s) * 1000;

    // Visual duration: at least minDurationMs so pulse can travel 0→1
    const visualDur = Math.max(minDurationMs, simDurationMs);

    // Start at cursor (guarantees non-overlap: cursor >= previous end)
    const visualStart = cursor;
    const visualEnd = visualStart + visualDur;

    segments.push({
      packetId: ev.packet_id,
      attemptNumber: ev.attempt_number,
      isRetry: ev.attempt_number > 1,
      authoritativeStatus: ev.status,
      visualStartMs: visualStart,
      visualEndMs: visualEnd,
      visualDurationMs: visualDur,
    });

    cursor = visualEnd; // advance cursor to enforce non-overlap
  }

  return segments;
}

// ── Builder ───────────────────────────────────────────────────────────────────

/**
 * Build a time-compressed visual playback timeline from actual attempt_events.
 *
 * Phase 5.1F: uses buildVisualAttemptSegments to guarantee non-overlapping sequential
 * visual segments, one per authoritative attempt_event.
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
  const minDurationMs = playbackConfig.transmission_min_duration_ms ?? 2000;

  // Build non-overlapping visual segments (Phase 5.1F — WORKSTREAM G/H)
  const visualSegments = buildVisualAttemptSegments(attemptEvents, minDurationMs);

  // Total visual duration covers all segments (or minDurationMs if zero attempts)
  const totalVisualDurationMs = visualSegments.length > 0
    ? visualSegments[visualSegments.length - 1].visualEndMs
    : Math.max(minDurationMs, 500);

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
