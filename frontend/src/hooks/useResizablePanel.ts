/**
 * useResizablePanel — manages the resizable right-panel width.
 *
 * Persists width in localStorage. Provides a drag handler for a
 * vertical divider between the 3D viewport and the right panel.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

const STORAGE_KEY = 'GCSI_PANEL_WIDTH_v1';
const DEFAULT_WIDTH = 440;
const MIN_WIDTH = 340;
const MAX_WIDTH = 680;

function loadWidth(): number {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_WIDTH;
    const n = parseInt(raw, 10);
    return isNaN(n) ? DEFAULT_WIDTH : Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, n));
  } catch {
    return DEFAULT_WIDTH;
  }
}

function saveWidth(w: number) {
  try { localStorage.setItem(STORAGE_KEY, String(w)); } catch { /* ignore */ }
}

export function useResizablePanel() {
  const [width, setWidth] = useState<number>(loadWidth);
  const dragging = useRef(false);
  const startX = useRef(0);
  const startWidth = useRef(0);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    startX.current = e.clientX;
    startWidth.current = width;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  }, [width]);

  useEffect(() => {
    function onMouseMove(e: MouseEvent) {
      if (!dragging.current) return;
      // Dragging left increases width (panel is on the right)
      const delta = startX.current - e.clientX;
      const next = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth.current + delta));
      setWidth(next);
    }
    function onMouseUp() {
      if (!dragging.current) return;
      dragging.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      setWidth((w) => { saveWidth(w); return w; });
    }
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    return () => {
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    };
  }, []);

  const resetWidth = useCallback(() => {
    setWidth(DEFAULT_WIDTH);
    saveWidth(DEFAULT_WIDTH);
  }, []);

  return { width, handleMouseDown, resetWidth, MIN_WIDTH, MAX_WIDTH, DEFAULT_WIDTH };
}
