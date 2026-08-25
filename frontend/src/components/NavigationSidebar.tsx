/**
 * NavigationSidebar — narrow persistent left navigation rail.
 *
 * V3.3: Config button now triggers navigation to 'config' view.
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
      width: 64,
      background: 'rgba(6,9,18,0.98)',
      borderRight: '1px solid rgba(255,255,255,0.06)',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      flexShrink: 0,
      zIndex: 20,
      position: 'relative',
    }}>
      {/* GCSI logo mark */}
      <div style={{
        width: '100%',
        padding: '14px 0 12px',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        borderBottom: '1px solid rgba(255,255,255,0.06)',
        marginBottom: 10,
        flexShrink: 0,
      }}>
        <div style={{
          fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
          fontSize: 9,
          fontWeight: 700,
          color: 'rgba(76,141,255,0.75)',
          letterSpacing: '0.12em',
          textTransform: 'uppercase',
        }}>
          GCS
        </div>
      </div>

      {/* Navigation items */}
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
        width: '100%',
        padding: '0 6px',
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
                gap: 4,
                padding: '9px 0',
                background: isActive ? 'rgba(76,141,255,0.12)' : 'transparent',
                border: `1px solid ${isActive ? 'rgba(76,141,255,0.28)' : 'transparent'}`,
                borderRadius: 8,
                cursor: 'pointer',
                transition: 'background 0.15s, border-color 0.15s',
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
                fontSize: 14,
                color: isActive ? '#4C8DFF' : 'rgba(147,160,180,0.5)',
                lineHeight: 1,
                transition: 'color 0.15s',
              }}>
                {item.icon}
              </span>
              <span style={{
                fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
                fontSize: 8,
                fontWeight: 500,
                color: isActive ? '#4C8DFF' : 'rgba(147,160,180,0.40)',
                letterSpacing: '0.03em',
                lineHeight: 1,
                transition: 'color 0.15s',
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
        borderTop: '1px solid rgba(255,255,255,0.05)',
        padding: '8px 6px',
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
            gap: 4,
            padding: '7px 0',
            borderRadius: 8,
            width: '100%',
            background: active === 'config' ? 'rgba(76,141,255,0.12)' : 'transparent',
            border: `1px solid ${active === 'config' ? 'rgba(76,141,255,0.28)' : 'transparent'}`,
            cursor: 'pointer',
            transition: 'background 0.15s, border-color 0.15s',
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
            fontSize: 13,
            color: active === 'config' ? '#4C8DFF' : 'rgba(147,160,180,0.35)',
            transition: 'color 0.15s',
          }}>⚙</span>
          <span style={{
            fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
            fontSize: 8, fontWeight: 500,
            color: active === 'config' ? '#4C8DFF' : 'rgba(147,160,180,0.30)',
            letterSpacing: '0.03em',
            transition: 'color 0.15s',
          }}>
            Config
          </span>
        </button>
      </div>
    </nav>
  );
}
