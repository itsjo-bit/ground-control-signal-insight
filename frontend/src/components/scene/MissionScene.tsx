/**
 * MissionScene — the main React Three Fiber scene.
 *
 * V3.1 changes:
 * - Increased Earth/spacecraft spatial separation
 * - Fixed camera presets: both camera.position AND OrbitControls.target update
 * - SPACECRAFT preset now correctly targets the spacecraft (not Earth)
 * - Smooth lerp transitions for both position and target
 *
 * Composition:
 *   - Deep space environment (dark background)
 *   - Starfield
 *   - Sun directional light
 *   - Earth (prominent center-left)
 *   - Spacecraft (far right, clearly separated)
 *   - CommunicationLink between them
 *   - HTML overlays for distance and status labels
 */
import { useRef } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import { Html, OrbitControls } from '@react-three/drei';
import * as THREE from 'three';
import { Starfield } from './Starfield';
import { Earth } from './Earth';
import { Spacecraft } from './Spacecraft';
import { CommunicationLink, type LinkHealthStatus } from './CommunicationLink';
import type { LinkState, MissionState } from '../../types/domain';
import type { ApprovalPhase } from '../ApprovalBar';
import { presentationLinkStatus, type PresentationLinkStatus } from '../../experience/linkPresentation';

// ── Layout constants ──────────────────────────────────────────────────────────
// V3.1: Increased separation between Earth and spacecraft for better visual
// communication of "deep space distance". Real mission distance data unchanged.
const EARTH_POS = new THREE.Vector3(-18, 0, 0);
const SPACECRAFT_POS = new THREE.Vector3(30, 4, -6);
const EARTH_RADIUS = 8;

// ── Camera presets ─────────────────────────────────────────────────────────────
// Each preset defines BOTH a camera position AND an orbit controls target.
// This is critical — only updating camera.position while leaving OrbitControls
// target unchanged causes the camera to orbit around the wrong point.

export const CAMERA_PRESETS = {
  default:    { pos: new THREE.Vector3(4, 10, 62),  target: new THREE.Vector3(6, 1, 0) },
  earth:      { pos: new THREE.Vector3(-20, 6, 34),  target: EARTH_POS.clone() },
  spacecraft: { pos: new THREE.Vector3(32, 8, 24),   target: SPACECRAFT_POS.clone() },
  link:       { pos: new THREE.Vector3(6, 16, 58),   target: new THREE.Vector3(6, 2, -3) },
} as const;

export type CameraPreset = keyof typeof CAMERA_PRESETS;

// ── Link health derivation using production presentation helper ───────────────

function deriveLinkStatus(
  linkState: LinkState | null,
  approvalPhase: ApprovalPhase,
): LinkHealthStatus {
  if (approvalPhase === 'transmitting') return 'transmitting';
  if (!linkState) return 'good';
  const status: PresentationLinkStatus = presentationLinkStatus(linkState);
  if (status === 'CRITICAL') return 'critical';
  if (status === 'DEGRADED') return 'warning';
  return 'good';
}

// ── Format distance for overlay ──────────────────────────────────────────────

function formatDistValue(km: number | null): string {
  if (km === null) return '—';
  if (km >= 1_000_000) return `${(km / 1_000_000).toFixed(1)}`;
  if (km >= 1_000)     return `${(km / 1_000).toFixed(1)}`;
  return `${km.toFixed(0)}`;
}

function formatDistUnit(km: number | null): string {
  if (km === null) return '';
  if (km >= 1_000_000) return 'MILLION KM';
  if (km >= 1_000)     return 'THOUSAND KM';
  return 'KM';
}

// ── Scene component ───────────────────────────────────────────────────────────

interface Props {
  linkState: LinkState | null;
  missionState: MissionState | null;
  distanceKm: number | null;
  approvalPhase: ApprovalPhase;
  cameraTarget?: CameraPreset;
  onCameraAnimated?: () => void;
  showStarfield?: boolean;
  showLabels?: boolean;
  showCommLink?: boolean;
  smoothCamera?: boolean;
  /** Current active transmission pulse — drives 3D animation. */
  activePulse?: import('./CommunicationLink').ActivePulse | null;
  /** Direction of active pulse. */
  pulseDirection?: import('./CommunicationLink').LinkDirection;
}

function SceneContent({
  linkState,
  missionState,
  distanceKm,
  approvalPhase,
  cameraTarget = 'default',
  onCameraAnimated,
  showStarfield = true,
  showLabels = true,
  showCommLink = true,
  smoothCamera = true,
  activePulse = null,
  pulseDirection = 'idle',
}: Props) {
  const { camera } = useThree();
  const orbitRef = useRef<React.ComponentRef<typeof OrbitControls>>(null);

  // Animation state
  const targetCamPos = useRef(new THREE.Vector3());
  const targetOrbitTarget = useRef(new THREE.Vector3());
  const animating = useRef(false);
  const prevPreset = useRef<CameraPreset>('default');

  const linkStatus = deriveLinkStatus(linkState, approvalPhase);
  const isTransmitting = approvalPhase === 'transmitting';

  // Camera animation — lerps BOTH camera position AND orbit controls target
  useFrame((_state, delta) => {
    if (prevPreset.current !== cameraTarget) {
      prevPreset.current = cameraTarget;
      const preset = CAMERA_PRESETS[cameraTarget];
      targetCamPos.current.copy(preset.pos);
      targetOrbitTarget.current.copy(preset.target);
      animating.current = true;
    }

    if (animating.current && orbitRef.current) {
      if (!smoothCamera) {
        // Snap immediately when smooth camera is disabled
        camera.position.copy(targetCamPos.current);
        orbitRef.current.target.copy(targetOrbitTarget.current);
        orbitRef.current.update();
        animating.current = false;
        onCameraAnimated?.();
        return;
      }

      const speed = delta * 2.8;

      // Lerp camera position
      camera.position.lerp(targetCamPos.current, speed);

      // Lerp orbit controls target (the look-at point)
      orbitRef.current.target.lerp(targetOrbitTarget.current, speed);
      orbitRef.current.update();

      // Check convergence on position
      if (
        camera.position.distanceTo(targetCamPos.current) < 0.4 &&
        orbitRef.current.target.distanceTo(targetOrbitTarget.current) < 0.2
      ) {
        animating.current = false;
        onCameraAnimated?.();
      }
    }
  });

  const distValue = formatDistValue(distanceKm);
  const distUnit  = formatDistUnit(distanceKm);
  const linkColor = {
    good:        '#22ddaa',
    warning:     '#ffaa33',
    critical:    '#ff4455',
    transmitting: '#44ffcc',
  }[linkStatus];

  // Communication link endpoints — derived from layout constants
  const linkStart = SPACECRAFT_POS;
  const linkEnd = new THREE.Vector3(EARTH_POS.x + EARTH_RADIUS * 0.9, EARTH_POS.y + 1, EARTH_POS.z);
  const linkMid = new THREE.Vector3(
    (linkStart.x + linkEnd.x) / 2,
    (linkStart.y + linkEnd.y) / 2 + 5,
    (linkStart.z + linkEnd.z) / 2,
  );

  return (
    <>
      {/* Orbit controls — initial target near scene center */}
      <OrbitControls
        ref={orbitRef}
        enableDamping
        dampingFactor={0.06}
        minDistance={18}
        maxDistance={140}
        maxPolarAngle={Math.PI * 0.85}
        target={[6, 1, 0]}
      />

      {/* Lighting */}
      <ambientLight intensity={0.07} color={new THREE.Color(0x0e1a33)} />
      <directionalLight
        position={[100, 50, 70]}
        intensity={1.8}
        color={new THREE.Color(0xfff0e0)}
        castShadow={false}
      />
      {/* Subtle fill light from the opposite/dark side — simulates space ambient */}
      <directionalLight
        position={[-70, -25, -50]}
        intensity={0.10}
        color={new THREE.Color(0x1a2a55)}
      />

      {/* Deep-space environment */}
      {showStarfield && <Starfield />}

      {/* Earth — center-left, recognizable globe */}
      <Earth position={EARTH_POS.toArray() as [number, number, number]} radius={EARTH_RADIUS} />

      {/* Spacecraft — clearly separated in deep space */}
      <Spacecraft
        position={SPACECRAFT_POS.toArray() as [number, number, number]}
        scale={0.95}
      />

      {/* Communication link beam — activePulse driven by authoritative attempt_events */}
      {showCommLink && (
        <CommunicationLink
          startPos={linkStart}
          endPos={linkEnd}
          linkStatus={linkStatus}
          transmitting={isTransmitting}
          activePulse={activePulse}
          direction={pulseDirection}
        />
      )}

      {/* Distance overlay — floats above the link midpoint */}
      {showLabels && (
        <Html
          position={[linkMid.x, linkMid.y, linkMid.z]}
          center
          distanceFactor={65}
          occlude={false}
          style={{ pointerEvents: 'none', userSelect: 'none' }}
        >
          <div style={{
            display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2,
          }}>
            <div style={{
              fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
              fontSize: 9,
              color: linkColor,
              letterSpacing: '0.1em',
              textTransform: 'uppercase',
              opacity: 0.7,
            }}>
              Distance
            </div>
            <div style={{
              fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
              fontSize: 14,
              fontWeight: 700,
              color: '#cce8ff',
              letterSpacing: '0.03em',
            }}>
              {distValue}
            </div>
            {distUnit && (
              <div style={{
                fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
                fontSize: 9,
                fontWeight: 600,
                color: '#cce8ff',
                letterSpacing: '0.08em',
                whiteSpace: 'nowrap',
                opacity: 0.85,
              }}>
                {distUnit}
              </div>
            )}
            {/* NOT TO SCALE label — visual spacing is presentation only */}
            <div
              data-testid="not-to-scale-label"
              style={{
                fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
                fontSize: 7,
                color: 'rgba(180,200,255,0.45)',
                letterSpacing: '0.12em',
                textTransform: 'uppercase',
                marginTop: 1,
              }}
            >
              visual spacing not to scale
            </div>
            {linkStatus !== 'good' && (
              <div style={{
                fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
                fontSize: 8,
                color: linkColor,
                letterSpacing: '0.08em',
                textTransform: 'uppercase',
                marginTop: 1,
              }}>
                {isTransmitting ? '⟳ Transmitting' : linkStatus === 'warning' ? '⚠ Link Degraded' : '✕ Link Critical'}
              </div>
            )}
          </div>
        </Html>
      )}

      {/* Spacecraft label */}
      {showLabels && (
        <Html
          position={[SPACECRAFT_POS.x, SPACECRAFT_POS.y + 3.2, SPACECRAFT_POS.z]}
          center
          distanceFactor={65}
          occlude={false}
          style={{ pointerEvents: 'none', userSelect: 'none' }}
        >
          <div style={{
            fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
            fontSize: 9,
            color: 'rgba(180,220,255,0.6)',
            letterSpacing: '0.12em',
            textTransform: 'uppercase',
            textAlign: 'center',
            whiteSpace: 'nowrap',
          }}>
            {missionState?.mission_id ?? 'SPACECRAFT'}
          </div>
        </Html>
      )}

      {/* Earth label */}
      {showLabels && (
        <Html
          position={[EARTH_POS.x, EARTH_POS.y - EARTH_RADIUS - 2.5, EARTH_POS.z]}
          center
          distanceFactor={65}
          occlude={false}
          style={{ pointerEvents: 'none', userSelect: 'none' }}
        >
          <div style={{
            fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
            fontSize: 9,
            color: 'rgba(120,180,255,0.6)',
            letterSpacing: '0.12em',
            textTransform: 'uppercase',
          }}>
            Earth / Ground Station
          </div>
        </Html>
      )}
    </>
  );
}

// Export with ref forwarding — parent wraps in <Canvas>
export { SceneContent as MissionScene };
