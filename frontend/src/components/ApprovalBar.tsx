import { useState } from 'react';
import { approvePlan } from '../api/client';
import type { ApproveResponse } from '../types/domain';

interface Props {
  /** plan_id of the AI-recommended plan, or null when no recommendation is available. */
  recommendedPlanId: string | null;
  onApproved: (result: ApproveResponse) => void;
}

export function ApprovalBar({ recommendedPlanId, onApproved }: Props) {
  const [notes, setNotes] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [overridePlanId, setOverridePlanId] = useState('');

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
  async function handleApprove(planId: string) {
    setLoading(true);
    setError(null);
    try {
      const result = await approvePlan(planId, notes);
      onApproved(result);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="approval-bar">
      <h2>Approval</h2>
      <label>
        Operator notes:&nbsp;
        <input
          type="text"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Optional notes for the record…"
          style={{ width: '40%' }}
        />
      </label>
      &nbsp;
      <button
        onClick={() => handleApprove(recommendedPlanId)}
        disabled={loading}
        style={{ background: '#22c55e', color: '#fff', fontWeight: 700, padding: '4px 16px' }}
      >
        ✓ Approve ({recommendedPlanId})
      </button>
      &nbsp;
      <input
        type="text"
        value={overridePlanId}
        onChange={(e) => setOverridePlanId(e.target.value)}
        placeholder="Override plan ID…"
        style={{ width: '200px' }}
      />
      <button
        onClick={() => overridePlanId && handleApprove(overridePlanId)}
        disabled={loading || !overridePlanId}
        style={{ background: '#f97316', color: '#fff', fontWeight: 700, padding: '4px 16px' }}
      >
        ⚠ Override
      </button>
      {loading && <span> Submitting…</span>}
      {error && <span style={{ color: '#ef4444' }}> Error: {error}</span>}
    </section>
  );
}
