import { useState, useRef } from 'react';
import { approvePlan, approveCustomPlan } from '../api/client';
import type { ApproveResponse, CandidatePlan, Packet } from '../types/domain';


/** Phase 2E-D4: operator approval state machine phases. */
export type ApprovalPhase = 'idle' | 'ai_analyzing' | 'ready' | 'transmitting' | 'complete';

interface Props {
  /** plan_id of the AI-recommended plan, or null when no recommendation is available. */
  recommendedPlanId: string | null;
  /**
   * The AI-recommended plan — used to populate the drag-to-reorder list.
   *
   * Phase 2E-D2 (P0 fix): this must be the recommended plan (AI-ordered packets),
   * NOT the raw baseline queue. The operator should see exactly which packets they
   * are approving — the set the AI prioritized and the scheduler selected.
   *
   * Falls back to baselinePlan when the recommended plan is not available (e.g.
   * legacy scenarios or before AI recommendation completes).
   */
  recommendedPlan: CandidatePlan | null;
  /**
   * The baseline plan — kept as a fallback for legacy/unavailable states.
   * Used only when recommendedPlan is null.
   */
  baselinePlan: CandidatePlan | null;
  /** Current phase of the approval state machine. */
  approvalPhase: ApprovalPhase;
  onApproved: (result: ApproveResponse) => void;
  /** Called immediately when the operator clicks Approve — before the async call resolves. */
  onTransmitting: () => void;
  /**
   * Phase 2E-D3 (P0-2): called when the approval POST fails.
   * The parent should reset approvalPhase back to 'ready' so the operator can retry.
   */
  onApprovalError: () => void;
}

const TYPE_COLOUR: Record<string, string> = {
  critical: 'var(--critical)',
  science: 'var(--warn)',
  telemetry: 'var(--ai)',
};

function packetTypeColour(type: string): string {
  return TYPE_COLOUR[type.toLowerCase()] ?? 'var(--text-muted)';
}

export function ApprovalBar({ recommendedPlanId, recommendedPlan, baselinePlan, approvalPhase, onApproved, onTransmitting, onApprovalError }: Props) {
  const [notes, setNotes] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Feature 3: drag-to-reorder state
  const [customOrder, setCustomOrder] = useState<Packet[] | null>(null);
  const dragIdx = useRef<number | null>(null);
  const dragOverIdx = useRef<number | null>(null);

  // Phase 2E-D2 (P0 fix): use recommendedPlan packets as the source of truth.
  // The operator sees and can reorder exactly the packets from the AI-recommended
  // plan — not the raw baseline queue.  Falls back to baseline for legacy scenarios
  // where recommendedPlan is null.
  const sourcePlan = recommendedPlan ?? baselinePlan;
  const packets: Packet[] = customOrder ?? sourcePlan?.packets ?? [];

  // ── AI Analyzing state ────────────────────────────────────────────────────
  if (approvalPhase === 'ai_analyzing') {
    return (
      <section className="approval-bar" style={{ opacity: 0.75 }}>
        <h2>
          Approval
          <span style={{
            marginLeft: 10, fontSize: 9, fontWeight: 700,
            background: 'rgba(124,158,255,0.10)', color: 'var(--ai)',
            border: '1px solid rgba(124,158,255,0.35)',
            borderRadius: 2, padding: '1px 7px', fontFamily: 'var(--font-mono)',
            letterSpacing: '0.06em',
          }}>
            AI ANALYZING
          </span>
        </h2>
        <p style={{ color: 'var(--text-muted)', fontSize: 12, fontFamily: 'var(--font-mono)' }}>
          Waiting for AI prioritization to complete…
        </p>
      </section>
    );
  }

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

  // ── Complete state ────────────────────────────────────────────────────────
  if (approvalPhase === 'complete') {
    return (
      <section className="approval-bar">
        <h2>
          Approval
          <span style={{
            marginLeft: 10, fontSize: 9, fontWeight: 700,
            background: 'rgba(53,231,183,0.08)', color: 'var(--signal)',
            border: '1px solid rgba(53,231,183,0.35)',
            borderRadius: 2, padding: '1px 7px', fontFamily: 'var(--font-mono)',
            letterSpacing: '0.06em',
          }}>
            ✓ TRANSMITTED
          </span>
        </h2>
        <p style={{ color: 'var(--text-muted)', fontSize: 12 }}>
          Transmission complete. Review the simulation results below.
        </p>
      </section>
    );
  }

  // ── Active state ─────────────────────────────────────────────────────────

  // Phase 2E-D3 (P0-1): send the full recommendedPlan so the backend uses it
  // directly — no packet-order loss from regeneration.
  async function handleApprove() {
    if (!recommendedPlan) {
      // Block approval if the recommended plan object is missing.
      // This prevents silently falling back to baseline.
      setError('Recommended plan is not available. Refresh and wait for AI analysis.');
      return;
    }
    onTransmitting();
    setLoading(true);
    setError(null);
    try {
      const result = await approvePlan(recommendedPlanId!, recommendedPlan, notes);
      onApproved(result);
    } catch (err) {
      // Phase 2E-D3 (P0-2): return to 'ready' so operator can retry.
      setError(String(err));
      onApprovalError();
    } finally {
      setLoading(false);
    }
  }

  async function handleOverride() {
    if (!sourcePlan || packets.length === 0) return;
    onTransmitting();
    setLoading(true);
    setError(null);
    try {
      const customPlan: CandidatePlan = {
        ...sourcePlan,
        plan_id: 'operator-override',
        strategy: 'operator_override',
        packets,
        generated_by: 'operator',
        metadata: { ...sourcePlan.metadata, override: true },
      };
      const result = await approveCustomPlan(customPlan, notes);
      onApproved(result);
    } catch (err) {
      // Phase 2E-D3 (P0-2): return to 'ready' so operator can retry.
      setError(String(err));
      onApprovalError();
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

    const base = customOrder ?? (sourcePlan?.packets ?? []);
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

  // Label shown in the drag list header — tells the operator which plan they're editing
  const packetListLabel = recommendedPlan
    ? `AI-recommended plan (${recommendedPlan.plan_id}) — drag to reorder`
    : `Baseline plan — drag to reorder`;

  return (
    <section className="approval-bar">
      <h2>
        Approval
        {approvalPhase === 'transmitting' && (
          <span style={{
            marginLeft: 10, fontSize: 9, fontWeight: 700,
            background: 'rgba(255,182,72,0.10)', color: 'var(--warn)',
            border: '1px solid rgba(255,182,72,0.40)',
            borderRadius: 2, padding: '1px 7px', fontFamily: 'var(--font-mono)',
            letterSpacing: '0.06em',
            animation: 'pulse 1.4s infinite',
          }}>
            ⟳ TRANSMITTING
          </span>
        )}
        {approvalPhase === 'ready' && (
          <span style={{
            marginLeft: 10, fontSize: 9, fontWeight: 700,
            background: 'rgba(53,231,183,0.06)', color: 'var(--signal)',
            border: '1px solid rgba(53,231,183,0.25)',
            borderRadius: 2, padding: '1px 7px', fontFamily: 'var(--font-mono)',
            letterSpacing: '0.06em',
          }}>
            READY
          </span>
        )}
      </h2>

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
          title="Submit the operator-reordered packet list"
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
      {sourcePlan && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <h3 style={{ margin: 0 }}>{isCustom ? 'Packet order — CUSTOM' : packetListLabel}</h3>
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
