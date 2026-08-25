import { useEffect, useRef, useState } from 'react';
import type { ApproveResponse } from '../types/domain';

interface Props {
  /** null until the operator approves a plan; populated from /approve response. */
  approveResult: ApproveResponse | null;
  /**
   * One-way signal propagation delay in seconds — from GET /state.
   * null for legacy scenarios without distance_km.
   * Phase 2E-D5: shown after playback as "signal confirmation expected in ~Xs".
   */
  propagationDelayS: number | null;
}

type PacketStatus = 'delivered' | 'deferred' | 'failed' | 'pending';

interface AnimPacket {
  id: string;
  status: PacketStatus;
  progressTarget: number; // 0..1 — position on timeline when animation lands
  retransmits: number;
}

const STATUS_COLOUR: Record<PacketStatus, string> = {
  delivered: 'var(--signal)',
  deferred: 'var(--text-muted)',
  failed: 'var(--critical)',
  pending: 'var(--text-dim)',
};

const STATUS_LABEL: Record<PacketStatus, string> = {
  delivered: 'DELIVERED',
  deferred: 'DEFERRED',
  failed: 'FAILED',
  pending: '…',
};

function fmtSeconds(s: number): string {
  if (s >= 3600) return `${(s / 3600).toFixed(1)} h`;
  if (s >= 60) return `${(s / 60).toFixed(1)} min`;
  return `${s.toFixed(1)} s`;
}

/**
 * SimulationPanel — Feature 4 / Phase 2E-D5.
 *
 * After approval, plays back the transmission result as a timeline animation.
 * Packets appear sequentially along a progress bar, then settle into their
 * final colour. Uses staggered CSS transitions driven by a requestAnimationFrame
 * ticker — no streaming backend required.
 *
 * Phase 2E-D5 enhancements:
 * - Propagation delay context shown post-playback.
 * - Per-packet retransmission counts highlighted.
 * - Live scrolling packet log revealed as animation plays.
 */
export function SimulationPanel({ approveResult, propagationDelayS }: Props) {
  // ── Pre-approval placeholder ─────────────────────────────────────────────
  if (approveResult === null) {
    return (
      <section className="panel">
        <h2>Simulation</h2>
        <p style={{ color: '#57606a' }}>
          <strong style={{ color: '#8b949e' }}>No simulation executed yet.</strong>
          &nbsp;Approve a valid plan to run the transmission simulation.
        </p>
      </section>
    );
  }

  return <SimulationPlayback approveResult={approveResult} propagationDelayS={propagationDelayS} />;
}

// ---------------------------------------------------------------------------
// Animated playback sub-component — only mounted after approval
// ---------------------------------------------------------------------------

function SimulationPlayback({
  approveResult,
  propagationDelayS,
}: {
  approveResult: ApproveResponse;
  propagationDelayS: number | null;
}) {
  const sim = approveResult.simulation_result;

  // Build the ordered packet list with final statuses
  const allPackets: AnimPacket[] = buildPackets(sim);
  const total = allPackets.length;

  const [revealedCount, setRevealedCount] = useState(0);
  const [playing, setPlaying] = useState(true);
  const rafRef = useRef<number | null>(null);
  const lastTimeRef = useRef<number | null>(null);
  // Reveal one packet every ~220ms during playback
  const INTERVAL_MS = 220;

  useEffect(() => {
    if (!playing) return;
    if (revealedCount >= total) { setPlaying(false); return; }

    function tick(now: number) {
      if (lastTimeRef.current === null) lastTimeRef.current = now;
      const elapsed = now - lastTimeRef.current;
      if (elapsed >= INTERVAL_MS) {
        lastTimeRef.current = now;
        setRevealedCount((c) => {
          const next = c + 1;
          if (next >= total) setPlaying(false);
          return next;
        });
      }
      if (revealedCount < total) {
        rafRef.current = requestAnimationFrame(tick);
      }
    }

    rafRef.current = requestAnimationFrame(tick);
    return () => { if (rafRef.current !== null) cancelAnimationFrame(rafRef.current); };
  }, [playing, revealedCount, total]);

  function handlePlayPause() {
    setPlaying((p) => !p);
    lastTimeRef.current = null;
  }

  function handleReplay() {
    setRevealedCount(0);
    setPlaying(true);
    lastTimeRef.current = null;
  }

  const revealedPackets = allPackets.slice(0, revealedCount);
  const deliveredCount = revealedPackets.filter((p) => p.status === 'delivered').length;
  const deferredCount  = revealedPackets.filter((p) => p.status === 'deferred').length;
  const failedCount    = revealedPackets.filter((p) => p.status === 'failed').length;
  const isDone = revealedCount >= total;

  // Any packets that needed retransmissions in the final result (not just revealed)
  const retransmitEntries = Object.entries(sim.retransmission_counts).filter(([, v]) => v > 0);

  return (
    <section className="panel">
      <h2>
        Simulation
        {isDone && (
          <span style={{
            marginLeft: 10, fontSize: 12, fontWeight: 400,
            color: 'var(--signal)', textTransform: 'none', letterSpacing: 0,
          }}>
            ✓ Complete — plan <code>{sim.plan_id}</code>
          </span>
        )}
      </h2>

      {/* Controls */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 14 }}>
        <button
          className="sim-ctrl"
          onClick={handlePlayPause}
          aria-label={playing ? 'Pause' : 'Play'}
          title={playing ? 'Pause playback' : 'Resume playback'}
        >
          {playing ? '⏸' : '▶'}
        </button>
        <button
          className="sim-ctrl"
          onClick={handleReplay}
          aria-label="Replay"
          title="Replay animation from start"
        >
          ↺
        </button>
        <span style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', fontSize: 12 }}>
          {revealedCount}/{total} packets
        </span>
        <span style={{ color: 'var(--signal)', fontFamily: 'var(--font-mono)', fontSize: 12, marginLeft: 10 }}>
          ✓ {deliveredCount}
        </span>
        <span style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', fontSize: 12 }}>
          ⊘ {deferredCount}
        </span>
        {failedCount > 0 && (
          <span style={{ color: 'var(--critical)', fontFamily: 'var(--font-mono)', fontSize: 12 }}>
            ✗ {failedCount}
          </span>
        )}
      </div>

      {/* Timeline track */}
      <div className="sim-timeline">
        <div
          className="sim-timeline-fill"
          style={{
            width: `${total > 0 ? (revealedCount / total) * 100 : 0}%`,
            transition: 'width 200ms linear',
          }}
        />
        {allPackets.map((pkt, idx) => {
          const revealed = idx < revealedCount;
          const posLeft = total > 1 ? (idx / (total - 1)) * 100 : 50;
          return (
            <div
              key={pkt.id}
              className="sim-marker"
              title={`${pkt.id} — ${STATUS_LABEL[pkt.status]}${pkt.retransmits > 0 ? ` (${pkt.retransmits} retx)` : ''}`}
              style={{
                left: `${posLeft}%`,
                background: revealed ? STATUS_COLOUR[pkt.status] : 'var(--border)',
                transform: revealed ? 'translate(-50%, -50%) scale(1.2)' : 'translate(-50%, -50%) scale(0.7)',
                opacity: revealed ? 1 : 0.35,
                transition: 'background 300ms ease-out, transform 250ms ease-out, opacity 250ms ease-out',
                // Ring for retransmitted packets
                outline: revealed && pkt.retransmits > 0 ? '2px solid rgba(255,182,72,0.7)' : 'none',
                outlineOffset: 2,
              }}
            />
          );
        })}
      </div>

      {/* Live packet log — revealed as animation plays */}
      {revealedCount > 0 && (
        <div style={{
          marginTop: 12,
          maxHeight: 160,
          overflowY: 'auto',
          background: 'rgba(0,0,0,0.15)',
          borderRadius: 4,
          padding: '6px 8px',
          display: 'flex',
          flexDirection: 'column',
          gap: 2,
        }}>
          {revealedPackets.slice().reverse().map((pkt) => (
            <div
              key={pkt.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                fontFamily: 'var(--font-mono)',
                fontSize: 11,
                color: STATUS_COLOUR[pkt.status],
                animation: 'fade-in 0.15s ease-out',
              }}
            >
              <span style={{ minWidth: 14, textAlign: 'center' }}>
                {pkt.status === 'delivered' ? '✓' : pkt.status === 'failed' ? '✗' : '⊘'}
              </span>
              <code style={{ color: STATUS_COLOUR[pkt.status], fontSize: 11 }}>{pkt.id}</code>
              <span style={{ color: 'var(--text-dim)', fontSize: 10, marginLeft: 'auto' }}>
                {STATUS_LABEL[pkt.status]}
              </span>
              {pkt.retransmits > 0 && (
                <span style={{
                  background: 'rgba(255,182,72,0.10)',
                  color: 'var(--warn)',
                  border: '1px solid rgba(255,182,72,0.3)',
                  borderRadius: 2,
                  padding: '0 4px',
                  fontSize: 9,
                  fontWeight: 700,
                }}>
                  {pkt.retransmits}× retx
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Summary table — shown when playback is done */}
      {isDone && (
        <>
          <table style={{ marginTop: 16 }}>
            <thead>
              <tr>
                <th>Elapsed</th>
                <th>Delivered</th>
                <th>Deferred</th>
                <th>Failed</th>
                <th>Window remaining</th>
                {retransmitEntries.length > 0 && <th>Retransmissions</th>}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td style={{ fontWeight: 700 }}>{fmtSeconds(sim.elapsed_time_s)}</td>
                <td style={{ color: 'var(--signal)', fontWeight: 700 }}>{sim.delivered_packets.length}</td>
                <td style={{ color: 'var(--text-muted)', fontWeight: 700 }}>{sim.deferred_packets.length}</td>
                <td style={{ color: sim.failed_packets.length > 0 ? 'var(--critical)' : 'var(--text-muted)', fontWeight: 700 }}>
                  {sim.failed_packets.length}
                </td>
                <td>{fmtSeconds(sim.link_state.remaining_window_s)}</td>
                {retransmitEntries.length > 0 && (
                  <td style={{ color: 'var(--warn)', fontWeight: 700 }}>
                    {retransmitEntries.length} pkt(s)
                  </td>
                )}
              </tr>
            </tbody>
          </table>

          {/* Propagation delay context — only shown when distance is known */}
          {propagationDelayS !== null && (
            <div style={{
              marginTop: 12,
              padding: '8px 12px',
              background: 'rgba(53,231,183,0.05)',
              border: '1px solid rgba(53,231,183,0.2)',
              borderRadius: 4,
              fontFamily: 'var(--font-mono)',
              fontSize: 11,
            }}>
              <span style={{ color: 'var(--signal)', fontWeight: 700 }}>Signal propagation</span>
              <span style={{ color: 'var(--text-muted)', marginLeft: 8 }}>
                Transmission data will reach Earth receivers in{' '}
                <span style={{ color: 'var(--text)', fontWeight: 700 }}>
                  ~{fmtSeconds(propagationDelayS)}
                </span>
                {' '}(one-way).
              </span>
              <span style={{ color: 'var(--text-dim)', marginLeft: 8, fontSize: 10 }}>
                ACK round-trip: ~{fmtSeconds(propagationDelayS * 2)}
              </span>
            </div>
          )}

          {/* Retransmission detail */}
          {retransmitEntries.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <div style={{
                fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 700,
                color: 'var(--warn)', textTransform: 'uppercase', letterSpacing: '0.08em',
                marginBottom: 5,
              }}>
                ⚠ Retransmissions
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {retransmitEntries.map(([id, count]) => (
                  <span key={id} style={{
                    background: 'rgba(255,182,72,0.08)',
                    border: '1px solid rgba(255,182,72,0.3)',
                    borderRadius: 3,
                    padding: '2px 7px',
                    fontFamily: 'var(--font-mono)',
                    fontSize: 11,
                    color: 'var(--warn)',
                  }}>
                    <code style={{ color: 'var(--text-muted)', fontSize: 10 }}>{id}</code>
                    &nbsp;×{count}
                  </span>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function buildPackets(sim: ApproveResponse['simulation_result']): AnimPacket[] {
  // Merge all outcomes into an ordered list.
  // Delivered first (in order), then deferred, then failed.
  const delivered = sim.delivered_packets.map((id, i) => ({
    id,
    status: 'delivered' as PacketStatus,
    progressTarget: sim.elapsed_time_s > 0
      ? i / Math.max(sim.delivered_packets.length, 1)
      : (i + 1) / Math.max(sim.delivered_packets.length + 1, 1),
    retransmits: sim.retransmission_counts[id] ?? 0,
  }));
  const deferred = sim.deferred_packets.map((id, i) => ({
    id,
    status: 'deferred' as PacketStatus,
    progressTarget: 1 - (i + 1) / Math.max(sim.deferred_packets.length + 1, 2) * 0.2,
    retransmits: 0,
  }));
  const failed = sim.failed_packets.map((id) => ({
    id,
    status: 'failed' as PacketStatus,
    progressTarget: 1,
    retransmits: sim.retransmission_counts[id] ?? 0,
  }));
  return [...delivered, ...deferred, ...failed];
}
