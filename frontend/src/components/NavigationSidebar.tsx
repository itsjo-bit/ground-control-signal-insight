/**
 * NavigationSidebar — narrow persistent left navigation rail.
 *
 * V4.1: Restrained dark engineering theme. Blue indicator line for active item.
 * No neon glow. Same structure as V4.0 — only colors changed.
 */

export type NavSection =
  | 'mission'
  | 'spacecraft'
  | 'comms'
  | 'data'
  | 'ai'
  | 'transmission'
  | 'log'
  | 'config';

interface NavItem {
  id: NavSection;
  label: string;
  icon: string;
  tooltip: string;
}

const NAV_ITEMS: NavItem[] = [
  { id: 'mission',      label: 'Mission',    icon: '◉', tooltip: 'Mission state and overview' },
  { id: 'spacecraft',   label: 'Spacecraft', icon: '⬡', tooltip: 'Spacecraft and comm geometry' },
  { id: 'comms',        label: 'Comms',      icon: '⌾', tooltip: 'Link health and signal data' },
  { id: 'data',         label: 'Data',       icon: '▦', tooltip: 'Data products and transmission queue' },
  { id: 'ai',           label: 'AI',         icon: '◈', tooltip: 'AI analysis and recommendations' },
  { id: 'transmission', label: 'Transmit',   icon: '↗', tooltip: 'Transmission control and approval' },
  { id: 'log',          label: 'Log',        icon: '≡', tooltip: 'Mission log and simulation results' },
];

interface Props {
  active: NavSection;
  onNavigate: (section: NavSection) => void;
}

export function NavigationSidebar({ active, onNavigate }: Props) {
  return (
    <nav style={{
      width: 60,
      background: '#161b22',
      borderRight: '1px solid #30363d',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      flexShrink: 0,
      zIndex: 20,
      position: 'relative',
    }}>
      {/* GCSI mark */}
      <div style={{
        width: '100%',
        padding: '12px 0 10px',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        borderBottom: '1px solid #30363d',
        marginBottom: 8,
        flexShrink: 0,
      }}>
        <div style={{
          fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
          fontSize: 8,
          fontWeight: 700,
          color: '#2f81f7',
          letterSpacing: '0.10em',
          textTransform: 'uppercase',
        }}>
          GCS
        </div>
      </div>

      {/* Navigation items */}
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 2,
        width: '100%',
        padding: '0 4px',
        flex: 1,
      }}>
        {NAV_ITEMS.map((item) => {
          const isActive = active === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onNavigate(item.id)}
              title={item.tooltip}
              aria-label={item.tooltip}
              aria-current={isActive ? 'page' : undefined}
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                gap: 3,
                padding: '8px 0',
                background: isActive ? 'rgba(47,129,247,0.12)' : 'transparent',
                border: 'none',
                borderLeft: isActive ? '3px solid #2f81f7' : '3px solid transparent',
                borderRadius: '0 3px 3px 0',
                cursor: 'pointer',
                transition: 'background 0.12s',
                width: '100%',
              }}
              onMouseEnter={(e) => {
                if (!isActive) {
                  (e.currentTarget as HTMLButtonElement).style.background = 'rgba(255,255,255,0.04)';
                }
              }}
              onMouseLeave={(e) => {
                if (!isActive) {
                  (e.currentTarget as HTMLButtonElement).style.background = 'transparent';
                }
              }}
            >
              <span style={{
                fontSize: 13,
                color: isActive ? '#2f81f7' : '#8b949e',
                lineHeight: 1,
                transition: 'color 0.12s',
              }}>
                {item.icon}
              </span>
              <span style={{
                fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
                fontSize: 8,
                fontWeight: isActive ? 600 : 400,
                color: isActive ? '#2f81f7' : '#656d76',
                letterSpacing: '0.02em',
                lineHeight: 1,
                transition: 'color 0.12s',
              }}>
                {item.label}
              </span>
            </button>
          );
        })}
      </div>

      {/* Config button — bottom utility area */}
      <div style={{
        width: '100%',
        borderTop: '1px solid #30363d',
        padding: '6px 4px',
        marginTop: 'auto',
      }}>
        <button
          onClick={() => onNavigate('config')}
          title="Configuration & settings"
          aria-label="Configuration & settings"
          aria-current={active === 'config' ? 'page' : undefined}
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 3,
            padding: '7px 0',
            borderRadius: '0 3px 3px 0',
            width: '100%',
            background: active === 'config' ? 'rgba(47,129,247,0.12)' : 'transparent',
            border: 'none',
            borderLeft: active === 'config' ? '3px solid #2f81f7' : '3px solid transparent',
            cursor: 'pointer',
            transition: 'background 0.12s',
          }}
          onMouseEnter={(e) => {
            if (active !== 'config') {
              (e.currentTarget as HTMLButtonElement).style.background = 'rgba(255,255,255,0.04)';
            }
          }}
          onMouseLeave={(e) => {
            if (active !== 'config') {
              (e.currentTarget as HTMLButtonElement).style.background = 'transparent';
            }
          }}
        >
          <span style={{
            fontSize: 12,
            color: active === 'config' ? '#2f81f7' : '#656d76',
            transition: 'color 0.12s',
          }}>⚙</span>
          <span style={{
            fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
            fontSize: 8, fontWeight: active === 'config' ? 600 : 400,
            color: active === 'config' ? '#2f81f7' : '#656d76',
            letterSpacing: '0.02em',
            transition: 'color 0.12s',
          }}>
            Config
          </span>
        </button>
      </div>
    </nav>
  );
}
