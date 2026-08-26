/**
 * TransmissionSequencePanel — Phase 4.2F4
 *
 * Full transmission choreography sequence:
 *   1. PLAN_UPLINK     — Earth → spacecraft command uplink (visualization only)
 *   2. CONTACT_WAIT    — Acquiring high-rate contact
 *   3. TRANSMITTING    — Actual backend approval executes; attempt_events drive playback
 *   4. SIGNAL_TRANSIT  — Signal propagating from spacecraft to Earth
 *   5. COMPLETE        — Summary
 *
 * IMPORTANT INVARIANTS:
 * - Backend approval only executes once (in the CONTACT_WAIT→TRANSMITTING transition)
 * - attempt_events from SimulationResult drive visual playback (not fake counters)
 * - Simulated elapsed_time_s is NOT modified
 * - Time compression is clearly labelled
 * - Deferred packets create no pulse
 * - prefers-reduced-motion: skip animation, show final state
 */

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import type { ApproveResponse, CandidatePlan, SimulationResult } from '../types/domain';
import type { ExperiencePlaybackConfig } from '../types/experience';
import { buildTransmissionPlayback, groupAttemptsByPacket } from '../experience/transmissionPlayback';
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

// ── Packet status icon ────────────────────────────────────────────────────────

function packetIcon(status: 'delivered' | 'failed' | 'deferred') {
  if (status === 'delivered') return '✓';
  if (status === 'failed') return '✕';
  return '⊘';
}
function packetColor(status: 'delivered' | 'failed' | 'deferred') {
  if (status === 'delivered') return '#34d399';
  if (status === 'failed') return '#f87171';
  return 'rgba(147,160,180,0.45)';
}

// ── Transmission Progress Panel ───────────────────────────────────────────────

function TransmissionProgressPanel({
  sim,
  visibleAttemptCount,
}: {
  sim: SimulationResult;
  visibleAttemptCount: number;
}) {
  const summaries = useMemo(() => groupAttemptsByPacket(sim), [sim]);

  // Count stats from visible attempts
  const visibleSummaries = summaries.slice(0, Math.max(0, visibleAttemptCount));
  const deliveredSoFar = visibleSummaries.filter((s) => s.finalStatus === 'delivered').length;
  const retriesSoFar = visibleSummaries.reduce((acc, s) => acc + s.retransmissions, 0);
  const failedSoFar = visibleSummaries.filter((s) => s.finalStatus === 'failed').length;
  const totalAttempts = summaries.length;

  return (
    <div>
      {/* Progress header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: DIM, letterSpacing: '0.1em' }}>
          TRANSMISSION IN PROGRESS · TIME-COMPRESSED PLAYBACK
        </span>
        <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 10, color: '#6EA8FF', fontWeight: 700 }}>
          {visibleAttemptCount}/{totalAttempts}
        </span>
      </div>

      {/* Progress bar */}
      <div style={{ height: 3, background: 'rgba(46,58,79,0.8)', borderRadius: 2, marginBottom: 10 }}>
        <div style={{
          height: '100%', borderRadius: 2,
          width: `${totalAttempts > 0 ? (visibleAttemptCount / totalAttempts) * 100 : 0}%`,
          background: '#6EA8FF',
          transition: 'width 0.3s ease',
        }} />
      </div>

      {/* Counts */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 10, flexWrap: 'wrap' }}>
        {[
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

      {/* Packet list — show visible summaries */}
      <div style={{ maxHeight: 180, overflowY: 'auto' }}>
        {visibleSummaries.map((s) => (
          <div key={s.packetId} style={{
            display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0',
            borderBottom: '1px solid rgba(46,58,79,0.3)',
          }}>
            <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 11, color: packetColor(s.finalStatus), flexShrink: 0, width: 14 }}>
              {packetIcon(s.finalStatus)}
            </span>
            <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 10, color: '#6EA8FF', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {s.packetId}
            </span>
            {s.retransmissions > 0 && (
              <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#f59e0b', flexShrink: 0 }}>
                ↻ ×{s.retransmissions}
              </span>
            )}
            <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: DIM, flexShrink: 0, minWidth: 48, textAlign: 'right' }}>
              {s.finalStatus === 'delivered' ? 'delivered'
                : s.finalStatus === 'failed' ? 'failed'
                : 'deferred'}
            </span>
          </div>
        ))}
        {visibleSummaries.length === 0 && (
          <div style={{ color: DIM, fontSize: 11, padding: '8px 0', fontFamily: '"IBM Plex Sans"' }}>
            Awaiting transmission attempts…
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
  /** Called during CONTACT_WAIT to execute backend approval. */
  onExecuteApproval: () => Promise<ApproveResponse>;
  /** Called when transmission sequence is fully complete. */
  onComplete: (result: ApproveResponse) => void;
  /** Called on error during approval. */
  onError: (msg: string) => void;
}

export function TransmissionSequencePanel({
  initialPhase,
  pendingPlan,
  playbackConfig,
  propagationDelayS,
  availableCapacityBits,
  onExecuteApproval,
  onComplete,
  onError,
}: Props) {
  const [phase, setPhase] = useState<TransmissionChoreographyPhase>(initialPhase);
  const [simResult, setSimResult] = useState<SimulationResult | null>(null);
  const [approveResult, setApproveResult] = useState<ApproveResponse | null>(null);
  const [visibleAttemptCount, setVisibleAttemptCount] = useState(0);
  const [elapsedPlaybackMs, setElapsedPlaybackMs] = useState(0);
  const executedRef = useRef(false);
  const playbackTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const reduced = prefersReducedMotion();

  const uplinkDurationMs = playbackConfig?.uplink_duration_ms ?? 1500;
  const contactAcqDurationMs = playbackConfig?.contact_acquisition_ms ?? 2000;
  const propagationDurationMs = playbackConfig?.propagation_duration_ms ?? 3000;
  const transmissionMinDurationMs = playbackConfig?.transmission_min_duration_ms ?? 2000;

  // Build playback from sim result
  const playback = useMemo(() => {
    if (!simResult) return null;
    return buildTransmissionPlayback(simResult, { transmission_min_duration_ms: transmissionMinDurationMs });
  }, [simResult, transmissionMinDurationMs]);

  // Phase transitions
  const advanceToContactWait = useCallback(() => setPhase('contact_wait'), []);
  const advanceToTransmitting = useCallback(() => setPhase('transmitting'), []);
  const advanceToSignalTransit = useCallback(() => setPhase('signal_transit'), []);
  const advanceToComplete = useCallback(() => setPhase('complete'), []);

  // PLAN_UPLINK → CONTACT_WAIT after uplink duration
  useEffect(() => {
    if (phase !== 'plan_uplink') return;
    if (reduced) { advanceToContactWait(); return; }
    const timer = setTimeout(advanceToContactWait, uplinkDurationMs);
    return () => clearTimeout(timer);
  }, [phase, uplinkDurationMs, reduced, advanceToContactWait]);

  // CONTACT_WAIT → execute backend approval → TRANSMITTING
  useEffect(() => {
    if (phase !== 'contact_wait') return;
    if (executedRef.current) return;
    executedRef.current = true;

    const delay = reduced ? 0 : contactAcqDurationMs;
    const timer = setTimeout(async () => {
      try {
        const result = await onExecuteApproval();
        setApproveResult(result);
        setSimResult(result.simulation_result);
        advanceToTransmitting();
      } catch (err) {
        onError(String(err));
      }
    }, delay);
    return () => clearTimeout(timer);
  }, [phase, contactAcqDurationMs, reduced, onExecuteApproval, onError, advanceToTransmitting]);

  // TRANSMITTING: step through attempt summaries
  useEffect(() => {
    if (phase !== 'transmitting' || !playback) return;
    if (reduced) {
      // Skip animation — jump to end
      setVisibleAttemptCount(groupAttemptsByPacket(simResult!).length);
      const timer = setTimeout(advanceToSignalTransit, 300);
      return () => clearTimeout(timer);
    }

    const totalSummaries = groupAttemptsByPacket(simResult!).length;
    const intervalMs = totalSummaries > 0
      ? Math.max(80, playback.totalVisualDurationMs / totalSummaries)
      : 500;

    let count = 0;
    const interval = setInterval(() => {
      count++;
      setVisibleAttemptCount(count);
      setElapsedPlaybackMs((prev) => prev + intervalMs);
      if (count >= totalSummaries) {
        clearInterval(interval);
        setTimeout(advanceToSignalTransit, 600);
      }
    }, intervalMs);
    playbackTimerRef.current = interval;
    return () => clearInterval(interval);
  }, [phase, playback, simResult, reduced, advanceToSignalTransit]);

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
    { id: 'plan_uplink', label: 'PRIORITY PLAN UPLINK', sub: 'Earth → Spacecraft' },
    { id: 'contact_wait', label: 'CONTACT ACQUISITION', sub: 'Acquiring high-rate downlink' },
    { id: 'transmitting', label: 'DOWNLINK TRANSMISSION', sub: 'Spacecraft → Earth' },
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
            EARTH → SPACECRAFT
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
            <div style={{ marginTop: 8, fontFamily: '"IBM Plex Sans"', fontSize: 10, color: DIM }}>
              TIME-COMPRESSED VISUALIZATION · Command uplink is visual only — the backend simulator models only the downlink.
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
            Executing backend simulation…
          </div>
        </div>
      )}

      {/* TRANSMITTING */}
      {phase === 'transmitting' && simResult && (
        <TransmissionProgressPanel
          sim={simResult}
          visibleAttemptCount={visibleAttemptCount}
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
              TIME-COMPRESSED · 608-second propagation represented separately from transmission time.
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
            {elapsedPlaybackMs > 0 && (
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
    </div>
  );
}
