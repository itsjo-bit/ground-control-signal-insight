/**
 * transmissionPlayback.ts
 *
 * Production transmission playback mapper for GCSI Phase 4.2F.
 *
 * Converts authoritative SimulationResult.attempt_events into timed
 * visual playback events. Time is compressed proportionally.
 *
 * CRITICAL: This module must NOT modify any SimulationResult fields.
 * It only produces a visualization timeline from them.
 * Tests must import buildTransmissionPlayback from here.
 */

import type { SimulationResult, TransmissionAttemptEvent } from '../types/domain';
import type { ExperiencePlaybackConfig } from '../types/experience';

// ── Playback event types ──────────────────────────────────────────────────────

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
  totalVisualDurationMs: number;
  deliveredCount: number;
  failedCount: number;
  deferredCount: number;
  retransmissionTotal: number;
}

// ── Builder ───────────────────────────────────────────────────────────────────

/**
 * Build a time-compressed visual playback timeline from actual attempt_events.
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

  // Compute the total simulated time span of attempts
  let maxSimElapsed = 0;
  for (const ev of attemptEvents) {
    if (ev.end_elapsed_s > maxSimElapsed) maxSimElapsed = ev.end_elapsed_s;
  }

  // Target visual duration — at least minDurationMs, scale up proportionally
  const visualDurationMs = Math.max(
    minDurationMs,
    Math.min(maxSimElapsed * 20, 15_000), // cap at 15 s of visual playback
  );

  const compressionFactor = maxSimElapsed > 0 ? visualDurationMs / (maxSimElapsed * 1000) : 1;

  const deliveredSet = new Set(result.delivered_packets);
  const failedSet = new Set(result.failed_packets);
  const deferredSet = new Set(result.deferred_packets);

  const events: PlaybackEvent[] = [];

  // Map attempt events to visual playback
  for (const ev of attemptEvents) {
    const visualStart = ev.start_elapsed_s * 1000 * compressionFactor;
    const simDurationMs = (ev.end_elapsed_s - ev.start_elapsed_s) * 1000;
    const visualDur = Math.max(200, simDurationMs * compressionFactor);

    events.push({
      kind: 'attempt_start',
      packetId: ev.packet_id,
      attemptNumber: ev.attempt_number,
      visualOffsetMs: visualStart,
      visualDurationMs: visualDur,
      simElapsedS: ev.start_elapsed_s,
      outcome: 'pending',
    });

    events.push({
      kind: ev.status === 'success' ? 'attempt_complete_success' : 'attempt_complete_failure',
      packetId: ev.packet_id,
      attemptNumber: ev.attempt_number,
      visualOffsetMs: visualStart + visualDur,
      visualDurationMs: 0,
      simElapsedS: ev.end_elapsed_s,
      outcome: ev.status === 'success' ? 'success' : 'failure',
    });
  }

  // Add deferred events at the end (after attempts are done)
  const deferredOffset = maxSimElapsed * 1000 * compressionFactor;
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
    visualOffsetMs: visualDurationMs,
    visualDurationMs: 0,
    simElapsedS: result.elapsed_time_s,
    outcome: 'pending',
  });

  // Sort by visual offset
  events.sort((a, b) => a.visualOffsetMs - b.visualOffsetMs);

  // Count retransmissions: any attempt_number > 1
  const retransmissionTotal = attemptEvents.filter((ev) => ev.attempt_number > 1).length;

  return {
    events,
    totalVisualDurationMs: visualDurationMs,
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
