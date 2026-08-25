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
      position: 'absolute', bottom: 16, left: '50%', transform: 'translateX(-50%)',
      display: 'flex', gap: 4, zIndex: 10, pointerEvents: 'auto',
    }}>
      {(Object.keys(CAMERA_PRESETS) as CameraPreset[]).map((preset) => (
        <button
          key={preset}
          onClick={() => onSelect(preset)}
          style={{
            background: active === preset
              ? 'rgba(52,211,153,0.12)'
              : 'rgba(8,12,20,0.80)',
            border: `1px solid ${active === preset
              ? 'rgba(52,211,153,0.35)'
              : 'rgba(255,255,255,0.10)'}`,
            color: active === preset ? '#34d399' : 'rgba(226,232,244,0.45)',
            fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
            fontSize: 10,
            fontWeight: 500,
            padding: '5px 12px',
            borderRadius: 6,
            cursor: 'pointer',
            backdropFilter: 'blur(8px)',
            transition: 'background 0.15s, border-color 0.15s, color 0.15s',
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
