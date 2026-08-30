/**
 * ConfigPanel.test.tsx — Phase 7A legacy scenario selector removal tests.
 *
 * Tests verify:
 *   C1  "Active Scenario" heading is NOT present
 *   C2  "Switch to this scenario" button is NOT present
 *   C3  Legacy scenario filenames (degraded_link.json, etc.) are NOT present
 *   C4  "legacy packets" text is NOT present
 *   C5  "Switching scenarios resets" note is NOT present
 *   C6  "Historical replay is currently active" note is NOT present
 *   C7  Configuration header IS present
 *   C8  "Main Control Layout" section IS present
 *   C9  "3D View" section IS present
 *   C10 "Camera" section IS present
 *   C11 "Restore Defaults" section IS present
 *   C12 "About" section IS present
 *   C13 Starfield toggle IS present
 *   C14 Communication link toggle IS present
 *   C15 Scene labels toggle IS present
 *   C16 Smooth transitions toggle IS present
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import React from 'react';
import { ConfigPanel } from '../ConfigPanel';
import type { ViewSettings } from '../../hooks/useViewSettings';

const NOOP = () => {};

const VIEW_SETTINGS: ViewSettings = {
  showStarfield: true,
  showCommLink: true,
  showLabels: true,
  smoothCamera: true,
  density: 'comfortable',
};

function renderConfig() {
  return render(
    React.createElement(ConfigPanel, {
      settings: VIEW_SETTINGS,
      onUpdate: NOOP as any,
      onResetSettings: NOOP,
      onResetPanelWidth: NOOP,
      panelWidth: 480,
      panelDefaultWidth: 480,
    })
  );
}

// ── C1–C6: Legacy scenario UI must NOT appear ─────────────────────────────────

describe('C1-C6 — Legacy scenario UI removed', () => {
  it('C1: does NOT render "Active Scenario" heading', () => {
    renderConfig();
    expect(screen.queryByText('Active Scenario')).not.toBeInTheDocument();
  });

  it('C2: does NOT render "Switch to this scenario" button', () => {
    renderConfig();
    expect(screen.queryByText('Switch to this scenario')).not.toBeInTheDocument();
  });

  it('C3: does NOT render degraded_link.json filename', () => {
    renderConfig();
    expect(screen.queryByText('degraded_link.json')).not.toBeInTheDocument();
  });

  it('C3: does NOT render nominal_pass.json filename', () => {
    renderConfig();
    expect(screen.queryByText('nominal_pass.json')).not.toBeInTheDocument();
  });

  it('C3: does NOT render mission_data_v2.json filename', () => {
    renderConfig();
    expect(screen.queryByText('mission_data_v2.json')).not.toBeInTheDocument();
  });

  it('C3: does NOT render mission_data_v3.json filename', () => {
    renderConfig();
    expect(screen.queryByText('mission_data_v3.json')).not.toBeInTheDocument();
  });

  it('C3: does NOT render asteria7_thermal_priority_contact_v1.json filename', () => {
    renderConfig();
    expect(screen.queryByText('asteria7_thermal_priority_contact_v1.json')).not.toBeInTheDocument();
  });

  it('C4: does NOT render "legacy packets" text', () => {
    renderConfig();
    expect(screen.queryByText('legacy packets')).not.toBeInTheDocument();
  });

  it('C5: does NOT render "Switching scenarios resets" text', () => {
    renderConfig();
    expect(document.body.textContent).not.toContain('Switching scenarios resets');
  });

  it('C6: does NOT render "Historical replay is currently active" note', () => {
    renderConfig();
    expect(screen.queryByText(/Historical replay is currently active/)).not.toBeInTheDocument();
  });
});

// ── C7–C16: Legitimate Configuration settings MUST appear ─────────────────────

describe('C7-C16 — Legitimate Configuration settings preserved', () => {
  it('C7: renders "Configuration" header', () => {
    renderConfig();
    expect(screen.getByText('Configuration')).toBeInTheDocument();
  });

  it('C8: renders "Main Control Layout" section', () => {
    renderConfig();
    expect(screen.getByText('Main Control Layout')).toBeInTheDocument();
  });

  it('C9: renders "3D View" section', () => {
    renderConfig();
    expect(screen.getByText('3D View')).toBeInTheDocument();
  });

  it('C10: renders "Camera" section', () => {
    renderConfig();
    expect(screen.getByText('Camera')).toBeInTheDocument();
  });

  it('C11: renders "Restore Defaults" section', () => {
    renderConfig();
    expect(screen.getByText('Restore Defaults')).toBeInTheDocument();
  });

  it('C12: renders "About" section', () => {
    renderConfig();
    expect(screen.getByText('About')).toBeInTheDocument();
  });

  it('C13: renders "Starfield" toggle', () => {
    renderConfig();
    expect(screen.getByText('Starfield')).toBeInTheDocument();
  });

  it('C14: renders "Communication link" toggle', () => {
    renderConfig();
    expect(screen.getByText('Communication link')).toBeInTheDocument();
  });

  it('C15: renders "Scene labels" toggle', () => {
    renderConfig();
    expect(screen.getByText('Scene labels')).toBeInTheDocument();
  });

  it('C16: renders "Smooth transitions" toggle', () => {
    renderConfig();
    expect(screen.getByText('Smooth transitions')).toBeInTheDocument();
  });
});

// ── Confirm ConfigPanel accepts no scenario props ─────────────────────────────

describe('ConfigPanel — no scenario props required', () => {
  it('renders correctly without any scenario props (no availableScenarios prop)', () => {
    // This test verifies that ConfigPanel's type signature no longer requires
    // availableScenarios, activeScenarioPath, scenarioSwitching, or onSwitchScenario.
    // The component must render without them.
    renderConfig();
    expect(screen.getByText('Configuration')).toBeInTheDocument();
    expect(screen.queryByText('Active Scenario')).not.toBeInTheDocument();
  });
});
