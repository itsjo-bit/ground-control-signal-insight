/**
 * CommunicationLink — Phase 5.1F upgrade.
 *
 * Renders a tube-based beam with:
 * - Ghost/track line (always visible, responds to link health)
 * - Animated transmission pulse (event-driven, only for authoritative attempt events)
 *
 * Phase 5.1F changes:
 * - ActivePulse uses isRetry + status instead of outcome='retry' (WORKSTREAM F)
 * - REMOVED generic auto-pulse fallback for transmitting=true, activePulse=null (WORKSTREAM I)
 *   → Only activePulse derived from an authoritative attempt_event creates a moving pulse
 *   → During CONTACT_WAIT (transmitting=true, no activePulse): beam/glow only, no packet pulse
 * - Visual policy documented by LinkVisualMode enum (WORKSTREAM I)
 * - prefers-reduced-motion: no animation, show state only via color
 *
 * LINK VISUAL POLICY (per phase):
 *   IDLE                  → beam/glow only, no pulse
 *   PLAN_UPLINK_VISUAL    → direction=earth_to_spacecraft, generic uplink pulse allowed
 *   CONTACT_ACQUISITION   → transmitting=true, activePulse=null → beam/glow only, NO packet pulse
 *   AUTHORITATIVE_DOWNLINK→ activePulse from attempt_event → authoritative data pulse
 *   SIGNAL_TRANSIT_VISUAL → no moving pulse (transit already happened)
 *   COMPLETE              → no pulse
 */
import { useRef, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import { Html } from '@react-three/drei';
import * as THREE from 'three';

export type LinkHealthStatus = 'good' | 'warning' | 'critical' | 'transmitting';

/** Direction of the active pulse. */
export type LinkDirection = 'earth_to_spacecraft' | 'spacecraft_to_earth' | 'idle';

/**
 * Phase 5.1F: ActivePulse uses isRetry + status (NOT outcome='retry').
 * Retry identity (isRetry) and outcome (status) are separate dimensions.
 * 'retry' is NOT a valid status — use isRetry=true + status='success'|'failure'.
 */
export interface ActivePulse {
  packetId: string;
  attemptNumber: number;
  /** True when this is a retry (attemptNumber > 1). Separate from outcome status. */
  isRetry: boolean;
  /** 0..1 progress along the curve. */
  progress: number;
  /** 'pending' while in flight; authoritative status on completion. Never 'retry'. */
  status: 'pending' | 'success' | 'failure';
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

/** Phase 5.1F: pulse color keyed on status (not PulseOutcome). */
const PULSE_STATUS_COLOR: Record<'pending' | 'success' | 'failure', THREE.Color> = {
  success:  new THREE.Color(0x34d399),
  failure:  new THREE.Color(0xf87171),
  pending:  new THREE.Color(0x44ffcc),
};

const STATUS_ICON: Record<'pending' | 'success' | 'failure', string> = {
  success: '✓',
  failure: '✕',
  pending: '●',
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
  // Phase 5.1F: uplink-direction generic animation progress (plan_uplink visual only)
  const uplinkAutoProgress = useRef(0);

  const baseColor = STATUS_COLOR[transmitting ? 'transmitting' : linkStatus];
  const pulseColor = activePulse ? PULSE_STATUS_COLOR[activePulse.status] : baseColor;

  // Determine pulse direction: uplink = earth→spacecraft, downlink = spacecraft→earth
  const isUplink = direction === 'earth_to_spacecraft';

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
      // AUTHORITATIVE_DOWNLINK: drive pulse from actual attempt_event progress
      pulseProgress.current = activePulse.progress;
      uplinkAutoProgress.current = 0;
      const pulsePos = activeCurve.getPoint(pulseProgress.current);
      pulseRef.current.position.copy(pulsePos);
      pulseRef.current.visible = true;
    } else if (isUplink && transmitting) {
      // PLAN_UPLINK_VISUAL: generic Earth→spacecraft presentation pulse is allowed.
      // This is clearly a presentation-only animation (direction = earth_to_spacecraft).
      // No fake packet ID, no authoritative data represented.
      uplinkAutoProgress.current = (uplinkAutoProgress.current + delta * 0.4) % 1.0;
      pulseProgress.current = uplinkAutoProgress.current;
      const pulsePos = activeCurve.getPoint(pulseProgress.current);
      pulseRef.current.position.copy(pulsePos);
      pulseRef.current.visible = true;
    } else {
      // Phase 5.1F (WORKSTREAM I): NO auto-pulse during CONTACT_ACQUISITION or other
      // phases where transmitting=true but no authoritative attempt_event is active.
      // The old generic auto-pulse was removed to avoid fake data packet visualization.
      pulseRef.current.visible = false;
      uplinkAutoProgress.current = 0;
      pulseProgress.current = 0;
    }
  });

  // Only show outcome label for non-pending authoritative pulses
  const showLabel = activePulse && activePulse.status !== 'pending';

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

      {/* Outcome label — shown at pulse midpoint for completed authoritative attempts */}
      {showLabel && activePulse && !reducedMotion && (
        <Html
          position={activeCurve.getPoint(0.5).toArray()}
          style={{ pointerEvents: 'none' }}
        >
          <div style={{
            fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
            fontSize: 10, fontWeight: 700,
            color: activePulse.status === 'success' ? '#34d399'
              : activePulse.status === 'failure' ? '#f87171'
              : '#6EA8FF',
            background: 'rgba(8,12,22,0.85)',
            border: '1px solid rgba(46,58,79,0.7)',
            borderRadius: 3, padding: '2px 6px',
            whiteSpace: 'nowrap',
          }}>
            {STATUS_ICON[activePulse.status]} {activePulse.packetId.slice(0, 16)}
            {activePulse.isRetry && ` ↻${activePulse.attemptNumber}`}
          </div>
        </Html>
      )}
    </group>
  );
}
