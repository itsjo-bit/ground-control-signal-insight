/**
 * usePanelLayout — manages panel visibility, order, sizes, and layout presets.
 *
 * Persists UI preferences to localStorage under GCSI_LAYOUT_PREFS.
 * Never touches mission data, backend state, or API calls.
 *
 * Panel IDs match the keys used in MissionControl to render sections.
 */

import { useCallback, useState } from 'react';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type PanelId =
  | 'mission-state'
  | 'link-health'
  | 'baseline-plan'
  | 'plan-comparison'
  | 'ai-decision'
  | 'mission-decision'
  | 'ai-order'
  | 'ai-reasoning'
  | 'approval'
  | 'simulation'
  | 'mission-report';

export type LayoutPreset = 'mission-control' | 'ai-analysis' | 'minimal';

export interface PanelConfig {
  id: PanelId;
  label: string;
  visible: boolean;
  /** How many grid columns this panel spans (1 or 2). */
  span: 1 | 2;
  /** Optional: operator-set height override in px. null = auto. */
  heightPx: number | null;
}

export interface LayoutPrefs {
  preset: LayoutPreset;
  panels: PanelConfig[];
}

// ---------------------------------------------------------------------------
// Default configurations
// ---------------------------------------------------------------------------

export const PANEL_LABELS: Record<PanelId, string> = {
  'mission-state':    'Mission State',
  'link-health':      'Link Health',
  'baseline-plan':    'Baseline Plan',
  'plan-comparison':  'Plan Comparison',
  'ai-decision':      'AI Decision / Prioritization',
  'mission-decision': 'Mission Decision',
  'ai-order':         'AI Recommended Order',
  'ai-reasoning':     'AI Reasoning',
  'approval':         'Approval',
  'simulation':       'Simulation',
  'mission-report':   'Mission Report',
};

const DEFAULT_PANELS: PanelConfig[] = [
  { id: 'mission-state',    label: 'Mission State',                visible: true,  span: 1, heightPx: null },
  { id: 'link-health',      label: 'Link Health',                  visible: true,  span: 1, heightPx: null },
  { id: 'baseline-plan',    label: 'Baseline Plan',                visible: true,  span: 1, heightPx: null },
  { id: 'plan-comparison',  label: 'Plan Comparison',              visible: true,  span: 1, heightPx: null },
  { id: 'ai-decision',      label: 'AI Decision / Prioritization', visible: true,  span: 2, heightPx: null },
  { id: 'mission-decision', label: 'Mission Decision',             visible: true,  span: 2, heightPx: null },
  { id: 'ai-order',         label: 'AI Recommended Order',         visible: false, span: 2, heightPx: null },
  { id: 'ai-reasoning',     label: 'AI Reasoning',                 visible: true,  span: 2, heightPx: null },
  { id: 'approval',         label: 'Approval',                     visible: true,  span: 2, heightPx: null },
  { id: 'simulation',       label: 'Simulation',                   visible: true,  span: 2, heightPx: null },
  { id: 'mission-report',   label: 'Mission Report',               visible: true,  span: 2, heightPx: null },
];

const PRESET_CONFIGS: Record<LayoutPreset, Partial<Record<PanelId, { visible: boolean; span: 1 | 2 }>>> = {
  'mission-control': {
    'mission-state':    { visible: true,  span: 1 },
    'link-health':      { visible: true,  span: 1 },
    'baseline-plan':    { visible: true,  span: 1 },
    'plan-comparison':  { visible: true,  span: 1 },
    'ai-decision':      { visible: true,  span: 2 },
    'mission-decision': { visible: true,  span: 2 },
    'ai-order':         { visible: false, span: 2 },
    'ai-reasoning':     { visible: true,  span: 2 },
    'approval':         { visible: true,  span: 2 },
    'simulation':       { visible: true,  span: 2 },
    'mission-report':   { visible: true,  span: 2 },
  },
  'ai-analysis': {
    'mission-state':    { visible: true,  span: 1 },
    'link-health':      { visible: true,  span: 1 },
    'baseline-plan':    { visible: false, span: 1 },
    'plan-comparison':  { visible: true,  span: 2 },
    'ai-decision':      { visible: true,  span: 2 },
    'mission-decision': { visible: true,  span: 2 },
    'ai-order':         { visible: false, span: 2 },
    'ai-reasoning':     { visible: true,  span: 2 },
    'approval':         { visible: true,  span: 2 },
    'simulation':       { visible: false, span: 2 },
    'mission-report':   { visible: false, span: 2 },
  },
  'minimal': {
    'mission-state':    { visible: true,  span: 1 },
    'link-health':      { visible: true,  span: 1 },
    'baseline-plan':    { visible: false, span: 1 },
    'plan-comparison':  { visible: false, span: 1 },
    'ai-decision':      { visible: false, span: 2 },
    'mission-decision': { visible: true,  span: 2 },
    'ai-order':         { visible: false, span: 2 },
    'ai-reasoning':     { visible: false, span: 2 },
    'approval':         { visible: true,  span: 2 },
    'simulation':       { visible: false, span: 2 },
    'mission-report':   { visible: false, span: 2 },
  },
};

// Canonical panel order for each preset
const PRESET_ORDER: Record<LayoutPreset, PanelId[]> = {
  'mission-control': [
    'mission-state', 'link-health',
    'baseline-plan', 'plan-comparison',
    'ai-decision',
    'mission-decision',
    'ai-order',
    'ai-reasoning',
    'approval',
    'simulation',
    'mission-report',
  ],
  'ai-analysis': [
    'mission-state', 'link-health',
    'plan-comparison',
    'ai-decision',
    'mission-decision',
    'ai-order',
    'ai-reasoning',
    'approval',
    'baseline-plan',
    'simulation',
    'mission-report',
  ],
  'minimal': [
    'mission-state', 'link-health',
    'mission-decision',
    'approval',
    'ai-decision', 'ai-reasoning',
    'baseline-plan', 'plan-comparison', 'ai-order', 'simulation',
    'mission-report',
  ],
};

// ---------------------------------------------------------------------------
// Storage
// ---------------------------------------------------------------------------

const STORAGE_KEY = 'GCSI_LAYOUT_PREFS_v2';

function applyPresetToPanels(
  panels: PanelConfig[],
  preset: LayoutPreset,
): PanelConfig[] {
  const cfg = PRESET_CONFIGS[preset];
  const order = PRESET_ORDER[preset];
  const updated = panels.map((p) => ({
    ...p,
    ...(cfg[p.id] ?? {}),
    heightPx: null,
  }));
  // Re-sort by preset order
  return order
    .map((id) => updated.find((p) => p.id === id)!)
    .filter(Boolean);
}

function buildDefault(): LayoutPrefs {
  return { preset: 'mission-control', panels: [...DEFAULT_PANELS] };
}

function loadPrefs(): LayoutPrefs {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return buildDefault();
    const parsed = JSON.parse(raw) as LayoutPrefs;
    // Validate basic shape; reject stale / malformed saves
    if (!parsed.panels || !Array.isArray(parsed.panels)) return buildDefault();
    // Merge in any new default panels added since last save
    const savedIds = new Set(parsed.panels.map((p) => p.id));
    const merged = [...parsed.panels];
    for (const def of DEFAULT_PANELS) {
      if (!savedIds.has(def.id)) merged.push({ ...def });
    }
    return { preset: parsed.preset ?? 'mission-control', panels: merged };
  } catch {
    return buildDefault();
  }
}

function savePrefs(prefs: LayoutPrefs): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // Storage quota or private-mode — ignore silently
  }
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function usePanelLayout() {
  const [prefs, setPrefs] = useState<LayoutPrefs>(loadPrefs);

  const updateAndSave = useCallback((next: LayoutPrefs) => {
    setPrefs(next);
    savePrefs(next);
  }, []);

  // Toggle one panel's visibility
  const togglePanel = useCallback((id: PanelId) => {
    setPrefs((prev) => {
      const next: LayoutPrefs = {
        ...prev,
        panels: prev.panels.map((p) =>
          p.id === id ? { ...p, visible: !p.visible } : p
        ),
      };
      savePrefs(next);
      return next;
    });
  }, []);

  // Apply a full preset (resets order + visibility + heights)
  const applyPreset = useCallback((preset: LayoutPreset) => {
    setPrefs((prev) => {
      const next: LayoutPrefs = {
        preset,
        panels: applyPresetToPanels(prev.panels, preset),
      };
      savePrefs(next);
      return next;
    });
  }, []);

  // Reorder: move panel from index `from` to index `to`
  const reorderPanels = useCallback((fromId: PanelId, toId: PanelId) => {
    if (fromId === toId) return;
    setPrefs((prev) => {
      const panels = [...prev.panels];
      const fromIdx = panels.findIndex((p) => p.id === fromId);
      const toIdx   = panels.findIndex((p) => p.id === toId);
      if (fromIdx === -1 || toIdx === -1) return prev;
      const [moved] = panels.splice(fromIdx, 1);
      panels.splice(toIdx, 0, moved);
      const next: LayoutPrefs = { ...prev, panels };
      savePrefs(next);
      return next;
    });
  }, []);

  // Update height for a panel
  const setPanelHeight = useCallback((id: PanelId, heightPx: number | null) => {
    setPrefs((prev) => {
      const next: LayoutPrefs = {
        ...prev,
        panels: prev.panels.map((p) =>
          p.id === id ? { ...p, heightPx } : p
        ),
      };
      savePrefs(next);
      return next;
    });
  }, []);

  // Reset everything to defaults — does NOT touch scenario or backend state
  const resetLayout = useCallback(() => {
    const next = buildDefault();
    updateAndSave(next);
  }, [updateAndSave]);

  return {
    prefs,
    togglePanel,
    applyPreset,
    reorderPanels,
    setPanelHeight,
    resetLayout,
  };
}
