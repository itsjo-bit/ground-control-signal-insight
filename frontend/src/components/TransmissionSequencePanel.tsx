/**
 * TransmissionSequencePanel — Phase 5.1E
 *
 * EXECUTION VS PRESENTATION SEPARATION (Phase 5.1E key change):
 *   - The backend approval is dispatched IMMEDIATELY at authorization time in MissionControl.
 *   - This panel OBSERVES the execution — it does NOT own the backend dispatch.
 *   - onExecuteApproval(executionId) returns the already-existing Promise.
 *   - The CONTACT_WAIT stage now just awaits an already-dispatched Promise.
 *   - Unmounting this panel CANNOT cancel or delay the backend execution.
 *
 * Transmission choreography sequence:
 *   1. PLAN_UPLINK     — Earth → spacecraft command uplink (visualization only)
 *   2. CONTACT_WAIT    — Acquiring high-rate contact (awaits already-dispatched approval Promise)
 *   3. TRANSMITTING    — attempt_events from SimulationResult drive visual playback
 *   4. SIGNAL_TRANSIT  — Signal propagating from spacecraft to Earth
 *   5. COMPLETE        — Summary
 *
 * PLAYBACK FIDELITY (Phase 5.1E):
 *   - ONE attempt_event → ONE visual attempt (invariant A21)
 *   - Retries = separate visual attempts (invariant A22)
 *   - Progress derived from absolute wall-clock time (invariant A23/A24)
 *   - Deferred packets produce zero pulses (invariant A20)
 *   - Zero-attempt edge case handled cleanly (invariant A29)
 *
 * METRICS (Phase 5.1E):
 *   - DOWNLINK ATTEMPTS = attempt_events.length (not grouped packet count)
 *   - ATTEMPTED PRODUCTS = unique packet IDs in attempt_events
 *   - RETRIES = attempt_events where attempt_number > 1
 *   - DELIVERED/FAILED/DEFERRED from simulation_result authoritatively
 *
 * IMPORTANT: Does NOT modify SimulationResult. Presentation only.
 */

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import type { ApproveResponse, CandidatePlan, SimulationResult } from '../types/domain';
import type { ExperiencePlaybackConfig } from '../types/experience';
import { buildTransmissionPlayback } from '../experience/transmissionPlayback';
import { formatBitsAsDataVolume, formatDuration } from '../utils/formatters';

// ── Types ─────────────────────────────────────────────────────────────────────

export type TransmissionChoreographyPhase =
  | 'plan_uplink'
  | 'contact_wait'
  | 'transmitting'
  | 'signal_transit'
  | 'complete';

// ── Helpers ───────────────────────────────────────────────────────────────────

const MUTED = 'rgba(147,160,180,0.7)';
const DIM = 'rgba(147,160,180,0.4)';

function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined') return false;
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function SequenceRow({
  active,
  done,
  label,
  sub,
}: {
  active: boolean;
  done: boolean;
  label: string;
  sub?: string;
}) {
  const color = done ? '#34d399' : active ? '#6EA8FF' : 'rgba(147,160,180,0.35)';
  const icon = done ? '✓' : active ? '●' : '○';
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '6px 0', opacity: done || active ? 1 : 0.45 }}>
      <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 11, color, flexShrink: 0, marginTop: 1 }}>{icon}</span>
      <div>
        <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 10, fontWeight: 600, color, letterSpacing: '0.06em' }}>
          {label}
        </div>
        {sub && (
          <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 11, color: DIM, marginTop: 2 }}>{sub}</div>
        )}
      </div>
    </div>
  );
}


// ── Transmission Progress Panel ───────────────────────────────────────────────
//
// Phase 5.1E corrections:
//   - totalAttempts = attempt_events.length (NOT groupAttemptsByPacket().length)
//   - visibleAttemptCount tracks individual attempt events, not packet summaries
//   - DOWNLINK ATTEMPTS label is accurate

function TransmissionProgressPanel({
  sim,
  visibleAttemptIndex,
}: {
  sim: SimulationResult;
  /** 0-based index of the last visible attempt event (inclusive). -1 = none visible yet. */
  visibleAttemptIndex: number;
}) {
  const attemptEvents = useMemo(() => sim.attempt_events ?? [], [sim]);
  const deferredCount = useMemo(() => sim.deferred_packets.length, [sim]);

  // Authoritative counts from simulation_result (INVARIANT E9)
  const totalAttempts = attemptEvents.length;      // attempt_events.length
  const currentAttempts = Math.max(0, Math.min(visibleAttemptIndex + 1, totalAttempts));

  // Attempt metrics from visible events
  const visibleEvents = useMemo(() => attemptEvents.slice(0, currentAttempts), [attemptEvents, currentAttempts]);

  // Retries = attempt_number > 1
  const retriesSoFar = useMemo(
    () => visibleEvents.filter((e) => e.attempt_number > 1).length,
    [visibleEvents]
  );

  // Unique attempted products (from visible events only)
  const attemptedProducts = useMemo(
    () => new Set(visibleEvents.map((e) => e.packet_id)).size,
    [visibleEvents]
  );

  // Delivered and failed from authoritative result (only count if their last attempt is visible)
  const visiblePacketIds = useMemo(() => new Set(visibleEvents.map((e) => e.packet_id)), [visibleEvents]);
  const deliveredSoFar = useMemo(
    () => sim.delivered_packets.filter((id) => visiblePacketIds.has(id)).length,
    [sim.delivered_packets, visiblePacketIds]
  );
  const failedSoFar = useMemo(
    () => sim.failed_packets.filter((id) => visiblePacketIds.has(id)).length,
    [sim.failed_packets, visiblePacketIds]
  );

  return (
    <div>
      {/* Progress header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: DIM, letterSpacing: '0.1em' }}>
          DOWNLINK ATTEMPTS · TIME-COMPRESSED PLAYBACK
        </span>
        <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 10, color: '#6EA8FF', fontWeight: 700 }}>
          {totalAttempts === 0 ? '0/0' : `${currentAttempts}/${totalAttempts}`}
        </span>
      </div>

      {/* Progress bar */}
      <div style={{ height: 3, background: 'rgba(46,58,79,0.8)', borderRadius: 2, marginBottom: 10 }}>
        <div style={{
          height: '100%', borderRadius: 2,
          width: `${totalAttempts > 0 ? (currentAttempts / totalAttempts) * 100 : 0}%`,
          background: '#6EA8FF',
          transition: 'width 0.3s ease',
        }} />
      </div>

      {/* Attempt metric grid */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 10, flexWrap: 'wrap' }}>
        {[
          { label: 'ATTEMPTED PRODUCTS', value: attemptedProducts, color: '#6EA8FF' },
          { label: 'DELIVERED', value: deliveredSoFar, color: '#34d399' },
          { label: 'RETRIES', value: retriesSoFar, color: '#f59e0b' },
          { label: 'FAILED', value: failedSoFar, color: '#f87171' },
        ].map(({ label, value, color }) => (
          <div key={label}>
            <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: DIM, letterSpacing: '0.08em' }}>{label}</div>
            <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 15, fontWeight: 700, color }}>{value}</div>
          </div>
        ))}
      </div>

      {/* Deferred summary — shown immediately, NOT animated */}
      {deferredCount > 0 && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '5px 8px', marginBottom: 6,
          background: 'rgba(245,158,11,0.05)',
          border: '1px solid rgba(245,158,11,0.18)',
          borderRadius: 4,
        }}>
          <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#f59e0b', letterSpacing: '0.07em' }}>
            DEFERRED THIS CONTACT
          </span>
          <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 12, fontWeight: 700, color: '#f59e0b' }}>
            {deferredCount.toLocaleString()}
          </span>
        </div>
      )}

      {/* Individual attempt event list — one row per attempt event */}
      <div style={{ maxHeight: 200, overflowY: 'auto' }}>
        {visibleEvents.map((ev, idx) => {
          const isRetry = ev.attempt_number > 1;
          const isFinalDelivered = ev.status === 'success' && sim.delivered_packets.includes(ev.packet_id);
          const isFinalFailed = ev.status === 'failure';
          const statusColor = ev.status === 'success' ? '#34d399' : '#f87171';
          const statusIcon = ev.status === 'success' ? '✓' : '✕';
          return (
            <div key={`${ev.packet_id}-${ev.attempt_number}-${idx}`} style={{
              display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0',
              borderBottom: '1px solid rgba(46,58,79,0.3)',
            }}>
              <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 10, color: statusColor, flexShrink: 0, width: 14 }}>
                {statusIcon}
              </span>
              <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#6EA8FF', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {ev.packet_id}
              </span>
              {isRetry && (
                <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: '#f59e0b', flexShrink: 0 }}>
                  RETRY #{ev.attempt_number}
                </span>
              )}
              <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: DIM, flexShrink: 0, minWidth: 48, textAlign: 'right' }}>
                {isFinalDelivered ? 'delivered' : isFinalFailed ? 'failed' : 'attempt'}
              </span>
            </div>
          );
        })}
        {visibleEvents.length === 0 && (
          <div style={{ color: DIM, fontSize: 11, padding: '8px 0', fontFamily: '"IBM Plex Sans"' }}>
            {totalAttempts === 0 ? 'No transmission attempts — all products deferred.' : 'Awaiting transmission attempts…'}
          </div>
        )}
      </div>

      {/* Scientific honesty note */}
      <div style={{
        marginTop: 8, padding: '5px 8px',
        background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(46,58,79,0.5)', borderRadius: 3,
        fontFamily: '"IBM Plex Sans"', fontSize: 10, color: DIM, lineHeight: 1.4,
      }}>
        ℹ Packet pulse outcome is a visualization of the simulator result, not a physical loss-location model.
      </div>
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

interface Props {
  /** Initial phase on mount. */
  initialPhase: TransmissionChoreographyPhase;
  /** Pending plan for uplink display. */
  pendingPlan: CandidatePlan | null;
  /** Playback configuration from experience manifest. */
  playbackConfig: ExperiencePlaybackConfig | null;
  /** One-way propagation delay in seconds. */
  propagationDelayS: number | null;
  /** Available capacity for display. */
  availableCapacityBits: number;
  /**
   * Stable execution identifier from application-level coordinator.
   * The approval Promise is already registered in executionPromiseRef before
   * this panel mounts. Calling onExecuteApproval just retrieves that Promise.
   */
  executionId: string;
  /**
   * Wall-clock ms when playback started (null if not yet started for this executionId).
   * Used to support absolute-time catch-up when panel remounts.
   */
  playbackStartedAtMs: number | null;
  /**
   * Called ONCE per execution when the visual playback phase starts (entering TRANSMITTING).
   * The coordinator stores this value so that remounted panels can catch up.
   */
  onSetPlaybackStarted: (ms: number) => void;
  /**
   * Retrieve the backend approval Promise — already dispatched at authorization time.
   * Returns the same Promise regardless of how many times called with the same executionId.
   * No new backend call is made by calling this.
   */
  onExecuteApproval: (executionId: string) => Promise<ApproveResponse>;
  /** Called when transmission sequence is fully complete. */
  onComplete: (result: ApproveResponse) => void;
  /** Called on error during approval. */
  onError: (msg: string) => void;
  /**
   * Called when a new attempt-pulse animation starts (for 3D visualization).
   * Called with null to clear the active pulse (between attempts, on complete).
   * Only called for actual attempt events — never for deferred packets.
   */
  onAttemptPulse?: (pulse: {
    packetId: string;
    attemptNumber: number;
    /** Absolute-time derived progress 0→1 along the comm link curve */
    progress: number;
    outcome: 'pending' | 'success' | 'failure' | 'retry' | 'deferred';
    /** Direction for the 3D link (downlink attempts are always spacecraft→earth) */
    direction?: 'spacecraft_to_earth';
  } | null) => void;
  /**
   * Phase 5.1E: Called whenever the choreography phase changes.
   * Allows MissionControl to update pulseDirection on the 3D viewport
   * (plan_uplink = earth→spacecraft, other phases = spacecraft→earth or idle).
   */
  onPhaseChange?: (phase: TransmissionChoreographyPhase) => void;
}

export function TransmissionSequencePanel({
  initialPhase,
  pendingPlan,
  playbackConfig,
  propagationDelayS,
  availableCapacityBits,
  executionId,
  playbackStartedAtMs,
  onSetPlaybackStarted,
  onExecuteApproval,
  onComplete,
  onError,
  onAttemptPulse,
  onPhaseChange,
}: Props) {
  const [phase, setPhase] = useState<TransmissionChoreographyPhase>(initialPhase);
  const [simResult, setSimResult] = useState<SimulationResult | null>(null);
  const [approveResult, setApproveResult] = useState<ApproveResponse | null>(null);

  /**
   * visibleAttemptIndex: 0-based index of the currently visible attempt event.
   * -1 = no attempts visible yet.
   * This tracks attempt_events (not packet summaries).
   */
  const [visibleAttemptIndex, setVisibleAttemptIndex] = useState(-1);

  const playbackTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  /** Stable ref to playbackStartedAtMs for use in interval callbacks. */
  const playbackStartedAtMsRef = useRef<number | null>(playbackStartedAtMs);
  const reduced = prefersReducedMotion();

  const uplinkDurationMs = playbackConfig?.uplink_duration_ms ?? 1500;
  const contactAcqDurationMs = playbackConfig?.contact_acquisition_ms ?? 2000;
  const propagationDurationMs = playbackConfig?.propagation_duration_ms ?? 3000;
  const transmissionMinDurationMs = playbackConfig?.transmission_min_duration_ms ?? 2000;

  // Build attempt-event-based playback from sim result
  const playback = useMemo(() => {
    if (!simResult) return null;
    return buildTransmissionPlayback(simResult, { transmission_min_duration_ms: transmissionMinDurationMs });
  }, [simResult, transmissionMinDurationMs]);

  // Phase change helper — reports upward to MissionControl
  const advancePhase = useCallback((newPhase: TransmissionChoreographyPhase) => {
    setPhase(newPhase);
    onPhaseChange?.(newPhase);
  }, [onPhaseChange]);

  const advanceToContactWait = useCallback(() => advancePhase('contact_wait'), [advancePhase]);
  const advanceToTransmitting = useCallback(() => advancePhase('transmitting'), [advancePhase]);
  const advanceToSignalTransit = useCallback(() => advancePhase('signal_transit'), [advancePhase]);
  const advanceToComplete = useCallback(() => advancePhase('complete'), [advancePhase]);

  // PLAN_UPLINK → CONTACT_WAIT after uplink duration
  useEffect(() => {
    if (phase !== 'plan_uplink') return;
    if (reduced) { advanceToContactWait(); return; }
    const timer = setTimeout(advanceToContactWait, uplinkDurationMs);
    return () => clearTimeout(timer);
  }, [phase, uplinkDurationMs, reduced, advanceToContactWait]);

  // Keep the ref in sync with the prop (for catch-up after remount)
  useEffect(() => {
    playbackStartedAtMsRef.current = playbackStartedAtMs;
  }, [playbackStartedAtMs]);

  // CONTACT_WAIT → await already-dispatched approval → TRANSMITTING
  //
  // Phase 5.1E: The approval Promise was already dispatched at authorization time
  // in MissionControl. This effect simply awaits that promise and advances the
  // visual stage when it resolves.
  //
  // If this component unmounts while we're waiting, the Promise continues executing
  // in MissionControl's executionResultRef — the backend execution is NOT cancelled.
  //
  // The contactAcqDurationMs delay is purely visual presentation — the Promise is
  // NOT waiting for this timer to dispatch; it was dispatched immediately at auth.
  useEffect(() => {
    if (phase !== 'contact_wait') return;

    let cancelled = false;
    const delay = reduced ? 0 : contactAcqDurationMs;

    const timer = setTimeout(async () => {
      if (cancelled) return;
      try {
        // Retrieve the already-dispatched Promise (no new backend call)
        const result = await onExecuteApproval(executionId);
        if (cancelled) return;
        setApproveResult(result);
        setSimResult(result.simulation_result);
        advanceToTransmitting();
      } catch (err) {
        if (!cancelled) onError(String(err));
      }
    }, delay);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, contactAcqDurationMs, reduced]);
  // Note: onExecuteApproval and executionId are intentionally excluded from deps.
  // The coordinator is stable by design. The Promise is keyed by executionId and
  // will always return the same result regardless of how many times it is called.

  // TRANSMITTING: absolute-time attempt-event playback.
  //
  // Phase 5.1E correctness:
  //   - Iterates attempt_events directly (not grouped packet summaries)
  //   - visibleAttemptIndex tracks individual events (1 event = 1 visual attempt)
  //   - Progress for each active attempt is derived from absolute wall-clock time → 0..1
  //   - Browser background throttling does NOT stall playback (time is authoritative)
  //   - Zero attempts: handle cleanly without division by zero or infinite timers
  useEffect(() => {
    if (phase !== 'transmitting' || !playback) return;

    // Record playback start time (ONCE per execution)
    if (!playbackStartedAtMsRef.current) {
      const nowMs = Date.now();
      playbackStartedAtMsRef.current = nowMs;
      onSetPlaybackStarted(nowMs);
    }

    const attemptEvents = simResult?.attempt_events ?? [];
    const totalAttempts = attemptEvents.length;
    const totalVisualMs = playback.totalVisualDurationMs;

    if (reduced) {
      // prefers-reduced-motion: skip animation, jump to final state immediately
      setVisibleAttemptIndex(totalAttempts - 1);
      onAttemptPulse?.(null);
      const timer = setTimeout(advanceToSignalTransit, 300);
      return () => clearTimeout(timer);
    }

    // Zero-attempt edge case (all deferred): advance immediately without timers
    if (totalAttempts === 0) {
      setVisibleAttemptIndex(-1);
      onAttemptPulse?.(null);
      setTimeout(advanceToSignalTransit, 300);
      return;
    }

    /**
     * Derive which attempt_event should be "current" from absolute elapsed time.
     * Returns the 0-based index of the current event, or -1 if not started.
     * This makes catch-up after background/remount deterministic.
     */
    function computeCurrentAttemptIndex(): number {
      const startMs = playbackStartedAtMsRef.current;
      if (!startMs) return -1;
      const elapsedMs = Date.now() - startMs;

      // Find the playback event whose visualOffset has elapsed
      // attempt_events[i] corresponds to playback events with kind='attempt_start'
      const startEvents = playback!.events.filter((e) => e.kind === 'attempt_start');

      // Find the last attempt_start event whose visualOffsetMs <= elapsedMs
      let currentIdx = -1;
      for (let i = 0; i < startEvents.length; i++) {
        if (startEvents[i].visualOffsetMs <= elapsedMs) {
          currentIdx = i;
        }
      }
      return Math.min(currentIdx, totalAttempts - 1);
    }

    /**
     * Compute absolute-time progress [0, 1] for the given attempt_event.
     * progress=0: pulse at spacecraft, progress=1: pulse reaches Earth.
     */
    function computeAttemptProgress(attemptIdx: number): number {
      const startMs = playbackStartedAtMsRef.current;
      if (!startMs || attemptIdx < 0) return 0;

      const startEvents = playback!.events.filter((e) => e.kind === 'attempt_start');
      const ev = startEvents[attemptIdx];
      if (!ev) return 0;

      const elapsedInAttemptMs = Date.now() - (startMs + ev.visualOffsetMs);
      const progress = elapsedInAttemptMs / ev.visualDurationMs;
      return Math.max(0, Math.min(1, progress));
    }

    // Immediate catch-up on mount (handles tab-return / panel remount)
    const initialIdx = computeCurrentAttemptIndex();
    setVisibleAttemptIndex(initialIdx);

    // If already fully elapsed, advance immediately
    const startMs = playbackStartedAtMsRef.current;
    if (startMs && Date.now() - startMs >= totalVisualMs) {
      onAttemptPulse?.(null);
      setTimeout(advanceToSignalTransit, 0);
      return;
    }

    // Emit initial pulse for current attempt
    if (initialIdx >= 0 && initialIdx < totalAttempts) {
      const ev = attemptEvents[initialIdx];
      if (ev) {
        const progress = computeAttemptProgress(initialIdx);
        const outcome = progress < 1 ? 'pending'
          : ev.status === 'success' ? (ev.attempt_number > 1 ? 'retry' : 'success')
          : 'failure';
        onAttemptPulse?.({
          packetId: ev.packet_id,
          attemptNumber: ev.attempt_number,
          progress,
          outcome,
        });
      }
    }

    const intervalMs = Math.max(50, totalVisualMs / Math.max(1, totalAttempts * 10));
    const interval = setInterval(() => {
      const nowMs = Date.now();
      const elapsedMs = startMs ? nowMs - startMs : 0;

      const idx = computeCurrentAttemptIndex();
      setVisibleAttemptIndex(idx);

      // Emit 3D pulse for the active attempt with absolute-time progress
      if (idx >= 0 && idx < totalAttempts) {
        const ev = attemptEvents[idx];
        if (ev) {
          const progress = computeAttemptProgress(idx);
          const outcome = progress < 1 ? 'pending'
            : ev.status === 'success' ? (ev.attempt_number > 1 ? 'retry' : 'success')
            : 'failure';
          onAttemptPulse?.({
            packetId: ev.packet_id,
            attemptNumber: ev.attempt_number,
            progress,
            outcome,
          });
        }
      }

      // Check if total visual duration has elapsed
      if (elapsedMs >= totalVisualMs) {
        clearInterval(interval);
        setVisibleAttemptIndex(totalAttempts - 1);
        onAttemptPulse?.(null);
        setTimeout(advanceToSignalTransit, 600);
      }
    }, intervalMs);
    playbackTimerRef.current = interval;
    return () => {
      clearInterval(interval);
      onAttemptPulse?.(null);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, playback, simResult, reduced]);
  // Note: advanceToSignalTransit, onSetPlaybackStarted, onAttemptPulse are stable
  // callbacks that should not restart the playback interval on re-render.

  // SIGNAL_TRANSIT → COMPLETE after propagation duration
  useEffect(() => {
    if (phase !== 'signal_transit') return;
    const dur = reduced ? 300 : propagationDurationMs;
    const timer = setTimeout(advanceToComplete, dur);
    return () => clearTimeout(timer);
  }, [phase, propagationDurationMs, reduced, advanceToComplete]);

  // COMPLETE — call parent
  useEffect(() => {
    if (phase !== 'complete' || !approveResult) return;
    onComplete(approveResult);
  }, [phase, approveResult, onComplete]);

  // Cleanup
  useEffect(() => {
    return () => {
      if (playbackTimerRef.current) clearInterval(playbackTimerRef.current);
    };
  }, []);

  const planPayloadBits = pendingPlan?.packets.reduce((s, p) => s + p.size_bits, 0) ?? 0;
  const oneWayLabel = propagationDelayS !== null ? formatDuration(propagationDelayS) : '—';

  const phases: Array<{ id: TransmissionChoreographyPhase; label: string; sub?: string }> = [
    {
      id: 'plan_uplink',
      label: 'PLAN UPLINK · VISUALIZATION',
      sub: 'Ground → spacecraft (presentation only)',
    },
    { id: 'contact_wait', label: 'CONTACT ACQUISITION', sub: 'Acquiring high-rate downlink' },
    { id: 'transmitting', label: 'DOWNLINK TRANSMISSION', sub: 'Spacecraft → Ground' },
    { id: 'signal_transit', label: 'SIGNAL IN TRANSIT', sub: `${oneWayLabel} one-way propagation` },
    { id: 'complete', label: 'TRANSMISSION COMPLETE', sub: 'Simulation done' },
  ];
  const phaseOrder = phases.map((p) => p.id);
  const currentIdx = phaseOrder.indexOf(phase);

  return (
    <div style={{
      background: 'rgba(8,12,22,0.95)',
      border: '1px solid rgba(76,141,255,0.18)',
      borderRadius: 8, padding: '14px 16px',
    }}>
      {/* Sequence tracker */}
      <div style={{ marginBottom: 14 }}>
        {phases.map((p, i) => (
          <SequenceRow
            key={p.id}
            active={i === currentIdx}
            done={i < currentIdx}
            label={p.label}
            sub={p.sub}
          />
        ))}
      </div>

      {/* Phase-specific content */}

      {/* PLAN_UPLINK */}
      {phase === 'plan_uplink' && (
        <div style={{ background: 'rgba(76,141,255,0.06)', border: '1px solid rgba(76,141,255,0.2)', borderRadius: 6, padding: '10px 12px' }}>
          <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: 'rgba(76,141,255,0.7)', letterSpacing: '0.1em', marginBottom: 6 }}>
            PLAN UPLINK · VISUALIZATION ONLY
          </div>
          <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: 'rgba(76,141,255,0.55)', letterSpacing: '0.08em', marginBottom: 8 }}>
            DIRECTION: EARTH → SPACECRAFT
          </div>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: DIM, letterSpacing: '0.07em' }}>ONE-WAY SIGNAL</div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 14, fontWeight: 700, color: '#6EA8FF' }}>{oneWayLabel}</div>
            </div>
            {planPayloadBits > 0 && (
              <div>
                <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: DIM, letterSpacing: '0.07em' }}>PLAN PAYLOAD</div>
                <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 14, fontWeight: 700, color: '#e2e8f4' }}>{formatBitsAsDataVolume(planPayloadBits)}</div>
              </div>
            )}
          </div>
          {!reduced && (
            <div style={{ marginTop: 8, fontFamily: '"IBM Plex Sans"', fontSize: 10, color: DIM, lineHeight: 1.5 }}>
              TIME-COMPRESSED VISUALIZATION · Command uplink is visual only.<br/>
              The backend simulator models only the downlink.<br/>
              Authorization and backend execution began immediately when you approved.
            </div>
          )}
        </div>
      )}

      {/* CONTACT_WAIT */}
      {phase === 'contact_wait' && (
        <div style={{ background: 'rgba(52,211,153,0.04)', border: '1px solid rgba(52,211,153,0.18)', borderRadius: 6, padding: '10px 12px' }}>
          <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: 'rgba(52,211,153,0.7)', letterSpacing: '0.1em', marginBottom: 6 }}>
            ACQUIRING HIGH-RATE CONTACT…
          </div>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: DIM }}>NOMINAL LINK RATE</div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 13, fontWeight: 700, color: '#34d399' }}>2.8 Mbps</div>
            </div>
            <div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: DIM }}>CONTACT CAPACITY</div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 13, fontWeight: 700, color: '#e2e8f4' }}>{formatBitsAsDataVolume(availableCapacityBits)}</div>
            </div>
          </div>
          <div style={{ marginTop: 8, fontFamily: '"IBM Plex Sans"', fontSize: 10, color: DIM }}>
            Awaiting simulation result…
          </div>
        </div>
      )}

      {/* TRANSMITTING */}
      {phase === 'transmitting' && simResult && (
        <TransmissionProgressPanel
          sim={simResult}
          visibleAttemptIndex={visibleAttemptIndex}
        />
      )}
      {phase === 'transmitting' && !simResult && (
        <div style={{ color: DIM, fontSize: 11, fontFamily: '"IBM Plex Sans"' }}>
          Awaiting simulation result…
        </div>
      )}

      {/* SIGNAL_TRANSIT */}
      {phase === 'signal_transit' && simResult && (
        <div style={{ background: 'rgba(110,168,255,0.05)', border: '1px solid rgba(110,168,255,0.2)', borderRadius: 6, padding: '10px 12px' }}>
          <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: 'rgba(110,168,255,0.7)', letterSpacing: '0.1em', marginBottom: 6 }}>
            SPACECRAFT TRANSMISSION COMPLETE
          </div>
          <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 11, color: '#6EA8FF', marginBottom: 8 }}>
            SIGNAL IN TRANSIT TO EARTH
          </div>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: DIM }}>ONE-WAY PROPAGATION</div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 14, fontWeight: 700, color: '#6EA8FF' }}>{oneWayLabel}</div>
            </div>
            <div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: DIM }}>DELIVERED</div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 14, fontWeight: 700, color: '#34d399' }}>{simResult.delivered_packets.length}</div>
            </div>
            <div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: DIM }}>DEFERRED</div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 14, fontWeight: 700, color: simResult.deferred_packets.length > 0 ? '#f59e0b' : MUTED }}>{simResult.deferred_packets.length}</div>
            </div>
          </div>
          {!reduced && (
            <div style={{ marginTop: 8, fontFamily: '"IBM Plex Sans"', fontSize: 10, color: DIM }}>
              TIME-COMPRESSED · Propagation delay represented separately from transmission time.
            </div>
          )}
        </div>
      )}

      {/* COMPLETE */}
      {phase === 'complete' && simResult && (
        <div style={{ background: 'rgba(52,211,153,0.05)', border: '1px solid rgba(52,211,153,0.22)', borderRadius: 6, padding: '10px 12px' }}>
          <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: 'rgba(52,211,153,0.7)', letterSpacing: '0.1em', marginBottom: 6 }}>
            TRANSMISSION SEQUENCE COMPLETE
          </div>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: DIM }}>DOWNLINK ATTEMPTS</div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 16, fontWeight: 700, color: '#6EA8FF' }}>{(simResult.attempt_events ?? []).length}</div>
            </div>
            <div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: DIM }}>DELIVERED</div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 16, fontWeight: 700, color: '#34d399' }}>{simResult.delivered_packets.length}</div>
            </div>
            <div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: DIM }}>FAILED</div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 16, fontWeight: 700, color: simResult.failed_packets.length > 0 ? '#f87171' : MUTED }}>{simResult.failed_packets.length}</div>
            </div>
            <div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: DIM }}>DEFERRED</div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 16, fontWeight: 700, color: simResult.deferred_packets.length > 0 ? '#f59e0b' : MUTED }}>{simResult.deferred_packets.length}</div>
            </div>
            {simResult.elapsed_time_s > 0 && (
              <div>
                <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: DIM }}>SIM ELAPSED</div>
                <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 14, fontWeight: 700, color: MUTED }}>{simResult.elapsed_time_s.toFixed(1)} s</div>
              </div>
            )}
          </div>
          <div style={{ marginTop: 8, fontFamily: '"IBM Plex Sans"', fontSize: 10, color: DIM, lineHeight: 1.4 }}>
            SIMULATED RECEPTION CONFIRMED · See Log for full simulation details and ground evidence.
          </div>
        </div>
      )}

      {/* Plan uplink visualization note — always shown during uplink */}
      {phase === 'plan_uplink' && (
        <div style={{ marginTop: 8, padding: '4px 8px', background: 'rgba(76,141,255,0.04)', border: '1px solid rgba(76,141,255,0.12)', borderRadius: 3 }}>
          <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: 'rgba(76,141,255,0.5)' }}>
            Backend execution began immediately at authorization — not waiting for this animation.
          </span>
        </div>
      )}
    </div>
  );
}
