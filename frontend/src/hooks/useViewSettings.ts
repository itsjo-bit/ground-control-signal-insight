/**
 * useViewSettings — persists UI/view configuration settings.
 *
 * Controls toggles for 3D scene elements and UI density.
 * Stored in localStorage under GCSI_VIEW_SETTINGS_v1.
 */
import { useCallback, useState } from 'react';

const STORAGE_KEY = 'GCSI_VIEW_SETTINGS_v1';

export interface ViewSettings {
  showStarfield: boolean;
  showLabels: boolean;
  showCommLink: boolean;
  smoothCamera: boolean;
  density: 'compact' | 'comfortable';
}

const DEFAULTS: ViewSettings = {
  showStarfield: true,
  showLabels: true,
  showCommLink: true,
  smoothCamera: true,
  density: 'comfortable',
};

function load(): ViewSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULTS };
    return { ...DEFAULTS, ...JSON.parse(raw) };
  } catch {
    return { ...DEFAULTS };
  }
}

function save(s: ViewSettings) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(s)); } catch { /* ignore */ }
}

export function useViewSettings() {
  const [settings, setSettings] = useState<ViewSettings>(load);

  const update = useCallback(<K extends keyof ViewSettings>(key: K, value: ViewSettings[K]) => {
    setSettings((prev) => {
      const next = { ...prev, [key]: value };
      save(next);
      return next;
    });
  }, []);

  const resetSettings = useCallback(() => {
    setSettings({ ...DEFAULTS });
    save({ ...DEFAULTS });
  }, []);

  return { settings, update, resetSettings };
}
