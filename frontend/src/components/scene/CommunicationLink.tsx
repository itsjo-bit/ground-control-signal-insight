/**
 * CommunicationLink — 3D visualization of the spacecraft-to-Earth communication path.
 *
 * Renders a tube-based beam with:
 * - Ghost/track line (always visible, responds to link health)
 * - Animated transmission pulse (event-driven, only during transmitting state)
 *
 * Link health status drives the color:
 *   GOOD    → cyan/teal
 *   WARNING → amber
 *   CRITICAL → red/degraded
 */
import { useRef, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';

export type LinkHealthStatus = 'good' | 'warning' | 'critical' | 'transmitting';

interface Props {
  startPos: THREE.Vector3;
  endPos: THREE.Vector3;
  linkStatus: LinkHealthStatus;
  transmitting?: boolean;
}

const STATUS_COLOR: Record<LinkHealthStatus, THREE.Color> = {
  good:        new THREE.Color(0x22ddaa),
  warning:     new THREE.Color(0xffaa33),
  critical:    new THREE.Color(0xff3344),
  transmitting: new THREE.Color(0x44ffcc),
};

export function CommunicationLink({ startPos, endPos, linkStatus, transmitting = false }: Props) {
  const pulseRef = useRef<THREE.Mesh>(null);
  const pulseProgress = useRef(0);

  const color = STATUS_COLOR[transmitting ? 'transmitting' : linkStatus];

  // Build a CatmullRomCurve3 with a slight arc for visual interest
  const curve = useMemo(() => {
    const mid = new THREE.Vector3().lerpVectors(startPos, endPos, 0.5);
    // Add a slight perpendicular offset to make the line arc slightly
    const dir = new THREE.Vector3().subVectors(endPos, startPos).normalize();
    const perp = new THREE.Vector3(-dir.y, dir.x, dir.z).normalize();
    mid.addScaledVector(perp, 2.0);
    return new THREE.CatmullRomCurve3([startPos.clone(), mid, endPos.clone()]);
  }, [startPos, endPos]);

  const tubeGeometry = useMemo(
    () => new THREE.TubeGeometry(curve, 32, 0.06, 5, false),
    [curve],
  );

  // Points along the curve for a simpler ghost line (cheaper)
  const linePoints = useMemo(() => curve.getPoints(80), [curve]);

  // Animate the transmission pulse along the curve
  useFrame((_state, delta) => {
    if (!pulseRef.current) return;
    if (transmitting) {
      pulseProgress.current = (pulseProgress.current + delta * 0.35) % 1.0;
    } else {
      // Reset smoothly
      if (pulseProgress.current > 0) {
        pulseProgress.current = Math.max(0, pulseProgress.current - delta * 0.5);
      }
    }
    const t = pulseProgress.current;
    const pulsePos = curve.getPoint(t);
    pulseRef.current.position.copy(pulsePos);
    pulseRef.current.visible = transmitting || pulseProgress.current > 0;
  });

  return (
    <group>
      {/* Ghost track line — always visible */}
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
          color={color}
          transparent
          opacity={0.18}
          linewidth={1}
        />
      </line>

      {/* Tube beam — slightly more visible */}
      <mesh geometry={tubeGeometry}>
        <meshBasicMaterial
          color={color}
          transparent
          opacity={transmitting ? 0.45 : 0.15}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>

      {/* Moving pulse sphere */}
      <mesh ref={pulseRef} visible={transmitting}>
        <sphereGeometry args={[0.3, 8, 6]} />
        <meshBasicMaterial
          color={color}
          transparent
          opacity={0.9}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </mesh>

      {/* Pulse glow halo */}
      <mesh ref={undefined} visible={transmitting}>
        {/* Handled through the main pulse above */}
      </mesh>
    </group>
  );
}
