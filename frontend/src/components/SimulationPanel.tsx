import type { ApproveResponse } from '../types/domain';

interface Props {
  /** null until the operator approves a plan; populated from /approve response. */
  approveResult: ApproveResponse | null;
}

/**
 * Always-visible simulation results panel.
 * Before approval: shows a "not yet executed" placeholder.
 * After approval:  shows actual data from the simulation_result — never fabricated.
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

  // ── Post-approval results ────────────────────────────────────────────────
  const sim = approveResult.simulation_result;

  return (
    <section className="panel panel-full">
      <h2>
        Simulation
        <span style={{
          marginLeft: 10,
          fontSize: 12,
          fontWeight: 400,
          color: '#22c55e',
          textTransform: 'none',
          letterSpacing: 0,
        }}>
          ✓ Completed — plan <code style={{ background: '#21262d', borderRadius: 3, padding: '1px 5px' }}>{sim.plan_id}</code>
        </span>
      </h2>

      {/* ── Summary row ── */}
      <table style={{ marginBottom: 12 }}>
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
            <td style={{ color: '#22c55e', fontWeight: 700 }}>{sim.delivered_packets.length}</td>
            <td style={{ color: '#eab308', fontWeight: 700 }}>{sim.deferred_packets.length}</td>
            <td style={{ color: sim.failed_packets.length > 0 ? '#ef4444' : '#8b949e', fontWeight: 700 }}>
              {sim.failed_packets.length}
            </td>
            <td>{sim.link_state.remaining_window_s.toFixed(1)}</td>
          </tr>
        </tbody>
      </table>

      {/* ── Per-outcome packet lists ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
        <PacketList
          label="Delivered"
          packetIds={sim.delivered_packets}
          colour="#22c55e"
        />
        <PacketList
          label="Deferred"
          packetIds={sim.deferred_packets}
          colour="#eab308"
        />
        <PacketList
          label="Failed"
          packetIds={sim.failed_packets}
          colour="#ef4444"
        />
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Internal helper
// ---------------------------------------------------------------------------

interface PacketListProps {
  label: string;
  packetIds: string[];
  colour: string;
}

function PacketList({ label, packetIds, colour }: PacketListProps) {
  return (
    <div>
      <h3 style={{ color: colour, marginBottom: 4 }}>{label} ({packetIds.length})</h3>
      {packetIds.length === 0 ? (
        <p style={{ color: '#57606a', fontSize: 12 }}>None</p>
      ) : (
        <ul style={{ listStyle: 'none', padding: 0 }}>
          {packetIds.map((id) => (
            <li key={id} style={{ fontSize: 12, padding: '1px 0' }}>
              <code>{id}</code>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
