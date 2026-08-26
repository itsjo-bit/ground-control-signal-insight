/**
 * CommunicationLink — Phase 4.2F4 upgrade.
 *
 * Renders a tube-based beam with:
 * - Ghost/track line (always visible, responds to link health)
 * - Animated transmission pulse (event-driven, only during transmitting state)
 *
 * F4 changes:
 * - direction prop: earth_to_spacecraft (uplink) | spacecraft_to_earth (downlink)
 * - activePulse prop: actual attempt event state
 * - Outcome icons: ✓ delivered, ✕ failed, ↻ retry, ⊘ deferred
 * - No fake random pulse emission
 * - prefers-reduced-motion: no animation, show state only via color
 */
import { useRef, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import { Html } from '@react-three/drei';
import * as THREE from 'three';

export type LinkHealthStatus = 'good' | 'warning' | 'critical' | 'transmitting';

/** Direction of the active pulse. */
export type LinkDirection = 'earth_to_spacecraft' | 'spacecraft_to_earth' | 'idle';

/** Outcome of the current/last attempt. */
export type PulseOutcome = 'success' | 'failure' | 'retry' | 'deferred' | 'pending';

export interface ActivePulse {
  packetId: string;
  attemptNumber: number;
  /** 0..1 progress along the curve. */
  progress: number;
  outcome: PulseOutcome;
}

interface Props {
  startPos: THREE.Vector3;  // spacecraft position
  endPos: THREE.Vector3;    // earth surface position
  linkStatus: LinkHealthStatus;
  transmitting?: boolean;
  direction?: LinkDirection;
  activePulse?: ActivePulse | null;
  reducedMotion?: boolean;
}

const STATUS_COLOR: Record<LinkHealthStatus, THREE.Color> = {
  good:         new THREE.Color(0x22ddaa),
  warning:      new THREE.Color(0xffaa33),
  critical:     new THREE.Color(0xff3344),
  transmitting: new THREE.Color(0x44ffcc),
};

const OUTCOME_COLOR: Record<PulseOutcome, THREE.Color> = {
  success:  new THREE.Color(0x34d399),
  failure:  new THREE.Color(0xf87171),
  retry:    new THREE.Color(0xf59e0b),
  deferred: new THREE.Color(0x6EA8FF),
  pending:  new THREE.Color(0x44ffcc),
};

const OUTCOME_ICON: Record<PulseOutcome, string> = {
  success:  '✓',
  failure:  '✕',
  retry:    '↻',
  deferred: '⊘',
  pending:  '●',
};

export function CommunicationLink({
  startPos,
  endPos,
  linkStatus,
  transmitting = false,
  direction = 'idle',
  activePulse = null,
  reducedMotion = false,
}: Props) {
  const pulseRef = useRef<THREE.Mesh>(null);
  const pulseProgress = useRef(0);
  const autoProgress = useRef(0); // fallback for generic transmitting without pulse data

  const baseColor = STATUS_COLOR[transmitting ? 'transmitting' : linkStatus];
  const pulseColor = activePulse ? OUTCOME_COLOR[activePulse.outcome] : baseColor;

  // Determine pulse direction: uplink = earth→spacecraft, downlink = spacecraft→earth
  const isUplink = direction === 'earth_to_spacecraft';
  // For uplink: pulse goes from endPos (earth) to startPos (spacecraft)
  // For downlink: pulse goes from startPos (spacecraft) to endPos (earth)

  // Build a CatmullRomCurve3 with a slight arc
  const curve = useMemo(() => {
    const mid = new THREE.Vector3().lerpVectors(startPos, endPos, 0.5);
    const dir = new THREE.Vector3().subVectors(endPos, startPos).normalize();
    const perp = new THREE.Vector3(-dir.y, dir.x, dir.z).normalize();
    mid.addScaledVector(perp, 2.0);
    return new THREE.CatmullRomCurve3([startPos.clone(), mid, endPos.clone()]);
  }, [startPos, endPos]);

  // Uplink curve goes earth→spacecraft
  const uplinkCurve = useMemo(() => {
    const mid = new THREE.Vector3().lerpVectors(endPos, startPos, 0.5);
    const dir = new THREE.Vector3().subVectors(startPos, endPos).normalize();
    const perp = new THREE.Vector3(-dir.y, dir.x, dir.z).normalize();
    mid.addScaledVector(perp, 2.0);
    return new THREE.CatmullRomCurve3([endPos.clone(), mid, startPos.clone()]);
  }, [startPos, endPos]);

  const activeCurve = isUplink ? uplinkCurve : curve;

  const tubeGeometry = useMemo(
    () => new THREE.TubeGeometry(curve, 32, 0.06, 5, false),
    [curve],
  );
  const linePoints = useMemo(() => curve.getPoints(80), [curve]);

  // Animate the pulse
  useFrame((_state, delta) => {
    if (!pulseRef.current) return;
    if (reducedMotion) {
      // No animation — show static position based on actual pulse.progress
      if (activePulse) {
        const pos = activeCurve.getPoint(activePulse.progress);
        pulseRef.current.position.copy(pos);
        pulseRef.current.visible = true;
      } else {
        pulseRef.current.visible = false;
      }
      return;
    }

    if (activePulse) {
      // Drive pulse position from actual progress
      pulseProgress.current = activePulse.progress;
    } else if (transmitting) {
      // Generic transmitting — auto-animate
      autoProgress.current = (autoProgress.current + delta * 0.35) % 1.0;
      pulseProgress.current = autoProgress.current;
    } else {
      pulseRef.current.visible = false;
      autoProgress.current = 0;
      pulseProgress.current = 0;
      return;
    }

    const pulsePos = activeCurve.getPoint(pulseProgress.current);
    pulseRef.current.position.copy(pulsePos);
    pulseRef.current.visible = true;
  });

  const showLabel = activePulse && activePulse.outcome !== 'pending';

  return (
    <group>
      {/* Ghost track line */}
      <line>
        <bufferGeometry>
          <bufferAttribute
            attach="attributes-position"
            array={new Float32Array(linePoints.flatMap((p) => [p.x, p.y, p.z]))}
            count={linePoints.length}
            itemSize={3}
          />
        </bufferGeometry>
        <lineBasicMaterial
          color={baseColor}
          transparent
          opacity={0.18}
          linewidth={1}
        />
      </line>

      {/* Tube beam */}
      <mesh geometry={tubeGeometry}>
        <meshBasicMaterial
          color={baseColor}
          transparent
          opacity={transmitting ? 0.45 : 0.15}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>

      {/* Moving pulse sphere */}
      <mesh ref={pulseRef} visible={false}>
        <sphereGeometry args={[0.3, 8, 6]} />
        <meshBasicMaterial
          color={pulseColor}
          transparent
          opacity={0.9}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </mesh>

      {/* Outcome label — shown at pulse midpoint */}
      {showLabel && activePulse && !reducedMotion && (
        <Html
          position={activeCurve.getPoint(0.5).toArray()}
          style={{ pointerEvents: 'none' }}
        >
          <div style={{
            fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
            fontSize: 10, fontWeight: 700,
            color: activePulse.outcome === 'success' ? '#34d399'
              : activePulse.outcome === 'failure' ? '#f87171'
              : activePulse.outcome === 'retry' ? '#f59e0b' : '#6EA8FF',
            background: 'rgba(8,12,22,0.85)',
            border: '1px solid rgba(46,58,79,0.7)',
            borderRadius: 3, padding: '2px 6px',
            whiteSpace: 'nowrap',
          }}>
            {OUTCOME_ICON[activePulse.outcome]} {activePulse.packetId.slice(0, 16)}
            {activePulse.attemptNumber > 1 && ` ↻${activePulse.attemptNumber}`}
          </div>
        </Html>
      )}
    </group>
  );
}
