/**
 * SignalGeometryBlock — Phase 2E-C3-D
 *
 * Displays spacecraft-to-Earth communication geometry to the Mission Control
 * operator:
 *
 *   SPACECRAFT RANGE
 *   54.0 M km
 *
 *   SPACECRAFT ──────────────────────────── EARTH
 *
 *   ONE-WAY SIGNAL       ROUND TRIP
 *   180.1 s              360.2 s
 *   ≈ 3.0 min            ≈ 6.0 min
 *
 * Semantic notes (enforced by labels):
 * - propagation_delay_s is the physical signal travel time, NOT transmission time.
 * - round_trip_time_s is propagation RTT only, NOT an ACK/delivery guarantee.
 * - Actual data transmission duration = data_size / goodput (existing pipeline).
 *
 * When distanceKm is null (legacy scenario), renders a graceful unavailable state.
 * Values come from props only — this component never fetches /state itself.
 */

const DIM   = 'var(--text-dim,  #3d4a63)';
const MUTED = 'var(--text-muted, #6f83a3)';
const TEXT  = 'var(--text, #dce6f5)';
const SIGNAL_COLOR = 'var(--signal, #35e7b7)';

// ── Formatting helpers ────────────────────────────────────────────────────────

/** 54,000,000 → "54.0 M km" */
function formatDistanceKm(km: number): string {
  if (km >= 1_000_000) return `${(km / 1_000_000).toFixed(1)} M km`;
  if (km >= 1_000)     return `${(km / 1_000).toFixed(1)} k km`;
  return `${km.toFixed(0)} km`;
}

/** 180.12449 → "180.1 s" */
function formatSeconds(s: number): string {
  return `${s.toFixed(1)} s`;
}

/** 180.12449 → "≈ 3.0 min" */
function formatMinutes(s: number): string {
  return `≈ ${(s / 60).toFixed(1)} min`;
}

// ── Label row ────────────────────────────────────────────────────────────────

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontFamily: 'var(--font-mono)', fontSize: 9,
      color: DIM, textTransform: 'uppercase', letterSpacing: '0.08em',
      marginBottom: 3,
    }}>
      {children}
    </div>
  );
}

// ── Props ─────────────────────────────────────────────────────────────────────

export interface SignalGeometryBlockProps {
  distanceKm: number | null;
  propagationDelayS: number | null;
  roundTripTimeS: number | null;
}

// ── Component ─────────────────────────────────────────────────────────────────

export function SignalGeometryBlock({
  distanceKm,
  propagationDelayS,
  roundTripTimeS,
}: SignalGeometryBlockProps) {

  // ── Null / unavailable state ─────────────────────────────────────────────
  if (distanceKm === null || propagationDelayS === null || roundTripTimeS === null) {
    return (
      <div style={{ marginTop: 10 }}>
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 9,
          color: DIM, textTransform: 'uppercase', letterSpacing: '0.08em',
          marginBottom: 5,
        }}>
          Communication Geometry
        </div>
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 11,
          color: DIM, fontStyle: 'italic',
        }}>
          Not available for this scenario
        </div>
      </div>
    );
  }

  // ── Distance string ──────────────────────────────────────────────────────
  const distStr  = formatDistanceKm(distanceKm);
  const propStr  = formatSeconds(propagationDelayS);
  const propMin  = formatMinutes(propagationDelayS);
  const rttStr   = formatSeconds(roundTripTimeS);
  const rttMin   = formatMinutes(roundTripTimeS);

  // ── Link ruler width as fraction of distance relative to known reference ─
  // The ruler is purely decorative; it uses a fixed SVG width with a centred label.
  const rulerW = 160; // px — constrained to panel width

  return (
    <div style={{ marginTop: 10 }}>

      {/* ── Section header ───────────────────────────────────────────────── */}
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
        marginBottom: 6,
      }}>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 9,
          color: DIM, textTransform: 'uppercase', letterSpacing: '0.08em',
        }}>
          Comm Geometry
        </span>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 700,
          color: MUTED,
        }}>
          deep-space link
        </span>
      </div>

      {/* ── Spacecraft range ─────────────────────────────────────────────── */}
      <div style={{ marginBottom: 8 }}>
        <SectionLabel>Spacecraft Range</SectionLabel>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 18, fontWeight: 700,
          color: TEXT, letterSpacing: '-0.01em',
        }}>
          {distStr}
        </span>
      </div>

      {/* ── Ruler — SPACECRAFT ────── EARTH ─────────────────────────────── */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8,
        overflow: 'hidden',
      }}>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 600,
          color: MUTED, flexShrink: 0, letterSpacing: '0.06em',
        }}>
          SC
        </span>

        {/* SVG ruler */}
        <svg
          width={rulerW}
          height={14}
          viewBox={`0 0 ${rulerW} 14`}
          style={{ flex: 1, minWidth: 40, maxWidth: rulerW }}
          aria-hidden="true"
        >
          {/* Line */}
          <line
            x1={4} y1={7} x2={rulerW - 4} y2={7}
            stroke="rgba(53,231,183,0.35)" strokeWidth={1}
            strokeDasharray="3 4"
          />
          {/* Left tick */}
          <line x1={4} y1={3} x2={4} y2={11}
            stroke="rgba(53,231,183,0.5)" strokeWidth={1} />
          {/* Right tick */}
          <line x1={rulerW - 4} y1={3} x2={rulerW - 4} y2={11}
            stroke="rgba(53,231,183,0.5)" strokeWidth={1} />
          {/* Distance label */}
          <text
            x={rulerW / 2} y={7}
            textAnchor="middle" dominantBaseline="middle"
            fill="rgba(53,231,183,0.75)"
            fontSize={8}
            fontFamily="'IBM Plex Mono', ui-monospace, monospace"
            fontWeight={600}
          >
            {distStr}
          </text>
        </svg>

        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 600,
          color: MUTED, flexShrink: 0, letterSpacing: '0.06em',
        }}>
          GS
        </span>
      </div>

      {/* ── Timing pair ──────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>

        {/* One-way */}
        <div style={{
          flex: '1 1 0', minWidth: 80,
          background: 'rgba(53,231,183,0.04)',
          border: '1px solid rgba(53,231,183,0.14)',
          borderRadius: 4, padding: '7px 10px',
        }}>
          <SectionLabel>Signal Propagation Delay</SectionLabel>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 15, fontWeight: 700,
            color: SIGNAL_COLOR, marginBottom: 2, letterSpacing: '-0.01em',
          }}>
            {propStr}
          </div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 10, color: MUTED,
          }}>
            {propMin}
          </div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 9, color: DIM,
            marginTop: 4, lineHeight: 1.4,
          }}>
            Signal travel time only —<br />
            not transmission duration
          </div>
        </div>

        {/* Round-trip */}
        <div style={{
          flex: '1 1 0', minWidth: 80,
          background: 'rgba(124,158,255,0.04)',
          border: '1px solid rgba(124,158,255,0.14)',
          borderRadius: 4, padding: '7px 10px',
        }}>
          <SectionLabel>Round Trip</SectionLabel>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 15, fontWeight: 700,
            color: 'var(--ai, #7c9eff)', marginBottom: 2, letterSpacing: '-0.01em',
          }}>
            {rttStr}
          </div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 10, color: MUTED,
          }}>
            {rttMin}
          </div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 9, color: DIM,
            marginTop: 4, lineHeight: 1.4,
          }}>
            Propagation RTT only —<br />
            not ACK/delivery time
          </div>
        </div>

      </div>

    </div>
  );
}
