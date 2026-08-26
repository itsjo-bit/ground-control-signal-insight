/**
 * SessionLogPanel — Phase 4.2F5
 *
 * Displays the session mission log — a timestamped record of actual
 * user actions and system transitions during this session.
 *
 * This is a SESSION UI LOG, not a cryptographically secure audit trail.
 * The authoritative ApprovalTrace remains backend-side.
 */

import type { SessionEvent } from '../experience/missionExperienceReducer';

// ── Helpers ───────────────────────────────────────────────────────────────────

const MONO: React.CSSProperties = {
  fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
};

const SANS: React.CSSProperties = {
  fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
};

function formatTs(ts: number): string {
  const d = new Date(ts);
  const hh = d.getHours().toString().padStart(2, '0');
  const mm = d.getMinutes().toString().padStart(2, '0');
  const ss = d.getSeconds().toString().padStart(2, '0');
  return `${hh}:${mm}:${ss}`;
}

function eventLabel(type: SessionEvent['type']): string {
  const labels: Record<SessionEvent['type'], string> = {
    asteria_initialized:      'ASTERIA-7 initialized',
    ingest_replay_completed:  'Ingest replay completed',
    ingest_replay_skipped:    'Ingest replay skipped',
    manual_mode_selected:     'Manual mode selected',
    ai_mode_selected:         'AI-assisted mode selected',
    manual_plan_assessed:     'Manual plan assessed',
    ai_analysis_requested:    'AI analysis requested',
    ai_analysis_completed:    'AI analysis completed',
    recommendation_approved:  'Recommendation approved',
    recommendation_modified:  'Plan modification started',
    recommendation_rejected:  'Recommendation rejected',
    plan_uplink_started:      'Plan uplink started',
    contact_acquired:         'Contact acquired',
    approval_executed:        'Approval executed',
    transmission_attempt:     'Transmission attempt',
    retransmission:           'Retransmission attempt',
    transmission_completed:   'Spacecraft transmission complete',
    signal_in_transit:        'Signal in transit to Earth',
    ground_reception_completed: 'Ground reception completed',
    scenario_reset:           'Scenario reset',
  };
  return labels[type] ?? type;
}

function eventColor(type: SessionEvent['type']): string {
  if (type === 'recommendation_rejected' || type.includes('failed')) return '#f87171';
  if (type === 'recommendation_approved' || type === 'ground_reception_completed') return '#34d399';
  if (type === 'ai_analysis_completed' || type === 'transmission_completed') return '#34d399';
  if (type === 'ai_analysis_requested' || type === 'plan_uplink_started' || type === 'contact_acquired') return '#6EA8FF';
  if (type === 'retransmission') return '#f59e0b';
  if (type === 'signal_in_transit') return '#6EA8FF';
  if (type === 'scenario_reset') return 'rgba(147,160,180,0.5)';
  return '#e2e8f4';
}

function eventIcon(type: SessionEvent['type']): string {
  if (type === 'recommendation_approved') return '✓';
  if (type === 'recommendation_rejected') return '✕';
  if (type === 'recommendation_modified') return '✎';
  if (type === 'retransmission') return '↻';
  if (type === 'transmission_completed') return '●';
  if (type === 'ground_reception_completed') return '⊙';
  if (type === 'signal_in_transit') return '→';
  if (type === 'scenario_reset') return '↺';
  if (type === 'plan_uplink_started') return '↑';
  if (type === 'contact_acquired') return '◉';
  return '·';
}

// ── Props ─────────────────────────────────────────────────────────────────────

interface SessionLogPanelProps {
  events: SessionEvent[];
}

// ── Component ─────────────────────────────────────────────────────────────────

export function SessionLogPanel({ events }: SessionLogPanelProps) {
  if (events.length === 0) {
    return (
      <div style={{ padding: '20px 0', textAlign: 'center', ...SANS, fontSize: 12, color: 'rgba(147,160,180,0.4)' }}>
        No session events recorded yet.
      </div>
    );
  }

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span style={{ ...MONO, fontSize: 9, color: 'rgba(147,160,180,0.45)', letterSpacing: '0.1em' }}>
          SESSION MISSION LOG
        </span>
        <span style={{ ...MONO, fontSize: 9, color: 'rgba(52,211,153,0.5)', background: 'rgba(52,211,153,0.07)', border: '1px solid rgba(52,211,153,0.2)', borderRadius: 2, padding: '1px 5px' }}>
          {events.length} EVENTS
        </span>
        <span style={{ ...SANS, fontSize: 10, color: 'rgba(147,160,180,0.3)', marginLeft: 'auto' }}>
          Session UI log · not a cryptographic audit trail
        </span>
      </div>

      {/* Event list — newest first */}
      <div style={{
        border: '1px solid rgba(46,58,79,0.6)',
        borderRadius: 6,
        overflow: 'hidden',
      }}>
        {[...events].reverse().map((ev, i) => {
          const color = eventColor(ev.type);
          const icon = eventIcon(ev.type);
          return (
            <div
              key={ev.id}
              style={{
                display: 'flex', alignItems: 'center', gap: 8,
                padding: '5px 10px',
                borderBottom: i < events.length - 1 ? '1px solid rgba(46,58,79,0.3)' : undefined,
                background: i === 0 ? 'rgba(52,211,153,0.03)' : 'transparent',
              }}
            >
              <span style={{ ...MONO, fontSize: 9, color: 'rgba(147,160,180,0.4)', minWidth: 54, flexShrink: 0 }}>
                {formatTs(ev.timestamp)}
              </span>
              <span style={{ ...MONO, fontSize: 10, color, flexShrink: 0, minWidth: 12 }}>
                {icon}
              </span>
              <span style={{ ...SANS, fontSize: 11, color, flex: 1 }}>
                {eventLabel(ev.type)}
              </span>
              {ev.detail && (
                <span style={{ ...MONO, fontSize: 9, color: 'rgba(147,160,180,0.4)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 140, flexShrink: 0 }}>
                  {ev.detail}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
