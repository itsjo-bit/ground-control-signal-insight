import { useEffect, useRef, useState } from 'react';
import type { ApproveResponse } from '../types/domain';

interface Props {
  /** null until the operator approves a plan; populated from /approve response. */
  approveResult: ApproveResponse | null;
}

type PacketStatus = 'delivered' | 'deferred' | 'failed' | 'pending';

interface AnimPacket {
  id: string;
  status: PacketStatus;
  progressTarget: number; // 0..1 — position on timeline when animation lands
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

/**
 * SimulationPanel — Feature 4.
 *
 * After approval, plays back the transmission result as a timeline animation.
 * Packets appear sequentially along a progress bar, then settle into their
 * final colour. Uses staggered CSS transitions driven by a requestAnimationFrame
 * ticker — no streaming backend required.
 */
export function SimulationPanel({ approveResult }: Props) {
  // ── Pre-approval placeholder ─────────────────────────────────────────────
  if (approveResult === null) {
    return (
      <section className="panel panel-full">
        <h2>Simulation</h2>
        <p style={{ color: '#57606a' }}>
          <strong style={{ color: '#8b949e' }}>No simulation executed yet.</strong>
          &nbsp;Approve a valid plan to run the transmission simulation.
        </p>
      </section>
    );
  }

  return <SimulationPlayback approveResult={approveResult} />;
}

// ---------------------------------------------------------------------------
// Animated playback sub-component — only mounted after approval
// ---------------------------------------------------------------------------

function SimulationPlayback({ approveResult }: { approveResult: ApproveResponse }) {
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

  const deliveredCount = allPackets.slice(0, revealedCount).filter((p) => p.status === 'delivered').length;
  const deferredCount  = allPackets.slice(0, revealedCount).filter((p) => p.status === 'deferred').length;
  const failedCount    = allPackets.slice(0, revealedCount).filter((p) => p.status === 'failed').length;
  const isDone = revealedCount >= total;

  return (
    <section className="panel panel-full">
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
              title={`${pkt.id} — ${STATUS_LABEL[pkt.status]}`}
              style={{
                left: `${posLeft}%`,
                background: revealed ? STATUS_COLOUR[pkt.status] : 'var(--border)',
                transform: revealed ? 'translate(-50%, -50%) scale(1.2)' : 'translate(-50%, -50%) scale(0.7)',
                opacity: revealed ? 1 : 0.35,
                transition: 'background 300ms ease-out, transform 250ms ease-out, opacity 250ms ease-out',
              }}
            />
          );
        })}
      </div>

      {/* Summary table — shown when playback is done */}
      {isDone && (
        <table style={{ marginTop: 16 }}>
          <thead>
            <tr>
              <th>Elapsed (s)</th>
              <th>Delivered</th>
              <th>Deferred</th>
              <th>Failed</th>
              <th>Window remaining (s)</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td style={{ fontWeight: 700 }}>{sim.elapsed_time_s.toFixed(2)}</td>
              <td style={{ color: 'var(--signal)', fontWeight: 700 }}>{sim.delivered_packets.length}</td>
              <td style={{ color: 'var(--text-muted)', fontWeight: 700 }}>{sim.deferred_packets.length}</td>
              <td style={{ color: sim.failed_packets.length > 0 ? 'var(--critical)' : 'var(--text-muted)', fontWeight: 700 }}>
                {sim.failed_packets.length}
              </td>
              <td>{sim.link_state.remaining_window_s.toFixed(1)}</td>
            </tr>
          </tbody>
        </table>
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
  }));
  const deferred = sim.deferred_packets.map((id, i) => ({
    id,
    status: 'deferred' as PacketStatus,
    progressTarget: 1 - (i + 1) / Math.max(sim.deferred_packets.length + 1, 2) * 0.2,
  }));
  const failed = sim.failed_packets.map((id) => ({
    id,
    status: 'failed' as PacketStatus,
    progressTarget: 1,
  }));
  return [...delivered, ...deferred, ...failed];
}
