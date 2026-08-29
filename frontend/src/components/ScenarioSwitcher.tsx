/**
 * ScenarioSwitcher — Phase 7 one-click mission source switcher.
 *
 * Compact dropdown in the top header.  Calls GET /sources on mount and
 * POST /sources/select on selection.  Never sends filesystem paths.
 *
 * Props
 * -----
 * activeSources:   loaded sources list (or null while loading)
 * activeSourceId:  currently active source_id (or null)
 * switching:       true while a switch POST is in flight (disables control)
 * onSelectSource:  called with the new source_id when the user picks one
 * error:           optional error message to show below the switcher
 */

import { useState, useRef, useEffect, useCallback } from 'react';
import type { MissionSourceInfo } from '../types/domain';

const MONO = '"IBM Plex Mono", ui-monospace, "SF Mono", monospace';
const SANS = '"IBM Plex Sans", system-ui, sans-serif';

export interface ScenarioSwitcherProps {
  sources: MissionSourceInfo[];
  activeSourceId: string | null;
  switching: boolean;
  onSelectSource: (sourceId: string) => void;
  error?: string | null;
}

/**
 * Build a compact label for the currently active source, suitable for the
 * dropdown trigger button.
 *
 * Examples:
 *   ASTERIA-7 · SYNTHETIC
 *   JUNO PJ62 · HISTORICAL V1
 *   JUNO PJ62 · HISTORICAL V2
 */
function buildActiveLabel(source: MissionSourceInfo | null, switching: boolean): string {
  if (switching) return 'Switching…';
  if (!source) return 'Loading…';
  const base = source.display_name.toUpperCase();
  const suffix = source.mode === 'historical_replay' ? 'HIST' : 'SYNTHETIC';
  return `${base} · ${suffix}`;
}

export function ScenarioSwitcher({
  sources,
  activeSourceId,
  switching,
  onSelectSource,
  error,
}: ScenarioSwitcherProps) {
  const [open, setOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const activeSource = sources.find((s) => s.source_id === activeSourceId) ?? null;

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    function handleOutside(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handleOutside);
    return () => document.removeEventListener('mousedown', handleOutside);
  }, [open]);

  const handleSelect = useCallback((sourceId: string) => {
    setOpen(false);
    if (sourceId === activeSourceId) return; // no-op for same source
    onSelectSource(sourceId);
  }, [activeSourceId, onSelectSource]);

  const disabled = switching || sources.length === 0;

  return (
    <div
      ref={dropdownRef}
      style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}
      data-testid="scenario-switcher"
    >
      {/* Label */}
      <span style={{
        fontFamily: SANS,
        fontSize: 10,
        color: 'rgba(122,143,168,0.6)',
        fontWeight: 500,
        letterSpacing: '0.01em',
        flexShrink: 0,
      }}>
        Scenario
      </span>

      {/* Trigger button */}
      <button
        data-testid="scenario-switcher-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Active scenario: ${buildActiveLabel(activeSource, switching)}`}
        disabled={disabled}
        onClick={() => !disabled && setOpen((v) => !v)}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          padding: '3px 9px',
          background: switching
            ? 'rgba(76,141,255,0.06)'
            : 'rgba(255,255,255,0.04)',
          color: switching
            ? '#6EA8FF'
            : 'rgba(226,232,244,0.8)',
          border: switching
            ? '1px solid rgba(76,141,255,0.22)'
            : '1px solid rgba(255,255,255,0.08)',
          borderRadius: 5,
          fontFamily: MONO,
          fontSize: 10,
          fontWeight: 600,
          letterSpacing: '0.03em',
          cursor: disabled ? 'not-allowed' : 'pointer',
          opacity: disabled ? 0.55 : 1,
          transition: 'background 0.15s, border-color 0.15s, color 0.15s',
          whiteSpace: 'nowrap',
          minWidth: 180,
          userSelect: 'none',
        }}
      >
        <span style={{ flex: 1, textAlign: 'left' }}>
          {buildActiveLabel(activeSource, switching)}
        </span>
        <span style={{
          fontSize: 8,
          color: switching ? '#6EA8FF' : 'rgba(122,143,168,0.5)',
          marginLeft: 2,
        }}>
          ▼
        </span>
      </button>

      {/* Dropdown menu */}
      {open && !switching && (
        <div
          role="listbox"
          aria-label="Select mission scenario"
          style={{
            position: 'absolute',
            top: '100%',
            left: 0,
            marginTop: 4,
            minWidth: 240,
            background: '#0E1520',
            border: '1px solid rgba(46,58,79,0.9)',
            borderRadius: 7,
            zIndex: 1000,
            boxShadow: '0 8px 32px rgba(0,0,0,0.55)',
            padding: '4px 0',
            overflow: 'hidden',
          }}
        >
          {sources.map((src) => {
            const isActive = src.source_id === activeSourceId;
            const isSynthetic = src.mode === 'synthetic_scenario';
            const modeLabel = isSynthetic ? 'SYNTHETIC' : 'HIST REPLAY';
            const modeColor = isSynthetic
              ? 'rgba(245,158,11,0.65)'
              : 'rgba(110,168,255,0.70)';
            return (
              <button
                key={src.source_id}
                role="option"
                aria-selected={isActive}
                data-testid={`source-option-${src.source_id}`}
                onClick={() => handleSelect(src.source_id)}
                style={{
                  display: 'block',
                  width: '100%',
                  textAlign: 'left',
                  padding: '8px 12px',
                  background: isActive ? 'rgba(76,141,255,0.08)' : 'transparent',
                  border: 'none',
                  borderBottom: '1px solid rgba(46,58,79,0.5)',
                  cursor: 'pointer',
                  transition: 'background 0.12s',
                }}
                onMouseEnter={(e) => {
                  if (!isActive) (e.currentTarget as HTMLButtonElement).style.background = 'rgba(255,255,255,0.04)';
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLButtonElement).style.background = isActive ? 'rgba(76,141,255,0.08)' : 'transparent';
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  {/* Active indicator */}
                  <span style={{
                    width: 5, height: 5, borderRadius: '50%', flexShrink: 0,
                    background: isActive ? '#4C8DFF' : 'transparent',
                    border: isActive ? 'none' : '1px solid rgba(255,255,255,0.12)',
                  }} />
                  {/* Name */}
                  <span style={{
                    fontFamily: SANS, fontSize: 11.5, fontWeight: isActive ? 600 : 400,
                    color: isActive ? '#E6EBF2' : 'rgba(226,232,244,0.7)',
                    flex: 1,
                  }}>
                    {src.display_name}
                  </span>
                  {/* Mode badge */}
                  <span style={{
                    fontFamily: MONO, fontSize: 8, fontWeight: 600,
                    letterSpacing: '0.04em',
                    color: modeColor,
                    padding: '1px 5px',
                    background: isSynthetic ? 'rgba(245,158,11,0.07)' : 'rgba(76,141,255,0.07)',
                    border: `1px solid ${isSynthetic ? 'rgba(245,158,11,0.20)' : 'rgba(76,141,255,0.20)'}`,
                    borderRadius: 3,
                    flexShrink: 0,
                  }}>
                    {modeLabel}
                  </span>
                </div>
                {/* Description */}
                <div style={{
                  fontFamily: SANS, fontSize: 10,
                  color: 'rgba(122,143,168,0.55)',
                  marginTop: 3, marginLeft: 13,
                  lineHeight: 1.4,
                }}>
                  {src.description}
                </div>
              </button>
            );
          })}
        </div>
      )}

      {/* Error toast */}
      {error && (
        <span
          data-testid="scenario-switcher-error"
          style={{
            position: 'absolute',
            top: '100%',
            left: 0,
            marginTop: 6,
            padding: '4px 10px',
            background: 'rgba(248,113,113,0.10)',
            border: '1px solid rgba(248,113,113,0.30)',
            borderRadius: 5,
            fontFamily: SANS,
            fontSize: 10.5,
            color: '#f87171',
            whiteSpace: 'nowrap',
            zIndex: 1001,
          }}
        >
          {error}
        </span>
      )}
    </div>
  );
}
