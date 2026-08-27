/**
 * TransmissionSequencePanel — Phase 5.1G
 *
 * EXECUTION VS PRESENTATION SEPARATION:
 *   - The backend approval is dispatched IMMEDIATELY at authorization time in MissionControl.
 *   - This panel OBSERVES the execution — it does NOT own the backend dispatch.
 *   - onExecuteApproval(executionId) returns the already-existing Promise (FAIL CLOSED).
 *   - The CONTACT_WAIT stage just awaits an already-dispatched approval Promise.
 *   - Unmounting this panel CANNOT cancel or delay the backend execution.
 *
 * Transmission choreography sequence:
 *   1. PLAN_UPLINK     — Earth → spacecraft command uplink (visualization only)
 *   2. CONTACT_WAIT    — Acquiring high-rate contact (awaits already-dispatched approval Promise)
 *   3. TRANSMITTING    — attempt_events from SimulationResult drive visual playback
 *   4. SIGNAL_TRANSIT  — Signal propagating from spacecraft to Earth
 *   5. COMPLETE        — Summary
 *
 * Phase 5.1G CORRECTIONS (WORKSTREAM A — ABSOLUTE-TIME EARLY TIMELINE):
 *   - authorizedAtMs prop anchors PLAN_UPLINK and CONTACT_WAIT presentation boundaries.
 *   - deriveEarlyExecutionPhase() from transmissionPlayback is used to compute current phase
 *     from absolute elapsed time — never from component mount time.
 *   - On mount/remount, early phase is derived IMMEDIATELY from Date.now() and authorizedAtMs.
 *   - setTimeout timers fire ONLY for the remaining duration (boundary - now), not full duration.
 *   - If early phases have already elapsed on remount, skips directly to correct state.
 *   - If result is not yet available after early phases, shows AWAITING AUTHORITATIVE RESULT.
 *   - Navigation away and back can never reset PLAN_UPLINK or CONTACT_WAIT.
 *
 * Phase 5.1F CORRECTIONS (preserved):
 *   - initialPhase comes from application-level presentationPhase (WORKSTREAM A)
 *   - Uses visualSegments from buildTransmissionPlayback for non-overlapping timeline (G/H)
 *   - Attempt rows show "IN FLIGHT" until visual segment completes (WORKSTREAM E)
 *   - Pulse uses isRetry + status instead of outcome='retry' (WORKSTREAM F)
 *
 * PLAYBACK FIDELITY:
 *   - ONE attempt_event → ONE visual segment (invariant F21)
 *   - Non-overlapping visual segments: segment[i+1].start >= segment[i].end (F22)
 *   - Every segment can reach progress=1 before next segment starts (F23)
 *   - Progress derived from absolute wall-clock time (F24)
 *   - Deferred packets produce zero pulses (F25)
 *
 * IMPORTANT: Does NOT modify SimulationResult. Presentation only.
 */

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import type { ApproveResponse, CandidatePlan, SimulationResult } from '../types/domain';
import type { ExperiencePlaybackConfig } from '../types/experience';
import {
  buildTransmissionPlayback,
  deriveEarlyExecutionPhase,
  msUntilNextPhaseBoundary,
} from '../experience/transmissionPlayback';
import type { VisualAttemptSegment } from '../experience/transmissionPlayback';
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
// Phase 5.1F corrections:
//   - Uses VisualAttemptSegment for per-row rendering (WORKSTREAM E/F)
//   - Active attempt (progress < 1) shows "IN FLIGHT" — never reveals outcome early (WORKSTREAM E)
//   - Completed attempt shows authoritative SUCCESS / FAILURE (WORKSTREAM E)
//   - Retry identity shown separately from success/failure outcome (WORKSTREAM F)

function TransmissionProgressPanel({
  sim,
  visualSegments,
  activeSegmentIndex,
  activeSegmentProgress,
}: {
  sim: SimulationResult;
  /** All visual attempt segments from buildVisualAttemptSegments. */
  visualSegments: VisualAttemptSegment[];
  /**
   * 0-based index of the currently active segment (-1 = none active yet).
   * "Active" means: elapsed >= visualStartMs AND elapsed < visualEndMs.
   */
  activeSegmentIndex: number;
  /**
   * Progress [0, 1] of the active segment.
   * 0 = pulse just started, 1 = pulse reached Earth.
   */
  activeSegmentProgress: number;
}) {
  const deferredCount = useMemo(() => sim.deferred_packets.length, [sim]);
  const totalAttempts = visualSegments.length;

  // Number of completed segments (strictly before the active one)
  const completedCount = Math.max(0, activeSegmentIndex);
  // Include active segment as "visible" in the count display
  const currentDisplay = activeSegmentIndex >= 0 ? activeSegmentIndex + 1 : 0;

  // Segments that are completed (strictly before activeSegmentIndex)
  const completedSegments = useMemo(
    () => visualSegments.slice(0, completedCount),
    [visualSegments, completedCount]
  );

  // Active segment (in flight)
  const activeSegment = activeSegmentIndex >= 0 && activeSegmentIndex < totalAttempts
    ? visualSegments[activeSegmentIndex]
    : null;

  // Delivered/failed from authoritative result (only after visual completion)
  const completedIds = useMemo(
    () => new Set(completedSegments.map((s) => s.packetId)),
    [completedSegments]
  );
  const deliveredSoFar = useMemo(
    () => sim.delivered_packets.filter((id) => completedIds.has(id)).length,
    [sim.delivered_packets, completedIds]
  );
  const failedSoFar = useMemo(
    () => sim.failed_packets.filter((id) => completedIds.has(id)).length,
    [sim.failed_packets, completedIds]
  );
  const retriesSoFar = useMemo(
    () => completedSegments.filter((s) => s.isRetry).length,
    [completedSegments]
  );
  const attemptedProducts = useMemo(
    () => new Set(completedSegments.map((s) => s.packetId)).size,
    [completedSegments]
  );

  return (
    <div>
      {/* Progress header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: DIM, letterSpacing: '0.1em' }}>
          DOWNLINK ATTEMPTS · TIME-COMPRESSED PLAYBACK
        </span>
        <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 10, color: '#6EA8FF', fontWeight: 700 }}>
          {totalAttempts === 0 ? '0/0' : `${currentDisplay}/${totalAttempts}`}
        </span>
      </div>

      {/* Progress bar */}
      <div style={{ height: 3, background: 'rgba(46,58,79,0.8)', borderRadius: 2, marginBottom: 10 }}>
        <div style={{
          height: '100%', borderRadius: 2,
          width: `${totalAttempts > 0 ? (completedCount / totalAttempts) * 100 : 0}%`,
          background: '#6EA8FF',
          transition: 'width 0.3s ease',
        }} />
      </div>

      {/* Attempt metric grid — counts from COMPLETED segments only */}
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

      {/* Individual attempt rows — one per segment */}
      <div style={{ maxHeight: 200, overflowY: 'auto' }}>
        {/* Active (in-flight) attempt — shown first if active */}
        {activeSegment && (
          <div key={`active-${activeSegment.packetId}-${activeSegment.attemptNumber}`} style={{
            display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0',
            borderBottom: '1px solid rgba(46,58,79,0.3)',
            background: 'rgba(76,141,255,0.04)',
          }}>
            <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 10, color: '#6EA8FF', flexShrink: 0, width: 14 }}>
              {/* Phase 5.1F (WORKSTREAM E): show ● while in flight, never reveal outcome early */}
              {activeSegmentProgress < 1 ? '●' : '…'}
            </span>
            <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#6EA8FF', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {activeSegment.packetId}
            </span>
            {activeSegment.isRetry && (
              <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: '#f59e0b', flexShrink: 0 }}>
                RETRY #{activeSegment.attemptNumber}
              </span>
            )}
            <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: '#6EA8FF', flexShrink: 0, minWidth: 64, textAlign: 'right' }}>
              {activeSegment.isRetry ? `RETRY #${activeSegment.attemptNumber} · ` : ''}IN FLIGHT
            </span>
          </div>
        )}

        {/* Completed attempts — shown in reverse-chronological order */}
        {completedSegments.slice().reverse().map((seg, idx) => {
          const statusColor = seg.authoritativeStatus === 'success' ? '#34d399' : '#f87171';
          const statusIcon = seg.authoritativeStatus === 'success' ? '✓' : '✕';
          const statusLabel = seg.authoritativeStatus === 'success' ? 'success' : 'failed';
          return (
            <div key={`${seg.packetId}-${seg.attemptNumber}-${idx}`} style={{
              display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0',
              borderBottom: '1px solid rgba(46,58,79,0.3)',
            }}>
              <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 10, color: statusColor, flexShrink: 0, width: 14 }}>
                {statusIcon}
              </span>
              <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: 'rgba(147,160,180,0.6)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {seg.packetId}
              </span>
              {seg.isRetry && (
                <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: '#f59e0b', flexShrink: 0 }}>
                  RETRY #{seg.attemptNumber}
                </span>
              )}
              <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: statusColor, flexShrink: 0, minWidth: 48, textAlign: 'right' }}>
                {statusLabel}
              </span>
            </div>
          );
        })}

        {completedSegments.length === 0 && !activeSegment && (
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
  /**
   * Initial phase on mount (application-level presentationPhase from MissionControl).
   * Used as the floor — the panel may advance forward from this but never backwards.
   * On remount this is the furthest phase reached so far, so the panel resumes correctly.
   */
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
   * Phase 5.1G (WORKSTREAM A): Wall-clock ms when operator authorized the execution.
   * This is the ABSOLUTE TIME ANCHOR for PLAN_UPLINK and CONTACT_WAIT presentation.
   * Must be non-null when this panel is mounted (authorization always precedes mount).
   *
   * Using this value, the panel derives the correct early phase from Date.now() on every
   * mount/remount — timers are set for the REMAINING duration only, not full duration.
   * Navigation / unmount cannot reset the early presentation timeline.
   */
  authorizedAtMs: number;
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
   * Phase 5.1F (WORKSTREAM F): Called when a new attempt-pulse animation starts.
   * status: 'pending' while pulse in flight (progress < 1), authoritative on completion.
   * isRetry separates retry identity from success/failure status.
   * No 'retry' status — retry is isRetry=true with status='success'|'failure'.
   */
  onAttemptPulse?: (pulse: {
    packetId: string;
    attemptNumber: number;
    isRetry: boolean;
    /** Absolute-time derived progress 0→1 along the comm link curve */
    progress: number;
    /** 'pending' while in flight; authoritative status after visual completion */
    status: 'pending' | 'success' | 'failure';
    /** Direction for the 3D link (downlink attempts are always spacecraft→earth) */
    direction?: 'spacecraft_to_earth';
  } | null) => void;
  /**
   * Called whenever the choreography phase changes.
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
  authorizedAtMs,
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
   * Phase 5.1G: Track whether we are actively awaiting the backend result
   * after early presentation phases have elapsed (AWAITING AUTHORITATIVE RESULT state).
   * Becomes true when early phases complete but result is not yet available.
   * Clears when result arrives.
   */
  const [awaitingResult, setAwaitingResult] = useState(false);

  /**
   * Phase 5.1F: activeSegmentIndex tracks which VisualAttemptSegment is currently active.
   * -1 = no segment active yet.
   * activeSegmentProgress = [0, 1] progress of the active segment's pulse.
   */
  const [activeSegmentIndex, setActiveSegmentIndex] = useState(-1);
  const [activeSegmentProgress, setActiveSegmentProgress] = useState(0);

  const playbackTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  /** Stable ref to playbackStartedAtMs for use in interval callbacks. */
  const playbackStartedAtMsRef = useRef<number | null>(playbackStartedAtMs);
  const reduced = prefersReducedMotion();

  const uplinkDurationMs = playbackConfig?.uplink_duration_ms ?? 1500;
  const contactAcqDurationMs = playbackConfig?.contact_acquisition_ms ?? 2000;
  const propagationDurationMs = playbackConfig?.propagation_duration_ms ?? 3000;
  const transmissionMinDurationMs = playbackConfig?.transmission_min_duration_ms ?? 2000;

  // Build non-overlapping visual segments from sim result (Phase 5.1F — WORKSTREAM G/H)
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

  // Keep the ref in sync with the prop (for catch-up after remount)
  useEffect(() => {
    playbackStartedAtMsRef.current = playbackStartedAtMs;
  }, [playbackStartedAtMs]);

  // ── WORKSTREAM A: Absolute-time early phase derivation ────────────────────
  //
  // Phase 5.1G: PLAN_UPLINK and CONTACT_WAIT are derived from authorizedAtMs
  // (absolute wall-clock time), NOT from component mount time.
  //
  // On every mount/remount we:
  //   1. Call deriveEarlyExecutionPhase() with Date.now() to get the current phase.
  //   2. If we are already past these phases, advance immediately.
  //   3. If still in an early phase, set a timer for only the REMAINING duration.
  //
  // This means navigating away for 5 seconds and returning does NOT restart a 1500ms timer.
  // Instead: max(uplinkEndMs - now, 0) is used as the remaining duration.
  //
  // Only runs for early phases (plan_uplink, contact_wait).
  // TRANSMITTING and later are managed by the absolute-time playback interval.
  useEffect(() => {
    // Only manage early phases here
    if (phase !== 'plan_uplink' && phase !== 'contact_wait') return;

    if (reduced) {
      // prefers-reduced-motion: skip early animation immediately
      if (phase === 'plan_uplink') advanceToContactWait();
      // contact_wait will be handled by the approval effect below
      return;
    }

    const now = Date.now();
    const earlyPhase = deriveEarlyExecutionPhase({
      nowMs: now,
      authorizedAtMs,
      uplinkDurationMs,
      contactAcquisitionMs: contactAcqDurationMs,
      resultAvailable: false, // we don't have result yet in early phases
    });

    if (earlyPhase === 'plan_uplink' && phase === 'plan_uplink') {
      // Remaining time until uplink phase ends
      const remaining = msUntilNextPhaseBoundary(now, authorizedAtMs, uplinkDurationMs, contactAcqDurationMs);
      const timer = setTimeout(advanceToContactWait, Math.max(0, remaining));
      return () => clearTimeout(timer);
    }

    if (earlyPhase === 'contact_wait' && phase === 'plan_uplink') {
      // Uplink already elapsed — advance immediately
      advanceToContactWait();
      return;
    }

    // earlyPhase is awaiting_result or ready_for_transmission:
    // Early phases have elapsed. The approval effect will handle the rest.
    // advance to contact_wait if still in plan_uplink so the approval awaiter fires
    if (phase === 'plan_uplink') {
      advanceToContactWait();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, authorizedAtMs, uplinkDurationMs, contactAcqDurationMs, reduced]);

  // CONTACT_WAIT → await already-dispatched approval → TRANSMITTING
  //
  // The approval Promise was already dispatched at authorization time in MissionControl.
  // This effect simply awaits that promise (FAIL CLOSED — no new dispatch).
  // If remounting during TRANSMITTING, this effect won't fire (phase !== 'contact_wait').
  //
  // Phase 5.1G: the timer delay is the REMAINING contact acquisition time, not the full duration.
  // If contact acquisition already elapsed (remounted after long absence), delay is 0.
  useEffect(() => {
    if (phase !== 'contact_wait') return;

    let cancelled = false;

    // Phase 5.1G: use remaining time, not full duration
    const now = Date.now();
    const remaining = reduced
      ? 0
      : msUntilNextPhaseBoundary(now, authorizedAtMs, uplinkDurationMs, contactAcqDurationMs);

    const timer = setTimeout(async () => {
      if (cancelled) return;
      try {
        // Retrieve the already-dispatched Promise (no new backend call — WORKSTREAM B)
        const result = await onExecuteApproval(executionId);
        if (cancelled) return;
        setAwaitingResult(false);
        setApproveResult(result);
        setSimResult(result.simulation_result);
        advanceToTransmitting();
      } catch (err) {
        if (!cancelled) onError(String(err));
      }
    }, remaining);

    // If we'll be waiting AFTER early phases complete (remaining == 0 but still awaiting
    // the Promise resolution), show awaiting state
    if (remaining === 0) {
      setAwaitingResult(true);
    }

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, authorizedAtMs, uplinkDurationMs, contactAcqDurationMs, reduced]);
  // Note: onExecuteApproval and executionId are intentionally excluded from deps.
  // The coordinator is stable by design — same Promise always returned for same executionId.

  // TRANSMITTING: absolute-time visual-segment playback (Phase 5.1F — WORKSTREAM E/F/G/H)
  //
  //   - Uses visualSegments (non-overlapping) instead of raw attempt events
  //   - activeSegmentIndex: which segment is currently active (elapsed >= start AND < end)
  //   - activeSegmentProgress: [0, 1] progress within the active segment
  //   - Pulse status='pending' while progress < 1 (WORKSTREAM E — never reveal outcome early)
  //   - Pulse status=authoritativeStatus when segment ends (WORKSTREAM E)
  //   - isRetry is a separate flag, NOT a status value (WORKSTREAM F)
  //   - Browser backgrounding: absolute-time catch-up
  //   - Zero attempts: handle cleanly
  useEffect(() => {
    if (phase !== 'transmitting' || !playback) return;

    // Record playback start time (ONCE per execution)
    if (!playbackStartedAtMsRef.current) {
      const nowMs = Date.now();
      playbackStartedAtMsRef.current = nowMs;
      onSetPlaybackStarted(nowMs);
    }

    const segments = playback.visualSegments;
    const totalAttempts = segments.length;
    const totalVisualMs = playback.totalVisualDurationMs;

    if (reduced) {
      // prefers-reduced-motion: skip animation, jump to final state immediately
      setActiveSegmentIndex(totalAttempts - 1);
      setActiveSegmentProgress(1);
      onAttemptPulse?.(null);
      const timer = setTimeout(advanceToSignalTransit, 300);
      return () => clearTimeout(timer);
    }

    // Zero-attempt edge case (all deferred): advance immediately without timers
    if (totalAttempts === 0) {
      setActiveSegmentIndex(-1);
      setActiveSegmentProgress(0);
      onAttemptPulse?.(null);
      setTimeout(advanceToSignalTransit, 300);
      return;
    }

    const startMs = playbackStartedAtMsRef.current!;

    /**
     * Phase 5.1F: Derive active segment index from absolute elapsed time.
     * A segment is "active" when: elapsed >= seg.visualStartMs AND elapsed < seg.visualEndMs
     * Returns -1 if before first segment, or last index if all segments complete.
     */
    function computeActiveSegmentIndex(elapsedMs: number): number {
      // Find segment that contains this elapsed time
      for (let i = 0; i < segments.length; i++) {
        const seg = segments[i];
        if (elapsedMs >= seg.visualStartMs && elapsedMs < seg.visualEndMs) {
          return i;
        }
      }
      // After all segments: return last index (segments are complete)
      if (elapsedMs >= segments[segments.length - 1].visualEndMs) {
        return segments.length - 1;
      }
      // Before first segment starts
      return -1;
    }

    /**
     * Compute absolute-time progress [0, 1] for the given segment.
     * progress=0: pulse at spacecraft, progress=1: pulse has reached Earth.
     * Phase 5.1F: progress is bounded [0, 1] per segment, not global.
     */
    function computeSegmentProgress(segIdx: number, elapsedMs: number): number {
      if (segIdx < 0 || segIdx >= segments.length) return 0;
      const seg = segments[segIdx];
      const p = (elapsedMs - seg.visualStartMs) / seg.visualDurationMs;
      return Math.max(0, Math.min(1, p));
    }

    // Immediate catch-up on mount (handles tab-return / panel remount — WORKSTREAM A)
    const initialElapsed = Date.now() - startMs;
    const initialIdx = computeActiveSegmentIndex(initialElapsed);
    const initialProgress = computeSegmentProgress(initialIdx, initialElapsed);
    setActiveSegmentIndex(initialIdx);
    setActiveSegmentProgress(initialProgress);

    // If already fully elapsed, advance immediately
    if (initialElapsed >= totalVisualMs) {
      onAttemptPulse?.(null);
      setTimeout(advanceToSignalTransit, 0);
      return;
    }

    // Emit initial pulse for current segment
    if (initialIdx >= 0 && initialIdx < totalAttempts) {
      const seg = segments[initialIdx];
      // Phase 5.1F (WORKSTREAM E): status=pending while progress < 1
      // Phase 5.1F (WORKSTREAM F): isRetry is separate from status
      const status: 'pending' | 'success' | 'failure' = initialProgress < 1
        ? 'pending'
        : seg.authoritativeStatus;
      onAttemptPulse?.({
        packetId: seg.packetId,
        attemptNumber: seg.attemptNumber,
        isRetry: seg.isRetry,
        progress: initialProgress,
        status,
      });
    }

    const intervalMs = Math.max(50, totalVisualMs / Math.max(1, totalAttempts * 10));
    const interval = setInterval(() => {
      const nowMs = Date.now();
      const elapsedMs = nowMs - startMs;

      const idx = computeActiveSegmentIndex(elapsedMs);
      const progress = computeSegmentProgress(idx, elapsedMs);
      setActiveSegmentIndex(idx);
      setActiveSegmentProgress(progress);

      // Emit 3D pulse for the active segment
      if (idx >= 0 && idx < totalAttempts) {
        const seg = segments[idx];
        // Phase 5.1F (WORKSTREAM E): never reveal outcome while in flight
        const status: 'pending' | 'success' | 'failure' = progress < 1
          ? 'pending'
          : seg.authoritativeStatus;
        onAttemptPulse?.({
          packetId: seg.packetId,
          attemptNumber: seg.attemptNumber,
          isRetry: seg.isRetry,
          progress,
          status,
        });
      }

      // Check if total visual duration has elapsed
      if (elapsedMs >= totalVisualMs) {
        clearInterval(interval);
        setActiveSegmentIndex(totalAttempts - 1);
        setActiveSegmentProgress(1);
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
  }, [phase, playback, reduced]);
  // Note: advanceToSignalTransit, onSetPlaybackStarted, onAttemptPulse are stable callbacks.

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
          {/* Phase 5.1G: show AWAITING AUTHORITATIVE RESULT when early phases elapsed but backend pending */}
          {awaitingResult ? (
            <>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: 'rgba(52,211,153,0.7)', letterSpacing: '0.1em', marginBottom: 6 }}>
                CONTACT ACQUIRED
              </div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: 'rgba(245,158,11,0.8)', letterSpacing: '0.1em', marginBottom: 8 }}>
                AWAITING AUTHORITATIVE EXECUTION RESULT
              </div>
              <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 10, color: DIM, lineHeight: 1.5 }}>
                TIME-COMPRESSED VISUALIZATION · Backend execution is in progress.
                Transmission playback will begin when the authoritative result arrives.
              </div>
            </>
          ) : (
            <>
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
            </>
          )}
        </div>
      )}

      {/* TRANSMITTING — Phase 5.1F: uses visualSegments + activeSegmentIndex/Progress */}
      {phase === 'transmitting' && simResult && playback && (
        <TransmissionProgressPanel
          sim={simResult}
          visualSegments={playback.visualSegments}
          activeSegmentIndex={activeSegmentIndex}
          activeSegmentProgress={activeSegmentProgress}
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
