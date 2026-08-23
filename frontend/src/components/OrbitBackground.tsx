/**
 * OrbitBackground — SVG background showing spacecraft position in comm window.
 * Feature 6: data-driven, z-index furthest back, pointer-events: none.
 * Position maps comm_window_remaining_s → fraction of orbit arc.
 */
import { useEffect, useRef } from 'react';

interface Props {
  commWindowRemainingS: number;
  totalWindowS: number;
}

export function OrbitBackground({ commWindowRemainingS, totalWindowS }: Props) {
  const dotRef = useRef<SVGCircleElement>(null);
  const pathRef = useRef<SVGPathElement>(null);

  // progress = 0 → start of pass, progress = 1 → end of pass / LOS
  const progress =
    totalWindowS > 0
      ? Math.max(0, Math.min(1, 1 - commWindowRemainingS / totalWindowS))
      : 1;

  const atLOS = commWindowRemainingS <= 0;

  // Parametric ellipse: centre (400, 220), rx=300, ry=90
  // The arc runs from left (progress=0) to right (progress=1) over the top.
  // θ ranges from π → 0 (upper semicircle, left to right).
  const cx = 400;
  const cy = 220;
  const rx = 300;
  const ry = 90;

  const theta = Math.PI - progress * Math.PI; // π down to 0
  const dotX = cx + rx * Math.cos(theta);
  const dotY = cy + ry * Math.sin(theta); // sin is positive → above centre

  useEffect(() => {
    const dot = dotRef.current;
    if (!dot) return;
    dot.style.transform = `translate(${dotX}px, ${dotY}px)`;
  }, [dotX, dotY]);

  return (
    <svg
      className="orbit-bg"
      viewBox="0 0 800 440"
      preserveAspectRatio="xMidYMid meet"
      aria-hidden="true"
    >
      {/* Earth / ground station — bottom centre */}
      <circle cx={400} cy={350} r={22} className="orbit-earth" />
      <circle cx={400} cy={350} r={34} className="orbit-earth-glow" />

      {/* Orbit path — upper semi-ellipse */}
      <path
        ref={pathRef}
        d={`M ${cx - rx} ${cy} A ${rx} ${ry} 0 0 1 ${cx + rx} ${cy}`}
        className="orbit-path"
      />

      {/* Travelled arc — highlights progress */}
      <path
        d={buildArc(cx, cy, rx, ry, progress)}
        className="orbit-arc-travelled"
      />

      {/* Spacecraft dot */}
      <circle
        ref={dotRef}
        r={5}
        className={'orbit-dot' + (atLOS ? ' orbit-dot--los' : '')}
        style={{ transform: `translate(${dotX}px, ${dotY}px)`, transition: 'transform 1.2s linear' }}
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
