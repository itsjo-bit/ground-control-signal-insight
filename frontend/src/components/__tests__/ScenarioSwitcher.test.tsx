/**
 * ScenarioSwitcher.test.tsx — Phase 7 source switcher tests.
 *
 * Tests verify:
 *   - Source list renders correctly
 *   - Active source is displayed
 *   - Dropdown contains ASTERIA + Juno V1 + Juno V2
 *   - onSelectSource called with source_id only (never a path)
 *   - Switching state disables the control
 *   - Same-source selection does not call onSelectSource
 *   - Historical badge is HIST, not LIVE
 *   - Error message is shown
 */

import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import React from 'react';
import { ScenarioSwitcher } from '../ScenarioSwitcher';
import type { MissionSourceInfo } from '../../types/domain';

// ── Test fixtures ─────────────────────────────────────────────────────────────

const ASTERIA: MissionSourceInfo = {
  source_id: 'asteria-7',
  display_name: 'ASTERIA-7',
  mode: 'synthetic_scenario',
  description: 'Fictional synthetic thermal-priority contact scenario.',
  historical: false,
  simulated: true,
};

const JUNO_V1: MissionSourceInfo = {
  source_id: 'juno-pj62-v1',
  display_name: 'Juno PJ62 Historical V1',
  mode: 'historical_replay',
  description: 'Small historical replay based on verified Juno PJ62 MWR archive evidence.',
  historical: true,
  simulated: true,
};

const JUNO_V2: MissionSourceInfo = {
  source_id: 'juno-pj62-v2',
  display_name: 'Juno PJ62 Historical V2',
  mode: 'historical_replay',
  description: 'Large historical replay using 403 eligible products.',
  historical: true,
  simulated: true,
};

const ALL_SOURCES = [ASTERIA, JUNO_V1, JUNO_V2];

// ── Source list rendering ─────────────────────────────────────────────────────

describe('ScenarioSwitcher — source list', () => {
  it('renders the switcher container', () => {
    render(React.createElement(ScenarioSwitcher, {
      sources: ALL_SOURCES,
      activeSourceId: 'asteria-7',
      switching: false,
      onSelectSource: vi.fn(),
    }));
    expect(document.querySelector('[data-testid="scenario-switcher"]')).not.toBeNull();
  });

  it('shows the "Scenario" label', () => {
    render(React.createElement(ScenarioSwitcher, {
      sources: ALL_SOURCES,
      activeSourceId: 'asteria-7',
      switching: false,
      onSelectSource: vi.fn(),
    }));
    expect(document.body.textContent).toContain('Scenario');
  });

  it('displays active source ASTERIA in the trigger button', () => {
    render(React.createElement(ScenarioSwitcher, {
      sources: ALL_SOURCES,
      activeSourceId: 'asteria-7',
      switching: false,
      onSelectSource: vi.fn(),
    }));
    const btn = document.querySelector('[data-testid="scenario-switcher-trigger"]');
    expect(btn?.textContent?.toUpperCase()).toContain('ASTERIA-7');
  });

  it('displays active source JUNO V2 in the trigger button', () => {
    render(React.createElement(ScenarioSwitcher, {
      sources: ALL_SOURCES,
      activeSourceId: 'juno-pj62-v2',
      switching: false,
      onSelectSource: vi.fn(),
    }));
    const btn = document.querySelector('[data-testid="scenario-switcher-trigger"]');
    expect(btn?.textContent?.toUpperCase()).toContain('JUNO');
  });

  it('opens dropdown on trigger click and shows all three sources', () => {
    render(React.createElement(ScenarioSwitcher, {
      sources: ALL_SOURCES,
      activeSourceId: 'asteria-7',
      switching: false,
      onSelectSource: vi.fn(),
    }));
    const btn = document.querySelector('[data-testid="scenario-switcher-trigger"]') as HTMLElement;
    fireEvent.click(btn);
    // All three option items should be present
    expect(document.querySelector('[data-testid="source-option-asteria-7"]')).not.toBeNull();
    expect(document.querySelector('[data-testid="source-option-juno-pj62-v1"]')).not.toBeNull();
    expect(document.querySelector('[data-testid="source-option-juno-pj62-v2"]')).not.toBeNull();
  });

  it('dropdown contains ASTERIA-7 display name', () => {
    render(React.createElement(ScenarioSwitcher, {
      sources: ALL_SOURCES,
      activeSourceId: 'asteria-7',
      switching: false,
      onSelectSource: vi.fn(),
    }));
    const btn = document.querySelector('[data-testid="scenario-switcher-trigger"]') as HTMLElement;
    fireEvent.click(btn);
    expect(document.body.textContent).toContain('ASTERIA-7');
  });

  it('dropdown contains Juno PJ62 Historical V1 display name', () => {
    render(React.createElement(ScenarioSwitcher, {
      sources: ALL_SOURCES,
      activeSourceId: 'asteria-7',
      switching: false,
      onSelectSource: vi.fn(),
    }));
    const btn = document.querySelector('[data-testid="scenario-switcher-trigger"]') as HTMLElement;
    fireEvent.click(btn);
    expect(document.body.textContent).toContain('Juno PJ62 Historical V1');
  });

  it('dropdown contains Juno PJ62 Historical V2 display name', () => {
    render(React.createElement(ScenarioSwitcher, {
      sources: ALL_SOURCES,
      activeSourceId: 'asteria-7',
      switching: false,
      onSelectSource: vi.fn(),
    }));
    const btn = document.querySelector('[data-testid="scenario-switcher-trigger"]') as HTMLElement;
    fireEvent.click(btn);
    expect(document.body.textContent).toContain('Juno PJ62 Historical V2');
  });
});

// ── onSelectSource called with source_id only ─────────────────────────────────

describe('ScenarioSwitcher — selection', () => {
  it('calls onSelectSource with the source_id string when user picks a different source', () => {
    const onSelect = vi.fn();
    render(React.createElement(ScenarioSwitcher, {
      sources: ALL_SOURCES,
      activeSourceId: 'asteria-7',
      switching: false,
      onSelectSource: onSelect,
    }));
    // Open dropdown
    const btn = document.querySelector('[data-testid="scenario-switcher-trigger"]') as HTMLElement;
    fireEvent.click(btn);
    // Click V2
    const v2btn = document.querySelector('[data-testid="source-option-juno-pj62-v2"]') as HTMLElement;
    fireEvent.click(v2btn);
    expect(onSelect).toHaveBeenCalledOnce();
    expect(onSelect).toHaveBeenCalledWith('juno-pj62-v2');
  });

  it('never calls onSelectSource with a filesystem path', () => {
    const onSelect = vi.fn();
    render(React.createElement(ScenarioSwitcher, {
      sources: ALL_SOURCES,
      activeSourceId: 'asteria-7',
      switching: false,
      onSelectSource: onSelect,
    }));
    const btn = document.querySelector('[data-testid="scenario-switcher-trigger"]') as HTMLElement;
    fireEvent.click(btn);
    const v1btn = document.querySelector('[data-testid="source-option-juno-pj62-v1"]') as HTMLElement;
    fireEvent.click(v1btn);
    // The argument must not contain path separators or look like a filesystem path
    const arg = onSelect.mock.calls[0]?.[0] as string;
    expect(arg).not.toContain('/');
    expect(arg).not.toContain('\\');
    expect(arg).not.toContain('.json');
  });

  it('does NOT call onSelectSource when same source is selected', () => {
    const onSelect = vi.fn();
    render(React.createElement(ScenarioSwitcher, {
      sources: ALL_SOURCES,
      activeSourceId: 'asteria-7',
      switching: false,
      onSelectSource: onSelect,
    }));
    const btn = document.querySelector('[data-testid="scenario-switcher-trigger"]') as HTMLElement;
    fireEvent.click(btn);
    const asteriaBtn = document.querySelector('[data-testid="source-option-asteria-7"]') as HTMLElement;
    fireEvent.click(asteriaBtn);
    expect(onSelect).not.toHaveBeenCalled();
  });
});

// ── Switching state disables control ─────────────────────────────────────────

describe('ScenarioSwitcher — switching state', () => {
  it('disables the trigger button when switching=true', () => {
    render(React.createElement(ScenarioSwitcher, {
      sources: ALL_SOURCES,
      activeSourceId: 'asteria-7',
      switching: true,
      onSelectSource: vi.fn(),
    }));
    const btn = document.querySelector('[data-testid="scenario-switcher-trigger"]') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it('shows "Switching…" text when switching=true', () => {
    render(React.createElement(ScenarioSwitcher, {
      sources: ALL_SOURCES,
      activeSourceId: 'asteria-7',
      switching: true,
      onSelectSource: vi.fn(),
    }));
    expect(document.body.textContent).toContain('Switching');
  });

  it('does not open dropdown when disabled', () => {
    render(React.createElement(ScenarioSwitcher, {
      sources: ALL_SOURCES,
      activeSourceId: 'asteria-7',
      switching: true,
      onSelectSource: vi.fn(),
    }));
    const btn = document.querySelector('[data-testid="scenario-switcher-trigger"]') as HTMLElement;
    fireEvent.click(btn);
    // Dropdown should not appear
    expect(document.querySelector('[data-testid="source-option-asteria-7"]')).toBeNull();
  });
});

// ── Historical badge is HIST, not LIVE ────────────────────────────────────────

describe('ScenarioSwitcher — historical badge not LIVE', () => {
  it('shows HIST REPLAY badge for historical sources in dropdown, not LIVE', () => {
    render(React.createElement(ScenarioSwitcher, {
      sources: ALL_SOURCES,
      activeSourceId: 'asteria-7',
      switching: false,
      onSelectSource: vi.fn(),
    }));
    const btn = document.querySelector('[data-testid="scenario-switcher-trigger"]') as HTMLElement;
    fireEvent.click(btn);
    const text = document.body.textContent?.toUpperCase() ?? '';
    // Historical sources must show HIST, not LIVE
    expect(text).toContain('HIST');
    expect(text).not.toContain('LIVE');
    expect(text).not.toContain('REAL-TIME');
  });

  it('shows SYNTHETIC badge for ASTERIA in dropdown', () => {
    render(React.createElement(ScenarioSwitcher, {
      sources: ALL_SOURCES,
      activeSourceId: 'juno-pj62-v2',
      switching: false,
      onSelectSource: vi.fn(),
    }));
    const btn = document.querySelector('[data-testid="scenario-switcher-trigger"]') as HTMLElement;
    fireEvent.click(btn);
    const text = document.body.textContent?.toUpperCase() ?? '';
    expect(text).toContain('SYNTHETIC');
  });
});

// ── Error display ─────────────────────────────────────────────────────────────

describe('ScenarioSwitcher — error UX', () => {
  it('shows error message when error prop is provided', () => {
    render(React.createElement(ScenarioSwitcher, {
      sources: ALL_SOURCES,
      activeSourceId: 'asteria-7',
      switching: false,
      onSelectSource: vi.fn(),
      error: 'Failed to switch scenario. Current scenario remains active.',
    }));
    const errorEl = document.querySelector('[data-testid="scenario-switcher-error"]');
    expect(errorEl).not.toBeNull();
    expect(errorEl?.textContent).toContain('Failed to switch scenario');
  });

  it('does not show error element when error is null', () => {
    render(React.createElement(ScenarioSwitcher, {
      sources: ALL_SOURCES,
      activeSourceId: 'asteria-7',
      switching: false,
      onSelectSource: vi.fn(),
      error: null,
    }));
    expect(document.querySelector('[data-testid="scenario-switcher-error"]')).toBeNull();
  });
});

// ── Active source identification ──────────────────────────────────────────────

describe('ScenarioSwitcher — active source display', () => {
  it('shows SYNTHETIC in trigger when ASTERIA is active', () => {
    render(React.createElement(ScenarioSwitcher, {
      sources: ALL_SOURCES,
      activeSourceId: 'asteria-7',
      switching: false,
      onSelectSource: vi.fn(),
    }));
    const btn = document.querySelector('[data-testid="scenario-switcher-trigger"]');
    expect(btn?.textContent?.toUpperCase()).toContain('SYNTHETIC');
  });

  it('shows HIST in trigger when V2 is active', () => {
    render(React.createElement(ScenarioSwitcher, {
      sources: ALL_SOURCES,
      activeSourceId: 'juno-pj62-v2',
      switching: false,
      onSelectSource: vi.fn(),
    }));
    const btn = document.querySelector('[data-testid="scenario-switcher-trigger"]');
    expect(btn?.textContent?.toUpperCase()).toContain('HIST');
  });
});
