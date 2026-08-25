/**
 * Earth — genuine 3D globe rendered with Three.js SphereGeometry.
 *
 * V3.2: Uses the provided equirectangular Earth photograph as the surface
 * texture (NASA Blue Marble, stored in /public/earth-texture.jpg).
 *
 * Features:
 * - Real photo-quality Earth surface texture via TextureLoader
 * - Cloud layer (separate transparent sphere, slow independent rotation)
 * - Thin blue atmospheric glow using additive BackSide sphere
 * - Phong lighting for convincing day/night shading
 * - Slow continuous rotation
 *
 * The cloud texture is still procedurally generated (no external dependency)
 * since the main Earth image already includes most cloud coverage — the
 * procedural layer just adds a very subtle additional wisp effect.
 */
import { useRef, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import { useTexture } from '@react-three/drei';
import * as THREE from 'three';

// ── Procedural cloud overlay (very subtle — complements the baked-in clouds) ──

function seededRand(seed: number) {
  let s = seed;
  return () => {
    s ^= s << 13; s ^= s >> 17; s ^= s << 5;
    return (s >>> 0) / 4294967296;
  };
}

function generateCloudOverlay(size: number): THREE.CanvasTexture {
  const w = size;
  const h = size / 2;
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d')!;
  ctx.clearRect(0, 0, w, h);

  const rand = seededRand(0xcafebabe);
  // Very sparse extra cloud wisps — the main Earth photo already has clouds
  for (let i = 0; i < 28; i++) {
    const x = rand() * w;
    const y = h * 0.08 + rand() * h * 0.84;
    const rx = rand() * w * 0.055 + w * 0.015;
    const ry = rand() * h * 0.030 + h * 0.008;
    const opacity = 0.08 + rand() * 0.16;

    const g = ctx.createRadialGradient(x, y, 0, x, y, Math.max(rx, ry));
    g.addColorStop(0,   `rgba(240,245,255,${opacity})`);
    g.addColorStop(0.5, `rgba(230,240,255,${opacity * 0.4})`);
    g.addColorStop(1,   'rgba(220,235,255,0)');
    ctx.fillStyle = g;
    ctx.save();
    ctx.translate(x, y);
    ctx.scale(rx / Math.max(rx, ry), ry / Math.max(rx, ry));
    ctx.beginPath();
    ctx.arc(0, 0, Math.max(rx, ry), 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  return texture;
}

// ── Component ─────────────────────────────────────────────────────────────────

interface Props {
  position?: [number, number, number];
  radius?: number;
}

export function Earth({ position = [0, 0, 0], radius = 8 }: Props) {
  const earthRef = useRef<THREE.Mesh>(null);
  const cloudRef = useRef<THREE.Mesh>(null);
  const atmosphereRef = useRef<THREE.Mesh>(null);

  // Load the real Earth photograph from /public/earth-texture.jpg
  // useTexture suspends until the texture is ready (works inside <Suspense>)
  const earthTexture = useTexture('/earth-texture.jpg');

  // Ensure correct UV wrapping for equirectangular mapping
  useMemo(() => {
    earthTexture.wrapS = THREE.RepeatWrapping;
    earthTexture.wrapT = THREE.ClampToEdgeWrapping;
    earthTexture.colorSpace = THREE.SRGBColorSpace;
    earthTexture.needsUpdate = true;
  }, [earthTexture]);

  // Procedural cloud overlay (very sparse — complements baked-in clouds)
  const cloudTexture = useMemo(() => generateCloudOverlay(256), []);

  // Rotation: Earth slow, clouds slightly faster
  useFrame((_state, delta) => {
    if (earthRef.current) {
      earthRef.current.rotation.y += delta * 0.035;
    }
    if (cloudRef.current) {
      cloudRef.current.rotation.y += delta * 0.042;
    }
    if (atmosphereRef.current) {
      atmosphereRef.current.rotation.y += delta * 0.030;
    }
  });

  return (
    <group position={position}>
      {/* Earth sphere — MeshPhong gives convincing directional lighting */}
      <mesh ref={earthRef} castShadow receiveShadow>
        <sphereGeometry args={[radius, 72, 54]} />
        <meshPhongMaterial
          map={earthTexture}
          shininess={10}
          specular={new THREE.Color(0x08141e)}
        />
      </mesh>

      {/* Sparse procedural cloud wisp layer — very subtle, independent rotation */}
      <mesh ref={cloudRef} scale={1.010}>
        <sphereGeometry args={[radius, 48, 36]} />
        <meshPhongMaterial
          map={cloudTexture}
          transparent
          opacity={0.20}
          depthWrite={false}
          shininess={0}
        />
      </mesh>

      {/* Inner atmosphere — thin blue rim */}
      <mesh ref={atmosphereRef} scale={1.038}>
        <sphereGeometry args={[radius, 36, 28]} />
        <meshLambertMaterial
          color={new THREE.Color(0x1a44bb)}
          transparent
          opacity={0.09}
          side={THREE.BackSide}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>

      {/* Outer atmospheric halo — very faint limb glow */}
      <mesh scale={1.068}>
        <sphereGeometry args={[radius, 24, 18]} />
        <meshLambertMaterial
          color={new THREE.Color(0x0d2d99)}
          transparent
          opacity={0.038}
          side={THREE.BackSide}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>
    </group>
  );
}
