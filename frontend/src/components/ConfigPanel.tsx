/**
 * ConfigPanel — Interface / Layout / View settings panel.
 *
 * V3.4: Added scenario management section.
 * V4.0: Light analytical workspace theme.
 */
import type { ViewSettings } from '../hooks/useViewSettings';
import type { MissionSourceMode, ScenarioInfo } from '../types/domain';

interface Props {
  settings: ViewSettings;
  onUpdate: <K extends keyof ViewSettings>(key: K, value: ViewSettings[K]) => void;
  onResetSettings: () => void;
  onResetPanelWidth: () => void;
  panelWidth: number;
  panelDefaultWidth: number;
  // V3.4: scenario management (optional — not shown when unavailable)
  availableScenarios?: ScenarioInfo[];
  activeScenarioPath?: string | null;
  scenarioSwitching?: boolean;
  onSwitchScenario?: (filename: string) => void;
  // Phase 6E-C7: source mode for historical context note
  sourceMode?: MissionSourceMode | null;
}

function ToggleRow({
  label,
  description,
  value,
  onChange,
}: {
  label: string;
  description?: string;
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '9px 0',
      borderBottom: '1px solid #30363d',
      gap: 12,
      minWidth: 0,
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
          fontSize: 12, fontWeight: 500, color: '#e6edf3',
        }}>
          {label}
        </div>
        {description && (
          <div style={{
            fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
            fontSize: 10.5, color: '#8b949e',
            marginTop: 2, lineHeight: 1.4,
          }}>
            {description}
          </div>
        )}
      </div>
      {/* Toggle switch */}
      <button
        onClick={() => onChange(!value)}
        aria-pressed={value}
        style={{
          width: 36, height: 20,
          borderRadius: 10,
          background: value ? '#2f81f7' : '#30363d',
          border: `1px solid ${value ? '#2f81f7' : '#444c56'}`,
          position: 'relative',
          cursor: 'pointer',
          flexShrink: 0,
          transition: 'background 0.18s, border-color 0.18s',
          padding: 0,
        }}
      >
        <div style={{
          position: 'absolute',
          top: 2,
          left: value ? 16 : 2,
          width: 14, height: 14,
          borderRadius: '50%',
          background: '#e6edf3',
          transition: 'left 0.18s',
          boxShadow: '0 1px 2px rgba(0,0,0,0.30)',
        }} />
      </button>
    </div>
  );
}

function SectionHead({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
      fontSize: 9, fontWeight: 600, letterSpacing: '0.07em',
      textTransform: 'uppercase',
      color: '#8b949e',
      marginTop: 18, marginBottom: 4,
      paddingBottom: 5,
      borderBottom: '1px solid #30363d',
    }}>
      {children}
    </div>
  );
}

function ActionBtn({
  label,
  onClick,
  danger,
}: {
  label: string;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        background: danger ? 'rgba(248,81,73,0.08)' : '#21262d',
        color: danger ? '#f85149' : '#8b949e',
        border: `1px solid ${danger ? 'rgba(248,81,73,0.25)' : '#444c56'}`,
        borderRadius: 4,
        padding: '6px 14px',
        fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
        fontSize: 11.5, fontWeight: 500,
        cursor: 'pointer',
        transition: 'background 0.15s',
        marginRight: 6,
        marginBottom: 6,
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.background =
          danger ? 'rgba(248,81,73,0.14)' : '#30363d';
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.background =
          danger ? 'rgba(248,81,73,0.08)' : '#21262d';
      }}
    >
      {label}
    </button>
  );
}

export function ConfigPanel({
  settings,
  onUpdate,
  onResetSettings,
  onResetPanelWidth,
  panelWidth,
  panelDefaultWidth,
  availableScenarios = [],
  activeScenarioPath: _activeScenarioPath,
  scenarioSwitching = false,
  onSwitchScenario,
  sourceMode,
}: Props) {
  const isHistoricalReplay = sourceMode === 'historical_replay';

  return (
    <div style={{ padding: '4px 0', minWidth: 0 }}>
      {/* Header */}
      <div style={{
        fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
        fontSize: 13, fontWeight: 600,
        color: '#e6edf3', marginBottom: 4,
      }}>
        Configuration
      </div>
      <div style={{
        fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
        fontSize: 11, color: '#8b949e',
        lineHeight: 1.5, marginBottom: 12,
      }}>
        Interface, layout, and 3D view preferences. All settings are saved automatically.
      </div>

      {/* ── Main Control Layout ── */}
      <SectionHead>Main Control Layout</SectionHead>

      <div style={{
        padding: '8px 12px',
        background: '#21262d',
        border: '1px solid #30363d',
        borderRadius: 4,
        marginBottom: 8,
      }}>
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <span style={{
            fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
            fontSize: 11, color: '#8b949e',
          }}>
            Current panel width
          </span>
          <span style={{
            fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
            fontSize: 12, fontWeight: 600, color: '#2f81f7',
          }}>
            {panelWidth}px
          </span>
        </div>
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          marginTop: 4,
        }}>
          <span style={{
            fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
            fontSize: 11, color: '#8b949e',
          }}>
            Default width
          </span>
          <span style={{
            fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
            fontSize: 12, color: '#656d76',
          }}>
            {panelDefaultWidth}px
          </span>
        </div>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', paddingTop: 4 }}>
        <ActionBtn label="Reset panel width" onClick={onResetPanelWidth} />
      </div>

      {/* ── 3D View ── */}
      <SectionHead>3D View</SectionHead>

      <div style={{ paddingBottom: 4 }}>
        <ToggleRow
          label="Starfield"
          description="Deep-space star background"
          value={settings.showStarfield}
          onChange={(v) => onUpdate('showStarfield', v)}
        />
        <ToggleRow
          label="Communication link"
          description="Signal beam between spacecraft and Earth"
          value={settings.showCommLink}
          onChange={(v) => onUpdate('showCommLink', v)}
        />
        <ToggleRow
          label="Scene labels"
          description="Distance, spacecraft, and Earth labels"
          value={settings.showLabels}
          onChange={(v) => onUpdate('showLabels', v)}
        />
      </div>

      {/* ── Camera ── */}
      <SectionHead>Camera</SectionHead>

      <div style={{ paddingBottom: 4 }}>
        <ToggleRow
          label="Smooth transitions"
          description="Animate camera movement between presets"
          value={settings.smoothCamera}
          onChange={(v) => onUpdate('smoothCamera', v)}
        />
      </div>

      {/* ── Scenario Management ── */}
      {availableScenarios && availableScenarios.length > 0 && (
        <>
          <SectionHead>Active Scenario</SectionHead>

          {/* Phase 6E-C7: Historical replay context note */}
          {isHistoricalReplay && (
            <div style={{
              padding: '8px 10px',
              marginBottom: 10,
              background: 'rgba(47,129,247,0.06)',
              border: '1px solid rgba(47,129,247,0.2)',
              borderRadius: 4,
              fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
              fontSize: 10.5, lineHeight: 1.5,
              color: '#8b949e',
            }}>
              <span style={{
                display: 'block', fontWeight: 600,
                color: '#2f81f7', fontSize: 10, marginBottom: 3,
              }}>
                Historical replay is currently active.
              </span>
              The list below contains synthetic scenarios.
              Selecting one exits historical replay and switches the runtime to synthetic mode.
            </div>
          )}

          <div style={{ marginBottom: 10 }}>
            {availableScenarios.map((scen: ScenarioInfo) => {
              const isActive = scen.is_active;
              return (
                <div
                  key={scen.filename}
                  style={{
                    padding: '10px 12px',
                    marginBottom: 6,
                    borderRadius: 4,
                    border: `1px solid ${isActive ? 'rgba(47,129,247,0.30)' : '#30363d'}`,
                    background: isActive ? 'rgba(47,129,247,0.10)' : '#21262d',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 4 }}>
                    <div style={{
                      fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
                      fontSize: 10, fontWeight: 600,
                      color: isActive ? '#2f81f7' : '#8b949e',
                      wordBreak: 'break-all',
                    }}>
                      {scen.filename}
                    </div>
                    {isActive && (
                      <span style={{
                        fontSize: 8, fontWeight: 700, letterSpacing: '0.07em',
                        background: 'rgba(63,185,80,0.10)', color: '#3fb950',
                        border: '1px solid rgba(63,185,80,0.28)',
                        borderRadius: 2, padding: '1px 5px',
                        fontFamily: '"IBM Plex Mono"', flexShrink: 0, marginLeft: 6,
                      }}>
                        ACTIVE
                      </span>
                    )}
                  </div>
                  <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 10.5, color: '#8b949e', marginBottom: 4, lineHeight: 1.4 }}>
                    {scen.label}
                  </div>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {scen.data_products_count > 0 && (
                      <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#d29922' }}>
                        {scen.data_products_count} products
                      </span>
                    )}
                    {scen.anomalies_count > 0 && (
                      <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#f85149' }}>
                        {scen.anomalies_count} anomalies
                      </span>
                    )}
                    {!scen.has_data_products && (
                      <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#d29922' }}>
                        legacy packets
                      </span>
                    )}
                  </div>
                  {!isActive && onSwitchScenario && (
                    <button
                      onClick={() => onSwitchScenario(scen.filename)}
                      disabled={scenarioSwitching}
                      style={{
                        marginTop: 8, fontSize: 11, padding: '4px 12px',
                        background: 'rgba(47,129,247,0.10)',
                        color: '#2f81f7',
                        border: '1px solid rgba(47,129,247,0.28)',
                        borderRadius: 4, cursor: 'pointer',
                        fontFamily: '"IBM Plex Sans"',
                        opacity: scenarioSwitching ? 0.5 : 1,
                      }}
                    >
                      {scenarioSwitching ? 'Switching…' : 'Switch to this scenario'}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
          <div style={{
            fontFamily: '"IBM Plex Sans"', fontSize: 10, color: '#656d76', lineHeight: 1.5, marginBottom: 8,
          }}>
            Switching scenarios resets AI analysis, manual selections, and transmission state.
          </div>
        </>
      )}

      {/* ── Restore Defaults ── */}
      <SectionHead>Restore Defaults</SectionHead>

      <div style={{ paddingTop: 6, paddingBottom: 8, display: 'flex', flexWrap: 'wrap' }}>
        <ActionBtn
          label="Reset all settings"
          onClick={onResetSettings}
          danger
        />
      </div>

      <div style={{
        marginTop: 4,
        fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
        fontSize: 10, color: '#656d76',
        lineHeight: 1.5,
      }}>
        Settings are stored in your browser's local storage and persist across page refreshes.
      </div>

      {/* ── About ── */}
      <SectionHead>About</SectionHead>
      <div style={{
        padding: '8px 10px',
        background: '#21262d',
        border: '1px solid #30363d',
        borderRadius: 4,
        fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
        fontSize: 10,
        lineHeight: 1.8,
        color: '#8b949e',
      }}>
        <div><span style={{ color: '#2f81f7', fontWeight: 600 }}>GCSI</span> 1.0.0</div>
        <div>Ground Control Signal Insight</div>
        <div style={{ marginTop: 4, fontFamily: '"IBM Plex Sans"', fontSize: 10, color: '#656d76' }}>
          Earth imagery: NASA Blue Marble
        </div>
      </div>
    </div>
  );
}
