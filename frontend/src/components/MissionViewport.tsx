/**
 * MissionViewport — the Canvas container for the 3D mission visualization.
 *
 * Wraps React Three Fiber Canvas with:
 * - Proper camera setup
 * - WebGL error boundary / fallback
 * - Camera preset controls (overlaid HTML)
 * - Loading state
 *
 * This is the primary visual element of GCSI V3.
 */
import { Suspense, useState, useCallback } from 'react';
import { Canvas } from '@react-three/fiber';
import { MissionScene, CAMERA_PRESETS, type CameraPreset } from './scene/MissionScene';
import type { LinkState, MissionState } from '../types/domain';
import type { ApprovalPhase } from './ApprovalBar';
import { presentationLinkStatus } from '../experience/linkPresentation';

// ── WebGL error boundary ──────────────────────────────────────────────────────

import React from 'react';

interface ErrorBoundaryState { hasError: boolean }
class WebGLErrorBoundary extends React.Component<
  { children: React.ReactNode },
  ErrorBoundaryState
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }
  static getDerivedStateFromError() { return { hasError: true }; }
  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          height: '100%', gap: 12,
          fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
          background: '#060a12',
          color: 'rgba(150,180,220,0.5)',
        }}>
          <div style={{ fontSize: 13, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
            3D Viewport Unavailable
          </div>
          <div style={{ fontSize: 10, color: 'rgba(100,130,170,0.5)', textAlign: 'center', maxWidth: 260 }}>
            WebGL could not be initialized.<br />
            Mission data remains fully operational via the control panel.
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

// ── Camera preset buttons ────────────────────────────────────────────────────

const PRESET_LABELS: Record<CameraPreset, string> = {
  default:    'Reset View',
  earth:      'Earth',
  spacecraft: 'Spacecraft',
  link:       'Link',
};

interface ViewportControlsProps {
  active: CameraPreset;
  onSelect: (p: CameraPreset) => void;
}

function ViewportControls({ active, onSelect }: ViewportControlsProps) {
  return (
    <div style={{
      position: 'absolute', bottom: 14, left: '50%', transform: 'translateX(-50%)',
      display: 'flex', gap: 3, zIndex: 10, pointerEvents: 'auto',
    }}>
      {(Object.keys(CAMERA_PRESETS) as CameraPreset[]).map((preset) => (
        <button
          key={preset}
          onClick={() => onSelect(preset)}
          style={{
            background: active === preset
              ? 'rgba(29,78,216,0.85)'
              : 'rgba(5,9,16,0.78)',
            border: `1px solid ${active === preset
              ? 'rgba(29,78,216,0.90)'
              : 'rgba(255,255,255,0.12)'}`,
            color: active === preset ? '#ffffff' : 'rgba(200,210,228,0.55)',
            fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
            fontSize: 10,
            fontWeight: active === preset ? 600 : 400,
            padding: '4px 10px',
            borderRadius: 3,
            cursor: 'pointer',
            backdropFilter: 'blur(6px)',
            transition: 'background 0.12s, border-color 0.12s, color 0.12s',
          }}
          title={`Camera: ${PRESET_LABELS[preset]}`}
        >
          {PRESET_LABELS[preset]}
        </button>
      ))}
    </div>
  );
}

// ── Distance telemetry overlay ────────────────────────────────────────────────

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

interface DistanceOverlayProps {
  distanceKm: number | null;
  linkState: LinkState | null;
  approvalPhase: ApprovalPhase;
}

function DistanceOverlay({ distanceKm, linkState, approvalPhase }: DistanceOverlayProps) {
  const isTransmitting = approvalPhase === 'transmitting';
  const presStatus = linkState ? presentationLinkStatus(linkState) : null;
  const linkBad = isTransmitting
    ? 'transmitting'
    : presStatus === 'CRITICAL'
      ? 'critical'
      : presStatus === 'DEGRADED'
        ? 'warning'
        : null;

  const linkColor =
    linkBad === 'transmitting' ? '#44ffcc'
    : linkBad === 'critical'  ? '#ff4455'
    : linkBad === 'warning'   ? '#ffaa33'
    : undefined;

  const distValue = formatDistValue(distanceKm);
  const distUnit  = formatDistUnit(distanceKm);

  const MONO = '"IBM Plex Mono", ui-monospace, monospace';

  return (
    <div
      data-testid="distance-overlay"
      style={{
        position: 'absolute',
        top: 28,
        left: 12,
        pointerEvents: 'none',
        userSelect: 'none',
        display: 'flex',
        flexDirection: 'column',
        gap: 1,
        zIndex: 10,
      }}
    >
      <div style={{
        fontFamily: MONO,
        fontSize: 8,
        color: 'rgba(180,200,255,0.42)',
        letterSpacing: '0.12em',
        textTransform: 'uppercase',
      }}>
        Distance
      </div>
      <div style={{
        fontFamily: MONO,
        fontSize: 15,
        fontWeight: 700,
        color: '#cce8ff',
        letterSpacing: '0.03em',
        lineHeight: 1.1,
      }}>
        {distValue}
      </div>
      {distUnit && (
        <div style={{
          fontFamily: MONO,
          fontSize: 8,
          fontWeight: 600,
          color: 'rgba(180,210,255,0.65)',
          letterSpacing: '0.10em',
          textTransform: 'uppercase',
        }}>
          {distUnit}
        </div>
      )}
      <div
        data-testid="not-to-scale-label"
        style={{
          fontFamily: MONO,
          fontSize: 7,
          color: 'rgba(180,200,255,0.30)',
          letterSpacing: '0.10em',
          textTransform: 'uppercase',
          marginTop: 3,
        }}
      >
        Visual spacing not to scale
      </div>
      {linkBad && (
        <div style={{
          fontFamily: MONO,
          fontSize: 8,
          color: linkColor,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          marginTop: 1,
        }}>
          {linkBad === 'transmitting'
            ? '⟳ Transmitting'
            : linkBad === 'warning'
              ? '⚠ Link Degraded'
              : '✕ Link Critical'}
        </div>
      )}
    </div>
  );
}

// ── 3D Loading indicator ──────────────────────────────────────────────────────

function SceneLoader() {
  return (
    <div style={{
      position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
      fontSize: 11, color: 'rgba(100,160,210,0.5)',
      letterSpacing: '0.1em', textTransform: 'uppercase',
      background: '#060a12',
      pointerEvents: 'none',
    }}>
      Initializing 3D View…
    </div>
  );
}

// ── Props ─────────────────────────────────────────────────────────────────────

interface Props {
  linkState: LinkState | null;
  missionState: MissionState | null;
  distanceKm: number | null;
  approvalPhase: ApprovalPhase;
  showStarfield?: boolean;
  showLabels?: boolean;
  showCommLink?: boolean;
  smoothCamera?: boolean;
  /** Current active pulse from authoritative attempt_events — drives 3D animation. */
  activePulse?: import('./scene/CommunicationLink').ActivePulse | null;
  /** Direction of the active pulse. */
  pulseDirection?: import('./scene/CommunicationLink').LinkDirection;
}

// ── MissionViewport ────────────────────────────────────────────────────────────

export function MissionViewport({
  linkState,
  missionState,
  distanceKm,
  approvalPhase,
  showStarfield = true,
  showLabels = true,
  showCommLink = true,
  smoothCamera = true,
  activePulse = null,
  pulseDirection = 'idle',
}: Props) {
  const [cameraPreset, setCameraPreset] = useState<CameraPreset>('default');

  const handleCameraSelect = useCallback((preset: CameraPreset) => {
    setCameraPreset(preset);
  }, []);

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%', background: '#060a12' }}>
      <WebGLErrorBoundary>
        <Suspense fallback={<SceneLoader />}>
          <Canvas
            camera={{
              position: CAMERA_PRESETS.default.pos.toArray() as [number, number, number],
              fov: 42,
              near: 0.5,
              far: 1200,
            }}
            shadows={false}
            dpr={Math.min(window.devicePixelRatio, 2)}
            gl={{ antialias: true, alpha: false, powerPreference: 'high-performance' }}
            style={{ background: '#050910' }}
          >
            <MissionScene
              linkState={linkState}
              missionState={missionState}
              distanceKm={distanceKm}
              approvalPhase={approvalPhase}
              cameraTarget={cameraPreset}
              showStarfield={showStarfield}
              showLabels={showLabels}
              showCommLink={showCommLink}
              smoothCamera={smoothCamera}
              activePulse={activePulse}
              pulseDirection={pulseDirection}
            />
          </Canvas>
        </Suspense>
      </WebGLErrorBoundary>

      {/* Camera preset controls */}
      <ViewportControls active={cameraPreset} onSelect={handleCameraSelect} />

      {/* Viewport corner label */}
      <div style={{
        position: 'absolute', top: 10, left: 12,
        fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
        fontSize: 9, color: 'rgba(226,232,244,0.22)',
        letterSpacing: '0.06em',
        pointerEvents: 'none',
      }}>
        3D Mission View
      </div>

      {/* Distance telemetry overlay — screen-space, upper-left, below viewport label */}
      <DistanceOverlay
        distanceKm={distanceKm}
        linkState={linkState}
        approvalPhase={approvalPhase}
      />
    </div>
  );
}
