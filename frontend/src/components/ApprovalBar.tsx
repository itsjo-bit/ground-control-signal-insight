import { useState } from 'react';
import { approvePlan } from '../api/client';
import type { ApproveResponse } from '../types/domain';

interface Props {
  recommendedPlanId: string;
  onApproved: (result: ApproveResponse) => void;
}

export function ApprovalBar({ recommendedPlanId, onApproved }: Props) {
  const [notes, setNotes] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [overridePlanId, setOverridePlanId] = useState('');

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
