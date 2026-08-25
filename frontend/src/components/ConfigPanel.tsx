/**
 * ConfigPanel — Interface / Layout / View settings panel.
 *
 * V3.4: Added scenario management section.
 */
import type { ViewSettings } from '../hooks/useViewSettings';
import type { ScenarioInfo } from '../types/domain';

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
      borderBottom: '1px solid rgba(255,255,255,0.05)',
      gap: 12,
      minWidth: 0,
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
          fontSize: 12, fontWeight: 500, color: '#d4dcea',
        }}>
          {label}
        </div>
        {description && (
          <div style={{
            fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
            fontSize: 10.5, color: 'rgba(147,160,180,0.7)',
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
          background: value ? '#4C8DFF' : 'rgba(255,255,255,0.10)',
          border: `1px solid ${value ? 'rgba(76,141,255,0.5)' : 'rgba(255,255,255,0.14)'}`,
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
          background: '#fff',
          transition: 'left 0.18s',
          boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
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
      color: 'rgba(76,141,255,0.65)',
      marginTop: 18, marginBottom: 4,
      paddingBottom: 5,
      borderBottom: '1px solid rgba(76,141,255,0.12)',
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
        background: danger ? 'rgba(248,113,113,0.08)' : 'rgba(76,141,255,0.08)',
        color: danger ? '#f87171' : '#6EA8FF',
        border: `1px solid ${danger ? 'rgba(248,113,113,0.20)' : 'rgba(76,141,255,0.20)'}`,
        borderRadius: 7,
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
          danger ? 'rgba(248,113,113,0.14)' : 'rgba(76,141,255,0.14)';
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.background =
          danger ? 'rgba(248,113,113,0.08)' : 'rgba(76,141,255,0.08)';
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
}: Props) {

  return (
    <div style={{ padding: '4px 0', minWidth: 0 }}>
      {/* Header */}
      <div style={{
        fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
        fontSize: 13, fontWeight: 600,
        color: '#d4dcea', marginBottom: 4,
      }}>
        Configuration
      </div>
      <div style={{
        fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
        fontSize: 11, color: 'rgba(147,160,180,0.65)',
        lineHeight: 1.5, marginBottom: 12,
      }}>
        Interface, layout, and 3D view preferences. All settings are saved automatically.
      </div>

      {/* ── Interface ── */}
      <SectionHead>Interface</SectionHead>

      <div style={{ padding: '4px 0 0' }}>
        <div style={{
          fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
          fontSize: 12, fontWeight: 500, color: '#d4dcea',
          marginBottom: 6,
        }}>
          Density
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          {(['compact', 'comfortable'] as const).map((d) => (
            <button
              key={d}
              onClick={() => onUpdate('density', d)}
              style={{
                padding: '5px 14px',
                borderRadius: 6,
                fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
                fontSize: 11.5, fontWeight: 500,
                cursor: 'pointer',
                border: `1px solid ${settings.density === d ? 'rgba(76,141,255,0.45)' : 'rgba(255,255,255,0.10)'}`,
                background: settings.density === d ? 'rgba(76,141,255,0.12)' : 'rgba(255,255,255,0.03)',
                color: settings.density === d ? '#6EA8FF' : 'rgba(180,195,215,0.6)',
                transition: 'all 0.15s',
              }}
            >
              {d.charAt(0).toUpperCase() + d.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {/* ── Main Control Layout ── */}
      <SectionHead>Main Control Layout</SectionHead>

      <div style={{
        padding: '8px 12px',
        background: 'rgba(76,141,255,0.05)',
        border: '1px solid rgba(76,141,255,0.12)',
        borderRadius: 8,
        marginBottom: 8,
      }}>
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <span style={{
            fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
            fontSize: 11, color: 'rgba(147,160,180,0.7)',
          }}>
            Current panel width
          </span>
          <span style={{
            fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
            fontSize: 12, fontWeight: 600, color: '#6EA8FF',
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
            fontSize: 11, color: 'rgba(147,160,180,0.7)',
          }}>
            Default width
          </span>
          <span style={{
            fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
            fontSize: 12, color: 'rgba(147,160,180,0.5)',
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
          <div style={{ marginBottom: 10 }}>
            {availableScenarios.map((scen: ScenarioInfo) => {
              const isActive = scen.is_active;
              return (
                <div
                  key={scen.filename}
                  style={{
                    padding: '10px 12px',
                    marginBottom: 6,
                    borderRadius: 8,
                    border: `1px solid ${isActive ? 'rgba(76,141,255,0.35)' : 'rgba(46,58,79,0.7)'}`,
                    background: isActive ? 'rgba(76,141,255,0.06)' : 'rgba(255,255,255,0.02)',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 4 }}>
                    <div style={{
                      fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
                      fontSize: 10, fontWeight: 600,
                      color: isActive ? '#6EA8FF' : 'rgba(147,160,180,0.7)',
                      wordBreak: 'break-all',
                    }}>
                      {scen.filename}
                    </div>
                    {isActive && (
                      <span style={{
                        fontSize: 8, fontWeight: 700, letterSpacing: '0.07em',
                        background: 'rgba(52,211,153,0.10)', color: '#34d399',
                        border: '1px solid rgba(52,211,153,0.25)',
                        borderRadius: 2, padding: '1px 5px',
                        fontFamily: '"IBM Plex Mono"', flexShrink: 0, marginLeft: 6,
                      }}>
                        ACTIVE
                      </span>
                    )}
                  </div>
                  <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 10.5, color: 'rgba(147,160,180,0.6)', marginBottom: 4, lineHeight: 1.4 }}>
                    {scen.label}
                  </div>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {scen.data_products_count > 0 && (
                      <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#f59e0b' }}>
                        {scen.data_products_count} products
                      </span>
                    )}
                    {scen.anomalies_count > 0 && (
                      <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#f87171' }}>
                        {scen.anomalies_count} anomalies
                      </span>
                    )}
                    {!scen.has_data_products && (
                      <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#f59e0b' }}>
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
                        background: 'rgba(76,141,255,0.08)',
                        color: '#6EA8FF',
                        border: '1px solid rgba(76,141,255,0.22)',
                        borderRadius: 5, cursor: 'pointer',
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
            fontFamily: '"IBM Plex Sans"', fontSize: 10, color: 'rgba(120,135,155,0.5)', lineHeight: 1.5, marginBottom: 8,
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
        fontSize: 10, color: 'rgba(120,135,155,0.5)',
        lineHeight: 1.5,
      }}>
        Settings are stored in your browser's local storage and persist across page refreshes.
      </div>
    </div>
  );
}
