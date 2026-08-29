/**
 * Section — a simple collapsible content section.
 *
 * V3.5.2: Replaced the old ResizableSection (drag-resize + fixed height +
 * localStorage persistence). Sections now use natural content height and let
 * the parent Main Control scroll container handle vertical scrolling.
 *
 * The resize handle, height state, and GCSI_SEC_H_* localStorage keys have
 * been removed entirely. Collapse / expand is kept for user convenience.
 *
 * V4.0: Light analytical workspace theme.
 */
import { useState } from 'react';

interface Props {
  /** Title shown in the section header */
  title: string;
  /** Icon/glyph shown before the title */
  icon?: string;
  /** Accent color for icon */
  accent?: string;
  /** Whether section starts collapsed */
  defaultOpen?: boolean;
  children: React.ReactNode;
}

export function ResizableSection({
  title,
  icon = '◆',
  accent = '#2f81f7',
  defaultOpen = true,
  children,
}: Props) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div style={{
      background: '#161b22',
      border: '1px solid #30363d',
      borderRadius: 4,
      marginBottom: 8,
      minWidth: 0,
    }}>
      {/* Collapse toggle header */}
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '8px 12px',
          background: 'none',
          border: 'none',
          borderBottom: open ? '1px solid #30363d' : 'none',
          cursor: 'pointer',
          textAlign: 'left',
          borderRadius: 0,
          flexShrink: 0,
        }}
      >
        <span style={{ fontSize: 9, color: accent, flexShrink: 0, lineHeight: 1 }}>{icon}</span>
        <span style={{
          fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
          fontSize: 11, fontWeight: 600,
          color: '#e6edf3',
          letterSpacing: '0.01em',
          flex: 1,
        }}>
          {title}
        </span>
        <span style={{
          fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
          fontSize: 12, color: '#656d76',
          lineHeight: 1, userSelect: 'none',
        }}>
          {open ? '−' : '+'}
        </span>
      </button>

      {/* Content — natural height, no scroll trap */}
      {open && (
        <div style={{
          padding: '10px 12px',
          minWidth: 0,
          overflowX: 'hidden',
        }}>
          {children}
        </div>
      )}
    </div>
  );
}
