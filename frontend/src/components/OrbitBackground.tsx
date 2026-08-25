/**
 * OrbitBackground — SVG background showing spacecraft position in comm window.
 * Phase 2E-D2: adds a signal beam from spacecraft to Earth when distance_km is known.
 *   - Beam line drawn from spacecraft dot to Earth circle.
 *   - Animated dash-offset simulates a signal propagating along the path.
 *   - Distance label rendered near the midpoint of the beam.
 * Phase 2E-D4: beam pulse is gated by approvalPhase.
 *   - Pulse only active during 'transmitting'.
 *   - Spacecraft dot changes appearance at 'complete' based on simulation outcome.
 * Decorative background; z-index furthest back; pointer-events: none.
 */
import { useMemo } from 'react';
import type { ApprovalPhase } from './ApprovalBar';
import type { SimulationResult } from '../types/domain';

interface Props {
  commWindowRemainingS: number;
  totalWindowS: number;
  /** Spacecraft distance from Earth in km — null for legacy scenarios. */
  distanceKm: number | null;
  /** Phase 2E-D4: current operator approval workflow phase. */
  approvalPhase: ApprovalPhase;
  /** Phase 2E-D4: simulation result after transmission; null until complete. */
  simulationResult: SimulationResult | null;
}

/** Format a distance in km into a human-readable label (AU at deep-space ranges). */
function formatDistance(km: number): string {
  if (km >= 1_000_000) {
    return `${(km / 149_597_870.7).toFixed(3)} AU`;
  }
  if (km >= 10_000) {
    return `${(km / 1_000).toFixed(0)} kkm`;
  }
  return `${km.toFixed(0)} km`;
}

// Orbit ellipse geometry constants — shared between arc, dot, and beam.
const CX = 400;
const CY = 220;
const RX = 300;
const RY = 90;

// Earth circle centre.
const EARTH_CX = 400;
const EARTH_CY = 350;

/**
 * Derive the completion visual state from the simulation result.
 *   'success'  — at least one delivered packet and no failures
 *   'warning'  — at least one failed packet
 *   'neutral'  — result is null or no packets in either set
 */
function deriveCompletionState(result: SimulationResult | null): 'success' | 'warning' | 'neutral' {
  if (result === null) return 'neutral';
  if (result.failed_packets.length > 0) return 'warning';
  if (result.delivered_packets.length > 0) return 'success';
  return 'neutral';
}

export function OrbitBackground({ commWindowRemainingS, totalWindowS, distanceKm, approvalPhase, simulationResult }: Props) {
  // progress = 0 → start of pass, progress = 1 → end of pass / LOS
  const progress =
    totalWindowS > 0
      ? Math.max(0, Math.min(1, 1 - commWindowRemainingS / totalWindowS))
      : 1;

  const atLOS = commWindowRemainingS <= 0;

  const theta = Math.PI - progress * Math.PI; // π down to 0
  const dotX = CX + RX * Math.cos(theta);
  const dotY = CY + RY * Math.sin(theta);

  // Beam geometry: straight line from spacecraft dot to Earth centre.
  const beamDx = EARTH_CX - dotX;
  const beamDy = EARTH_CY - dotY;
  const beamLen = Math.sqrt(beamDx * beamDx + beamDy * beamDy);

  // Mid-point of the beam for the distance label.
  const labelX = dotX + beamDx * 0.48;
  const labelY = dotY + beamDy * 0.48;

  // Shorten label line so it doesn't overlap the Earth glow circle.
  const EARTH_RADIUS_MARGIN = 36;
  const fraction = beamLen > 0 ? Math.max(0, (beamLen - EARTH_RADIUS_MARGIN) / beamLen) : 1;
  const beamEndX = dotX + beamDx * fraction;
  const beamEndY = dotY + beamDy * fraction;

  // Dash pattern for the animated signal pulse.
  // We want a dash segment that travels along the beam.
  const DASH = 18;
  const GAP = beamLen > 0 ? beamLen - DASH : 120;

  // Perpendicular offset for the distance label so it doesn't sit on the line.
  const perpScale = beamLen > 0 ? 1 / beamLen : 0;
  const perpX = -beamDy * perpScale * 12; // 12px perpendicular offset
  const perpY = beamDx * perpScale * 12;

  const hasBeam = distanceKm !== null && distanceKm > 0 && !atLOS;

  // Phase 2E-D4: animate the pulse only while actively transmitting.
  const pulseActive = approvalPhase === 'transmitting';

  // Phase 2E-D4: derive completion dot state when phase is 'complete'.
  const completionState = approvalPhase === 'complete' ? deriveCompletionState(simulationResult) : null;

  const distLabel = useMemo(
    () => (distanceKm !== null ? formatDistance(distanceKm) : null),
    [distanceKm],
  );

  return (
    <svg
      className="orbit-bg"
      viewBox="0 0 800 440"
      preserveAspectRatio="xMidYMid meet"
      aria-hidden="true"
    >
      {/* ── Animated beam definitions ── */}
      {/* Phase 2E-D4: keyframe and class emitted only when beam exists.
          The 'signal-beam-pulse' class is only applied when pulseActive is true,
          so the animation keyframe is harmless when the class is absent.
          Duration is purely SVG geometry: max(0.8, beamLen/300) — not physics-derived. */}
      {hasBeam && (
        <defs>
          <style>{`
            @keyframes signal-travel {
              from { stroke-dashoffset: ${(GAP + DASH).toFixed(1)}; }
              to   { stroke-dashoffset: 0; }
            }
            .signal-beam-pulse {
              animation: signal-travel ${Math.max(0.8, (beamLen / 300)).toFixed(2)}s linear infinite;
            }
          `}</style>
        </defs>
      )}

      {/* ── Earth / ground station — bottom centre ── */}
      <circle cx={EARTH_CX} cy={EARTH_CY} r={22} className="orbit-earth" />
      <circle cx={EARTH_CX} cy={EARTH_CY} r={34} className="orbit-earth-glow" />

      {/* ── Signal beam: ghost track ── */}
      {hasBeam && (
        <line
          x1={dotX} y1={dotY}
          x2={beamEndX} y2={beamEndY}
          className="orbit-beam-track"
        />
      )}

      {/* ── Signal beam: animated pulse (Phase 2E-D4: only while transmitting) ── */}
      {hasBeam && pulseActive && (
        <line
          x1={dotX} y1={dotY}
          x2={beamEndX} y2={beamEndY}
          className="orbit-beam-pulse signal-beam-pulse"
          strokeDasharray={`${DASH} ${GAP.toFixed(1)}`}
        />
      )}

      {/* ── Distance label ── */}
      {hasBeam && distLabel && (
        <text
          x={labelX + perpX}
          y={labelY + perpY}
          className="orbit-beam-label"
          textAnchor="middle"
          dominantBaseline="middle"
        >
          {distLabel}
        </text>
      )}

      {/* ── Orbit path — upper semi-ellipse ── */}
      <path
        d={`M ${CX - RX} ${CY} A ${RX} ${RY} 0 0 1 ${CX + RX} ${CY}`}
        className="orbit-path"
      />

      {/* ── Travelled arc — highlights progress ── */}
      <path
        d={buildArc(CX, CY, RX, RY, progress)}
        className="orbit-arc-travelled"
      />

      {/* ── Spacecraft dot (Phase 2E-D4: completion state variants) ── */}
      <circle
        r={5}
        className={
          'orbit-dot' +
          (atLOS ? ' orbit-dot--los' : '') +
          (completionState === 'success' ? ' orbit-dot--complete-success' : '') +
          (completionState === 'warning' ? ' orbit-dot--complete-warning' : '') +
          (completionState === 'neutral' ? ' orbit-dot--complete-neutral' : '')
        }
        style={{
          transform: `translate(${dotX}px, ${dotY}px)`,
          transition: 'transform 1.2s linear',
        }}
      />
    </svg>
  );
}

/** Build an SVG arc path for the portion already travelled (0..progress). */
function buildArc(
  cx: number,
  cy: number,
  rx: number,
  ry: number,
  progress: number,
): string {
  if (progress <= 0) return '';
  const startX = cx - rx;
  const startY = cy;
  const theta = Math.PI - progress * Math.PI;
  const endX = cx + rx * Math.cos(theta);
  const endY = cy + ry * Math.sin(theta);
  const largeArc = progress > 0.5 ? 1 : 0;
  return `M ${startX} ${startY} A ${rx} ${ry} 0 ${largeArc} 1 ${endX} ${endY}`;
}
