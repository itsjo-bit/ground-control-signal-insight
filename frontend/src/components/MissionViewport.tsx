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
    </div>
  );
}
