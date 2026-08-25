/**
 * ResizableSection — a vertically resizable content section.
 *
 * Renders a header + content area with a drag handle at the bottom.
 * Used inside RightPanel to allow the user to resize stacked sections.
 *
 * V3.3: unified resizable section component for all Main Control views.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

interface Props {
  /** Title shown in the section header */
  title: string;
  /** Icon/glyph shown before the title */
  icon?: string;
  /** Accent color for icon */
  accent?: string;
  /** Initial height in px. null = auto (content-sized), but still scrollable */
  defaultHeight?: number | null;
  /** Minimum allowed height */
  minHeight?: number;
  /** Disable the resize handle (for single-section views) */
  noResize?: boolean;
  /** Whether section starts collapsed */
  defaultOpen?: boolean;
  /** Storage key for persisting height. If omitted, height is session-only */
  storageKey?: string;
  children: React.ReactNode;
}

function loadHeight(key: string | undefined, def: number | null): number | null {
  if (!key) return def;
  try {
    const raw = localStorage.getItem(`GCSI_SEC_H_${key}`);
    if (!raw) return def;
    const n = parseInt(raw, 10);
    return isNaN(n) ? def : n;
  } catch { return def; }
}

function saveHeight(key: string | undefined, h: number) {
  if (!key) return;
  try { localStorage.setItem(`GCSI_SEC_H_${key}`, String(h)); } catch { /* ignore */ }
}

export function ResizableSection({
  title,
  icon = '◆',
  accent = '#4C8DFF',
  defaultHeight = null,
  minHeight = 80,
  noResize = false,
  defaultOpen = true,
  storageKey,
  children,
}: Props) {
  const [open, setOpen] = useState(defaultOpen);
  const [height, setHeight] = useState<number | null>(() =>
    loadHeight(storageKey, defaultHeight)
  );
  const dragging = useRef(false);
  const startY = useRef(0);
  const startH = useRef(0);
  const containerRef = useRef<HTMLDivElement>(null);

  const handleDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    if (!open) return;
    dragging.current = true;
    startY.current = e.clientY;
    // Snapshot current rendered height
    startH.current = height ?? (containerRef.current?.clientHeight ?? 200);
    document.body.style.cursor = 'ns-resize';
    document.body.style.userSelect = 'none';
  }, [open, height]);

  useEffect(() => {
    function onMove(e: MouseEvent) {
      if (!dragging.current) return;
      const delta = e.clientY - startY.current;
      const next = Math.max(minHeight, startH.current + delta);
      setHeight(next);
    }
    function onUp() {
      if (!dragging.current) return;
      dragging.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      setHeight((h) => {
        if (h !== null) saveHeight(storageKey, h);
        return h;
      });
    }
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [storageKey, minHeight]);

  const resetHeight = useCallback(() => {
    setHeight(defaultHeight);
    saveHeight(storageKey, defaultHeight ?? 0);
  }, [storageKey, defaultHeight]);

  return (
    <div style={{
      background: 'rgba(255,255,255,0.024)',
      border: '1px solid rgba(255,255,255,0.07)',
      borderRadius: 10,
      marginBottom: 8,
      minWidth: 0,
      overflow: 'hidden',
      display: 'flex',
      flexDirection: 'column',
    }}>
      {/* Header */}
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
        {!noResize && open && (
          <span
            title="Double-click to reset height"
            onDoubleClick={(e) => { e.stopPropagation(); resetHeight(); }}
            style={{
              fontSize: 10, color: 'rgba(76,141,255,0.3)',
              marginRight: 6, cursor: 'default',
              fontFamily: 'monospace', userSelect: 'none',
            }}
          >
            ↕
          </span>
        )}
        <span style={{
          fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
          fontSize: 13, color: 'rgba(120,140,168,0.45)',
          lineHeight: 1, userSelect: 'none',
        }}>
          {open ? '−' : '+'}
        </span>
      </button>

      {/* Content area */}
      {open && (
        <div
          ref={containerRef}
          style={{
            flex: height === null ? '1 1 auto' : undefined,
            height: height !== null ? height : undefined,
            minHeight,
            overflowY: 'auto',
            overflowX: 'hidden',
            padding: '12px 14px',
            minWidth: 0,
          }}
        >
          {children}
        </div>
      )}

      {/* Drag handle — only shown when open and resize enabled */}
      {open && !noResize && (
        <div
          onMouseDown={handleDragStart}
          style={{
            height: 6,
            cursor: 'ns-resize',
            flexShrink: 0,
            background: 'transparent',
            borderTop: '1px solid rgba(76,141,255,0.12)',
            transition: 'background 0.15s, border-color 0.15s',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLDivElement).style.background = 'rgba(76,141,255,0.08)';
            (e.currentTarget as HTMLDivElement).style.borderTopColor = 'rgba(76,141,255,0.30)';
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLDivElement).style.background = 'transparent';
            (e.currentTarget as HTMLDivElement).style.borderTopColor = 'rgba(76,141,255,0.12)';
          }}
        >
          {/* grip dots */}
          <div style={{
            width: 28, height: 3,
            background: 'rgba(76,141,255,0.18)',
            borderRadius: 2,
          }} />
        </div>
      )}
    </div>
  );
}
