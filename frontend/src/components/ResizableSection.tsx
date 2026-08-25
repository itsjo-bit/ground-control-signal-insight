/**
 * Section — a simple collapsible content section.
 *
 * V3.5.2: Replaced the old ResizableSection (drag-resize + fixed height +
 * localStorage persistence). Sections now use natural content height and let
 * the parent Main Control scroll container handle vertical scrolling.
 *
 * The resize handle, height state, and GCSI_SEC_H_* localStorage keys have
 * been removed entirely. Collapse / expand is kept for user convenience.
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
  accent = '#4C8DFF',
  defaultOpen = true,
  children,
}: Props) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div style={{
      background: 'rgba(255,255,255,0.024)',
      border: '1px solid rgba(255,255,255,0.07)',
      borderRadius: 10,
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
          padding: '10px 14px',
          background: 'none',
          border: 'none',
          borderBottom: open ? '1px solid rgba(255,255,255,0.06)' : 'none',
          cursor: 'pointer',
          textAlign: 'left',
          borderRadius: 0,
          flexShrink: 0,
        }}
      >
        <span style={{ fontSize: 10, color: accent, flexShrink: 0, lineHeight: 1 }}>{icon}</span>
        <span style={{
          fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
          fontSize: 11, fontWeight: 600,
          color: 'rgba(220,230,244,0.80)',
          letterSpacing: '0.01em',
          flex: 1,
        }}>
          {title}
        </span>
        <span style={{
          fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
          fontSize: 13, color: 'rgba(120,140,168,0.45)',
          lineHeight: 1, userSelect: 'none',
        }}>
          {open ? '−' : '+'}
        </span>
      </button>

      {/* Content — natural height, no scroll trap */}
      {open && (
        <div style={{
          padding: '12px 14px',
          minWidth: 0,
          overflowX: 'hidden',
        }}>
          {children}
        </div>
      )}
    </div>
  );
}
