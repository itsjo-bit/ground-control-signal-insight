import { useState, useMemo } from 'react';
import type { CandidatePlan } from '../types/domain';
import { formatBitsAsDataVolume } from '../utils/formatters';

const PAGE_SIZE = 50;

interface Props {
  plan: CandidatePlan;
}

export function TransmissionQueuePanel({ plan }: Props) {
  const [page, setPage] = useState(0);
  const [search, setSearch] = useState('');

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return plan.packets;
    return plan.packets.filter(
      (pkt) =>
        pkt.packet_id.toLowerCase().includes(q) ||
        pkt.packet_type.toLowerCase().includes(q),
    );
  }, [plan.packets, search]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const pageItems = filtered.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);

  function handleSearch(val: string) {
    setSearch(val);
    setPage(0);
  }

  return (
    <section className="panel">
      <h2>
        Baseline Plan{' '}
        <small style={{ textTransform: 'none', letterSpacing: 0, fontWeight: 400 }}>
          ({plan.strategy})
        </small>
      </h2>

      {/* Search + stats row */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap',
      }}>
        <input
          type="text"
          placeholder="Search ID or type…"
          value={search}
          onChange={(e) => handleSearch(e.target.value)}
          style={{
            flex: '1 1 180px', minWidth: 120, maxWidth: 280,
            background: 'rgba(255,255,255,0.04)',
            border: '1px solid rgba(255,255,255,0.10)',
            borderRadius: 4, padding: '4px 8px',
            color: 'var(--text)', fontSize: 12,
            fontFamily: 'var(--font-mono)',
          }}
        />
        <span style={{ color: 'var(--text-muted)', fontSize: 11, fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap' }}>
          {filtered.length} / {plan.packets.length} pkts
          {' · '}
          {formatBitsAsDataVolume(plan.packets.reduce((s, p) => s + p.size_bits, 0))} total
        </span>
      </div>

      {/* Pagination controls (top) */}
      {totalPages > 1 && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6,
          fontFamily: 'var(--font-mono)', fontSize: 11,
        }}>
          <button
            onClick={() => setPage(0)}
            disabled={safePage === 0}
            style={paginationBtnStyle(safePage === 0)}
          >
            «
          </button>
          <button
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={safePage === 0}
            style={paginationBtnStyle(safePage === 0)}
          >
            ‹ Prev
          </button>
          <span style={{ color: 'var(--text-muted)', flex: 1, textAlign: 'center' }}>
            Page {safePage + 1} / {totalPages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
            disabled={safePage >= totalPages - 1}
            style={paginationBtnStyle(safePage >= totalPages - 1)}
          >
            Next ›
          </button>
          <button
            onClick={() => setPage(totalPages - 1)}
            disabled={safePage >= totalPages - 1}
            style={paginationBtnStyle(safePage >= totalPages - 1)}
          >
            »
          </button>
        </div>
      )}

      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>ID</th>
            <th>Type</th>
            <th>Size</th>
            <th>Criticality</th>
            <th>Relevance</th>
            <th>Deadline</th>
            <th>Delivery</th>
          </tr>
        </thead>
        <tbody>
          {pageItems.map((pkt, i) => (
            <tr key={pkt.packet_id}>
              <td>{safePage * PAGE_SIZE + i + 1}</td>
              <td><code>{pkt.packet_id}</code></td>
              <td>{pkt.packet_type}</td>
              <td>{formatBitsAsDataVolume(pkt.size_bits)}</td>
              <td>{pkt.criticality.toFixed(2)}</td>
              <td>{pkt.mission_relevance.toFixed(2)}</td>
              <td>{pkt.deadline_s.toFixed(1)} s</td>
              <td>{pkt.delivery_requirement}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* Pagination controls (bottom) */}
      {totalPages > 1 && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 6, marginTop: 8,
          fontFamily: 'var(--font-mono)', fontSize: 11,
        }}>
          <button
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={safePage === 0}
            style={paginationBtnStyle(safePage === 0)}
          >
            ‹ Prev
          </button>
          <span style={{ color: 'var(--text-muted)', flex: 1, textAlign: 'center' }}>
            Showing {safePage * PAGE_SIZE + 1}–{Math.min((safePage + 1) * PAGE_SIZE, filtered.length)} of {filtered.length}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
            disabled={safePage >= totalPages - 1}
            style={paginationBtnStyle(safePage >= totalPages - 1)}
          >
            Next ›
          </button>
        </div>
      )}
    </section>
  );
}

function paginationBtnStyle(disabled: boolean): React.CSSProperties {
  return {
    background: disabled ? 'rgba(255,255,255,0.02)' : 'rgba(76,141,255,0.08)',
    color: disabled ? 'rgba(147,160,180,0.3)' : '#6EA8FF',
    border: `1px solid ${disabled ? 'rgba(255,255,255,0.05)' : 'rgba(76,141,255,0.22)'}`,
    borderRadius: 4, padding: '3px 10px',
    fontFamily: 'var(--font-mono)', fontSize: 11,
    cursor: disabled ? 'default' : 'pointer',
  };
}
