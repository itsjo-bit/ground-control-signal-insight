/**
 * ScenarioSwitcher — Phase 7 one-click mission source switcher.
 *
 * Compact dropdown in the top header.  Calls GET /sources on mount and
 * POST /sources/select on selection.  Never sends filesystem paths.
 *
 * V4.1: Restrained dark engineering theme. Same behavior as V4.0.
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
        color: '#656d76',
        fontWeight: 400,
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
          background: switching ? 'rgba(47,129,247,0.12)' : '#21262d',
          color: switching ? '#2f81f7' : '#e6edf3',
          border: switching
            ? '1px solid rgba(47,129,247,0.35)'
            : '1px solid #444c56',
          borderRadius: 3,
          fontFamily: MONO,
          fontSize: 10,
          fontWeight: 600,
          letterSpacing: '0.03em',
          cursor: disabled ? 'not-allowed' : 'pointer',
          opacity: disabled ? 0.55 : 1,
          transition: 'background 0.12s, border-color 0.12s, color 0.12s',
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
          color: switching ? '#2f81f7' : '#656d76',
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
            background: '#21262d',
            border: '1px solid #444c56',
            borderRadius: 4,
            zIndex: 1000,
            boxShadow: '0 4px 16px rgba(0,0,0,0.40)',
            padding: '4px 0',
            overflow: 'hidden',
          }}
        >
          {sources.map((src) => {
            const isActive = src.source_id === activeSourceId;
            const isSynthetic = src.mode === 'synthetic_scenario';
            const modeLabel = isSynthetic ? 'SYNTHETIC' : 'HIST';
            const modeColor = isSynthetic ? '#d29922' : '#2f81f7';
            const modeBg = isSynthetic ? 'rgba(210,153,34,0.10)' : 'rgba(47,129,247,0.12)';
            const modeBdr = isSynthetic ? 'rgba(210,153,34,0.28)' : 'rgba(47,129,247,0.30)';
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
                  background: isActive ? 'rgba(47,129,247,0.12)' : 'transparent',
                  border: 'none',
                  borderBottom: '1px solid #30363d',
                  cursor: 'pointer',
                  transition: 'background 0.10s',
                }}
                onMouseEnter={(e) => {
                  if (!isActive) (e.currentTarget as HTMLButtonElement).style.background = 'rgba(255,255,255,0.05)';
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLButtonElement).style.background = isActive ? 'rgba(47,129,247,0.12)' : 'transparent';
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  {/* Active indicator */}
                  <span style={{
                    width: 4, height: 4, borderRadius: '50%', flexShrink: 0,
                    background: isActive ? '#2f81f7' : 'transparent',
                    border: isActive ? 'none' : '1px solid #444c56',
                  }} />
                  {/* Name */}
                  <span style={{
                    fontFamily: SANS, fontSize: 11.5, fontWeight: isActive ? 600 : 400,
                    color: isActive ? '#e6edf3' : '#8b949e',
                    flex: 1,
                  }}>
                    {src.display_name}
                  </span>
                  {/* Mode badge */}
                  <span style={{
                    fontFamily: MONO, fontSize: 8, fontWeight: 700,
                    letterSpacing: '0.05em',
                    color: modeColor,
                    padding: '1px 5px',
                    background: modeBg,
                    border: `1px solid ${modeBdr}`,
                    borderRadius: 2,
                    flexShrink: 0,
                  }}>
                    {modeLabel}
                  </span>
                </div>
                {/* Description */}
                <div style={{
                  fontFamily: SANS, fontSize: 10,
                  color: '#656d76',
                  marginTop: 2, marginLeft: 12,
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
            background: 'rgba(248,81,73,0.08)',
            border: '1px solid rgba(248,81,73,0.28)',
            borderRadius: 3,
            fontFamily: SANS,
            fontSize: 10.5,
            color: '#f85149',
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
