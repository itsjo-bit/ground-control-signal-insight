/**
 * Spacecraft — procedural 3D spacecraft geometry built from Three.js primitives.
 *
 * Creates a recognizable spacecraft shape:
 * - Central body (main bus)
 * - Solar panels (flat wings extending from the body)
 * - High-gain antenna (parabolic dish)
 * - Propulsion module (cylindrical thruster at rear)
 * - Small instrument protrusions
 *
 * No external GLTF asset required.
 */
import { useRef, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';

interface Props {
  position?: [number, number, number];
  scale?: number;
}

export function Spacecraft({ position = [0, 0, 0], scale = 1.0 }: Props) {
  const groupRef = useRef<THREE.Group>(null);

  // Subtle slow tumble/wobble
  useFrame((_state, delta) => {
    if (groupRef.current) {
      groupRef.current.rotation.y += delta * 0.12;
      groupRef.current.rotation.x = Math.sin(_state.clock.elapsedTime * 0.2) * 0.04;
    }
  });

  const goldColor = useMemo(() => new THREE.Color(0xcc9922), []);
  const bodyColor = useMemo(() => new THREE.Color(0x778899), []);
  const darkColor = useMemo(() => new THREE.Color(0x334455), []);
  const dishColor = useMemo(() => new THREE.Color(0xaabbcc), []);
  const thrusterColor = useMemo(() => new THREE.Color(0x445566), []);
  const emissiveBlue = useMemo(() => new THREE.Color(0x003388), []);

  return (
    <group ref={groupRef} position={position} scale={scale}>

      {/* ── Main body (spacecraft bus) ── */}
      <mesh castShadow>
        <boxGeometry args={[0.9, 0.65, 1.4]} />
        <meshPhongMaterial color={bodyColor} shininess={60} specular={darkColor} />
      </mesh>

      {/* MLI (multi-layer insulation) gold-foil patches */}
      <mesh position={[0, 0.33, 0]} castShadow>
        <boxGeometry args={[0.88, 0.02, 1.38]} />
        <meshPhongMaterial color={goldColor} shininess={120} specular={new THREE.Color(0xffcc44)} />
      </mesh>
      <mesh position={[0, -0.33, 0]} castShadow>
        <boxGeometry args={[0.88, 0.02, 1.38]} />
        <meshPhongMaterial color={goldColor} shininess={120} specular={new THREE.Color(0xffcc44)} />
      </mesh>

      {/* ── Solar panels ── */}
      {/* Left panel */}
      <group position={[-2.0, 0, 0.1]}>
        <mesh castShadow>
          <boxGeometry args={[2.0, 0.04, 0.9]} />
          <meshPhongMaterial
            color={new THREE.Color(0x112244)}
            shininess={180}
            specular={new THREE.Color(0x3366cc)}
            emissive={emissiveBlue}
            emissiveIntensity={0.15}
          />
        </mesh>
        {/* Solar cell lines */}
        {[-0.35, 0, 0.35].map((x, i) => (
          <mesh key={i} position={[x, 0.021, 0]} castShadow>
            <boxGeometry args={[0.02, 0.005, 0.86]} />
            <meshBasicMaterial color={new THREE.Color(0x2244aa)} />
          </mesh>
        ))}
      </group>

      {/* Right panel */}
      <group position={[2.0, 0, 0.1]}>
        <mesh castShadow>
          <boxGeometry args={[2.0, 0.04, 0.9]} />
          <meshPhongMaterial
            color={new THREE.Color(0x112244)}
            shininess={180}
            specular={new THREE.Color(0x3366cc)}
            emissive={emissiveBlue}
            emissiveIntensity={0.15}
          />
        </mesh>
        {[-0.35, 0, 0.35].map((x, i) => (
          <mesh key={i} position={[x, 0.021, 0]} castShadow>
            <boxGeometry args={[0.02, 0.005, 0.86]} />
            <meshBasicMaterial color={new THREE.Color(0x2244aa)} />
          </mesh>
        ))}
      </group>

      {/* ── High-gain antenna (parabolic dish) ── */}
      <group position={[0, 0.38, -0.5]} rotation={[-0.3, 0, 0]}>
        {/* Dish ring */}
        <mesh castShadow>
          <torusGeometry args={[0.4, 0.025, 8, 32]} />
          <meshPhongMaterial color={dishColor} shininess={90} />
        </mesh>
        {/* Dish face (slightly concave cap) */}
        <mesh position={[0, 0.0, 0.0]} castShadow>
          <sphereGeometry args={[0.4, 16, 8, 0, Math.PI * 2, 0, Math.PI / 3]} />
          <meshPhongMaterial
            color={dishColor}
            shininess={200}
            specular={new THREE.Color(0x99bbdd)}
            side={THREE.DoubleSide}
          />
        </mesh>
        {/* Antenna stem */}
        <mesh position={[0, -0.3, 0]} castShadow>
          <cylinderGeometry args={[0.02, 0.02, 0.6, 6]} />
          <meshPhongMaterial color={darkColor} shininess={40} />
        </mesh>
      </group>

      {/* ── Low-gain omni antennas ── */}
      <mesh position={[0.0, 0.32, 0.7]} castShadow>
        <cylinderGeometry args={[0.012, 0.012, 0.55, 5]} />
        <meshPhongMaterial color={dishColor} shininess={40} />
      </mesh>
      <mesh position={[-0.2, -0.32, -0.6]} castShadow>
        <cylinderGeometry args={[0.012, 0.012, 0.4, 5]} />
        <meshPhongMaterial color={dishColor} shininess={40} />
      </mesh>

      {/* ── Propulsion module (main thruster) ── */}
      <group position={[0, 0, 0.72]}>
        <mesh castShadow>
          <cylinderGeometry args={[0.28, 0.35, 0.35, 12]} />
          <meshPhongMaterial color={thrusterColor} shininess={50} />
        </mesh>
        {/* Nozzle */}
        <mesh position={[0, 0, 0.22]} rotation={[Math.PI / 2, 0, 0]} castShadow>
          <cylinderGeometry args={[0.18, 0.28, 0.2, 10]} />
          <meshPhongMaterial color={new THREE.Color(0x223344)} shininess={30} />
        </mesh>
      </group>

      {/* ── Science instrument boom ── */}
      <mesh position={[-0.0, -0.32, -0.9]} castShadow>
        <cylinderGeometry args={[0.015, 0.015, 0.6, 5]} />
        <meshPhongMaterial color={bodyColor} shininess={60} />
      </mesh>
      <mesh position={[-0.0, -0.32, -1.22]} castShadow>
        <sphereGeometry args={[0.065, 8, 6]} />
        <meshPhongMaterial color={new THREE.Color(0x667788)} shininess={80} />
      </mesh>

    </group>
  );
}
