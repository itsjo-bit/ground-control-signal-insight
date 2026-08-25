/**
 * Starfield — procedural particle-based deep-space star background.
 * Uses a BufferGeometry with random positions distributed on a large sphere.
 * Performance: single draw call, instanced particles.
 */
import { useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';

const STAR_COUNT = 2000;
const SPHERE_RADIUS = 450;

export function Starfield() {
  const pointsRef = useRef<THREE.Points>(null);

  const [positions, sizes] = useMemo(() => {
    const pos = new Float32Array(STAR_COUNT * 3);
    const sz = new Float32Array(STAR_COUNT);
    for (let i = 0; i < STAR_COUNT; i++) {
      // Distribute on sphere using rejection sampling for uniform distribution
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      const r = SPHERE_RADIUS * (0.8 + Math.random() * 0.2);
      pos[i * 3]     = r * Math.sin(phi) * Math.cos(theta);
      pos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      pos[i * 3 + 2] = r * Math.cos(phi);
      sz[i] = Math.random() < 0.05 ? 2.5 : (0.5 + Math.random() * 1.2);
    }
    return [pos, sz];
  }, []);

  // Very subtle drift — barely noticeable, adds life without distraction
  useFrame((_state, delta) => {
    if (pointsRef.current) {
      pointsRef.current.rotation.y += delta * 0.001;
    }
  });

  return (
    <points ref={pointsRef}>
      <bufferGeometry>
        <bufferAttribute
          attach="attributes-position"
          array={positions}
          count={STAR_COUNT}
          itemSize={3}
        />
        <bufferAttribute
          attach="attributes-size"
          array={sizes}
          count={STAR_COUNT}
          itemSize={1}
        />
      </bufferGeometry>
      <pointsMaterial
        color="#ccd6f0"
        size={1.2}
        sizeAttenuation
        transparent
        opacity={0.75}
        fog={false}
      />
    </points>
  );
}
