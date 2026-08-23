import { useState, useRef } from 'react';
import { approvePlan, approveCustomPlan } from '../api/client';
import type { ApproveResponse, CandidatePlan, Packet } from '../types/domain';

interface Props {
  /** plan_id of the AI-recommended plan, or null when no recommendation is available. */
  recommendedPlanId: string | null;
  /** The baseline plan — used to populate the drag-to-reorder list. */
  baselinePlan: CandidatePlan | null;
  onApproved: (result: ApproveResponse) => void;
}

const TYPE_COLOUR: Record<string, string> = {
  critical: 'var(--critical)',
  science: 'var(--warn)',
  telemetry: 'var(--ai)',
};

function packetTypeColour(type: string): string {
  return TYPE_COLOUR[type.toLowerCase()] ?? 'var(--text-muted)';
}

export function ApprovalBar({ recommendedPlanId, baselinePlan, onApproved }: Props) {
  const [notes, setNotes] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Feature 3: drag-to-reorder state
  const [customOrder, setCustomOrder] = useState<Packet[] | null>(null);
  const dragIdx = useRef<number | null>(null);
  const dragOverIdx = useRef<number | null>(null);

  // Initialise custom order lazily from baseline
  const packets = customOrder ?? baselinePlan?.packets ?? [];

  // ── Unavailable state ────────────────────────────────────────────────────
  if (recommendedPlanId === null) {
    return (
      <section className="approval-bar" style={{ opacity: 0.6 }}>
        <h2>Approval</h2>
        <p style={{ color: '#8b949e' }}>
          <strong>Approval unavailable.</strong>
          &nbsp;Waiting for a valid AI recommendation.
        </p>
        <p style={{ color: '#57606a', fontSize: 12, marginTop: 6 }}>
          No plan can be approved until the AI provider returns a valid recommendation.
          Ensure the backend has a scenario loaded and refresh to enable approval.
        </p>
      </section>
    );
  }

  // ── Active state ─────────────────────────────────────────────────────────
  async function handleApprove() {
    setLoading(true);
    setError(null);
    try {
      const result = await approvePlan(recommendedPlanId!, notes);
      onApproved(result);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }

  async function handleOverride() {
    if (!baselinePlan || packets.length === 0) return;
    setLoading(true);
    setError(null);
    try {
      const customPlan: CandidatePlan = {
        ...baselinePlan,
        plan_id: 'operator-override',
        strategy: 'operator_override',
        packets,
        generated_by: 'operator',
        metadata: { ...baselinePlan.metadata, override: true },
      };
      const result = await approveCustomPlan(customPlan, notes);
      onApproved(result);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }

  // ── Drag handlers (native HTML5 DnD) ─────────────────────────────────────
  function handleDragStart(idx: number) {
    dragIdx.current = idx;
  }

  function handleDragOver(e: React.DragEvent, idx: number) {
    e.preventDefault();
    dragOverIdx.current = idx;
  }

  function handleDrop() {
    const from = dragIdx.current;
    const to = dragOverIdx.current;
    if (from === null || to === null || from === to) return;

    const base = customOrder ?? (baselinePlan?.packets ?? []);
    const next = [...base];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    setCustomOrder(next);
    dragIdx.current = null;
    dragOverIdx.current = null;
  }

  function handleDragEnd() {
    dragIdx.current = null;
    dragOverIdx.current = null;
  }

  function resetOrder() {
    setCustomOrder(null);
  }

  const isCustom = customOrder !== null;

  return (
    <section className="approval-bar">
      <h2>Approval</h2>

      {/* Notes input */}
      <div style={{ marginBottom: 10 }}>
        <label style={{ color: 'var(--text-muted)', fontSize: 12 }}>
          Operator notes:&nbsp;
          <input
            type="text"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Optional notes for the record…"
            style={{ width: '40%' }}
          />
        </label>
      </div>

      {/* Action buttons */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        <button
          onClick={handleApprove}
          disabled={loading}
          className="btn-approve"
        >
          ✓ Approve AI plan ({recommendedPlanId})
        </button>
        <button
          onClick={handleOverride}
          disabled={loading || packets.length === 0}
          className="btn-override"
          title="Submit the operator-reordered packet list below"
        >
          ⚡ Submit reordered override
        </button>
        {isCustom && (
          <button onClick={resetOrder} disabled={loading} className="btn-reset">
            ↺ Reset order
          </button>
        )}
        {loading && <span style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', fontSize: 12 }}> Submitting…</span>}
        {error && <span style={{ color: 'var(--critical)', fontFamily: 'var(--font-mono)', fontSize: 12 }}> Error: {error}</span>}
      </div>

      {/* Drag-to-reorder packet list */}
      {baselinePlan && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <h3 style={{ margin: 0 }}>Packet order{isCustom ? ' — CUSTOM (drag to reorder)' : ' — drag to reorder'}</h3>
            {isCustom && (
              <span style={{
                background: 'rgba(255,182,72,0.12)', color: 'var(--warn)',
                border: '1px solid rgba(255,182,72,0.4)',
                borderRadius: 3, padding: '1px 7px',
                fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 600,
              }}>
                CUSTOM
              </span>
            )}
          </div>
          <div className="drag-list" role="list" aria-label="Packet transmission order — drag to reorder">
            {packets.map((pkt, idx) => (
              <div
                key={pkt.packet_id}
                draggable
                role="listitem"
                className="drag-item"
                onDragStart={() => handleDragStart(idx)}
                onDragOver={(e) => handleDragOver(e, idx)}
                onDrop={handleDrop}
                onDragEnd={handleDragEnd}
                aria-label={`${idx + 1}. ${pkt.packet_id} — ${pkt.packet_type}`}
              >
                <span className="drag-handle" title="Drag to reorder">⠿</span>
                <span className="drag-rank">{idx + 1}</span>
                <code className="drag-id">{pkt.packet_id}</code>
                <span
                  className="drag-type"
                  style={{ color: packetTypeColour(pkt.packet_type) }}
                >
                  {pkt.packet_type}
                </span>
                <span className="drag-crit">crit {pkt.criticality.toFixed(2)}</span>
                <span className="drag-size">{(pkt.size_bits / 1024).toFixed(1)} kb</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
