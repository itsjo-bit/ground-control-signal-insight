/**
 * LandingPage — GCSI product landing page.
 *
 * Sections:
 *   1. TopNav
 *   2. Hero (3D MissionViewport + editorial copy)
 *   3. Problem
 *   4. How It Works (4 steps)
 *   5. Capabilities
 *   6. Scenarios
 *   7. Trust / Evidence
 *   8. Console Preview CTA
 *   9. Final CTA
 *  10. Footer
 *
 * Routing: pure React state — no router dependency needed.
 * Parent (App) switches between 'landing' and 'console'.
 */

import React, { Suspense } from 'react';
import { Canvas } from '@react-three/fiber';
import { MissionScene, CAMERA_PRESETS } from '../components/scene/MissionScene';

// ── Shared constants ──────────────────────────────────────────────────────────

const MONO = '"IBM Plex Mono", ui-monospace, "SF Mono", monospace';
const SANS = '"IBM Plex Sans", system-ui, sans-serif';

const C = {
  bg:         '#0d1117',
  surface:    '#161b22',
  surfaceUp:  '#1c2128',
  border:     '#30363d',
  borderSub:  '#21262d',
  text:       '#e6edf3',
  textSec:    '#8b949e',
  textDim:    '#656d76',
  accent:     '#2f81f7',
  accentGlow: 'rgba(47,129,247,0.15)',
  amber:      '#d29922',
  green:      '#3fb950',
  red:        '#f85149',
};

// ── Scene fallback ────────────────────────────────────────────────────────────

function HeroSceneFallback() {
  return (
    <div style={{
      position: 'absolute', inset: 0,
      background: 'linear-gradient(160deg, #050910 0%, #0b1525 60%, #0d1117 100%)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <span style={{ fontFamily: MONO, fontSize: 11, color: 'rgba(100,160,210,0.4)',
        letterSpacing: '0.1em', textTransform: 'uppercase' }}>
        Initializing Scene…
      </span>
    </div>
  );
}

// ── Hero 3D scene (read-only, no controls needed) ─────────────────────────────

function HeroScene() {
  return (
    <Canvas
      camera={{ position: CAMERA_PRESETS.default.pos.toArray() as [number,number,number], fov: 42, near: 0.5, far: 1200 }}
      shadows={false}
      dpr={Math.min(typeof window !== 'undefined' ? window.devicePixelRatio : 1, 2)}
      gl={{ antialias: true, alpha: false, powerPreference: 'high-performance' }}
      style={{ background: '#050910', position: 'absolute', inset: 0 }}
    >
      <MissionScene
        linkState={null}
        missionState={null}
        distanceKm={null}
        approvalPhase="idle"
        cameraTarget="default"
        showStarfield
        showLabels={false}
        showCommLink
        smoothCamera={false}
      />
    </Canvas>
  );
}

// ── Top navigation ────────────────────────────────────────────────────────────

interface TopNavProps { onLaunch: () => void }

function TopNav({ onLaunch }: TopNavProps) {
  return (
    <header style={{
      position: 'fixed', top: 0, left: 0, right: 0, zIndex: 100,
      height: 52,
      background: 'rgba(13,17,23,0.88)',
      backdropFilter: 'blur(12px)',
      borderBottom: `1px solid ${C.border}`,
      display: 'flex', alignItems: 'center',
      padding: '0 32px',
      gap: 0,
    }}>
      {/* Wordmark */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginRight: 40, flexShrink: 0 }}>
        <span style={{ fontFamily: MONO, fontSize: 15, fontWeight: 600, color: C.text, letterSpacing: '0.04em' }}>
          GCSI
        </span>
        <span style={{ fontFamily: SANS, fontSize: 11, color: C.textDim, letterSpacing: '0.06em',
          textTransform: 'uppercase', fontWeight: 500 }}>
          Ground Control Signal Insight
        </span>
      </div>

      {/* Nav anchors */}
      <nav style={{ display: 'flex', gap: 2, flex: 1 }}>
        {['Problem', 'How It Works', 'Scenarios', 'Trust'].map((label) => (
          <a
            key={label}
            href={`#${label.toLowerCase().replace(/\s+/g, '-')}`}
            style={{
              fontFamily: SANS, fontSize: 12, color: C.textSec,
              textDecoration: 'none', padding: '4px 12px',
              borderRadius: 4, transition: 'color 0.15s',
            }}
            onMouseEnter={e => (e.currentTarget.style.color = C.text)}
            onMouseLeave={e => (e.currentTarget.style.color = C.textSec)}
          >
            {label}
          </a>
        ))}
      </nav>

      {/* CTA buttons */}
      <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
        <a
          href="https://github.com/itsjo-bit/ground-control-signal-insight"
          target="_blank" rel="noopener noreferrer"
          style={{
            fontFamily: SANS, fontSize: 12, color: C.textSec,
            textDecoration: 'none', padding: '5px 14px',
            border: `1px solid ${C.border}`, borderRadius: 4,
            transition: 'border-color 0.15s, color 0.15s',
          }}
          onMouseEnter={e => { e.currentTarget.style.borderColor = C.accent; e.currentTarget.style.color = C.text; }}
          onMouseLeave={e => { e.currentTarget.style.borderColor = C.border; e.currentTarget.style.color = C.textSec; }}
        >
          GitHub
        </a>
        <button
          onClick={onLaunch}
          style={{
            fontFamily: SANS, fontSize: 12, fontWeight: 600,
            color: '#fff', background: C.accent,
            border: 'none', borderRadius: 4,
            padding: '5px 16px', cursor: 'pointer',
            transition: 'background 0.15s',
          }}
          onMouseEnter={e => (e.currentTarget.style.background = '#4a94f8')}
          onMouseLeave={e => (e.currentTarget.style.background = C.accent)}
        >
          Launch Console
        </button>
      </div>
    </header>
  );
}

// ── Hero section ──────────────────────────────────────────────────────────────

interface HeroProps { onLaunch: () => void; onLaunchWithSource?: (sourceId: string) => void }

function Hero({ onLaunch, onLaunchWithSource }: HeroProps) {
  return (
    <section style={{
      position: 'relative',
      height: '100vh', minHeight: 640,
      overflow: 'hidden',
      display: 'flex', alignItems: 'center',
    }}>
      {/* 3D scene fills the entire hero */}
      <div style={{ position: 'absolute', inset: 0, zIndex: 0 }}>
        <Suspense fallback={<HeroSceneFallback />}>
          <HeroScene />
        </Suspense>
      </div>

      {/* Dark gradient overlay — left fade, keeps copy legible */}
      <div style={{
        position: 'absolute', inset: 0, zIndex: 1,
        background: 'linear-gradient(105deg, rgba(13,17,23,0.92) 0%, rgba(13,17,23,0.75) 45%, rgba(13,17,23,0.18) 75%, rgba(13,17,23,0.05) 100%)',
      }} />
      <div style={{
        position: 'absolute', bottom: 0, left: 0, right: 0, height: 180, zIndex: 1,
        background: `linear-gradient(to top, ${C.bg}, transparent)`,
      }} />

      {/* Editorial copy — left-aligned */}
      <div style={{
        position: 'relative', zIndex: 2,
        maxWidth: 580,
        marginLeft: 'clamp(32px, 7vw, 120px)',
        marginTop: 52, /* below fixed nav */
        padding: '0 0 60px',
      }}>
        {/* Eyebrow */}
        <div style={{
          fontFamily: MONO, fontSize: 10, fontWeight: 500,
          color: C.accent, letterSpacing: '0.18em',
          textTransform: 'uppercase', marginBottom: 20,
          display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <span style={{
            display: 'inline-block', width: 24, height: 1,
            background: C.accent, opacity: 0.6,
          }} />
          AI-Assisted Mission Decision Support
        </div>

        {/* Main headline */}
        <h1 style={{
          fontFamily: SANS, fontSize: 'clamp(36px, 4.5vw, 60px)',
          fontWeight: 700, color: C.text,
          lineHeight: 1.1, letterSpacing: '-0.02em',
          marginBottom: 20,
        }}>
          Decide What Reaches<br />
          Earth First.
        </h1>

        {/* Sub-headline */}
        <p style={{
          fontFamily: SANS, fontSize: 15,
          color: C.textSec, lineHeight: 1.65,
          marginBottom: 32, maxWidth: 480,
        }}>
          Spacecraft generate far more data than a communication window can carry.
          GCSI helps mission operators prioritize what matters most — combining
          AI-assisted reasoning with deterministic validation and human approval.
        </p>

        {/* CTA row */}
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 36 }}>
          <button
            onClick={onLaunch}
            style={{
              fontFamily: SANS, fontSize: 13, fontWeight: 600,
              color: '#fff', background: C.accent,
              border: 'none', borderRadius: 4,
              padding: '11px 24px', cursor: 'pointer',
              transition: 'background 0.15s',
            }}
            onMouseEnter={e => (e.currentTarget.style.background = '#4a94f8')}
            onMouseLeave={e => (e.currentTarget.style.background = C.accent)}
          >
            Launch Mission Console →
          </button>
          <button
            onClick={() => onLaunchWithSource ? onLaunchWithSource('juno-pj62-v2') : onLaunch()}
            style={{
              fontFamily: SANS, fontSize: 13, fontWeight: 500,
              color: C.text, background: 'transparent',
              border: `1px solid ${C.border}`, borderRadius: 4,
              padding: '11px 22px', cursor: 'pointer',
              transition: 'border-color 0.15s',
            }}
            onMouseEnter={e => (e.currentTarget.style.borderColor = C.accent)}
            onMouseLeave={e => (e.currentTarget.style.borderColor = C.border)}
          >
            Explore Juno PJ62 Replay
          </button>
        </div>

        {/* Trust micro-labels */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px 20px' }}>
          {[
            { label: 'Verified NASA/JPL/PDS evidence', color: C.green },
            { label: 'Deterministic constraints', color: C.accent },
            { label: 'Human-in-the-loop approval', color: C.amber },
          ].map(({ label, color }) => (
            <span key={label} style={{
              fontFamily: MONO, fontSize: 9, color,
              letterSpacing: '0.10em', textTransform: 'uppercase',
              display: 'flex', alignItems: 'center', gap: 5,
            }}>
              <span style={{ display: 'inline-block', width: 5, height: 5, borderRadius: '50%', background: color }} />
              {label}
            </span>
          ))}
        </div>
      </div>

      {/* Telemetry micro-labels — floating right side, pass-start baseline */}
      <div style={{
        position: 'absolute', right: 'clamp(20px, 4vw, 60px)', top: '50%',
        transform: 'translateY(-40%)',
        zIndex: 2, pointerEvents: 'none',
        display: 'flex', flexDirection: 'column', gap: 12,
        alignItems: 'flex-end',
      }}>
        {/* Context label */}
        <div style={{
          fontFamily: MONO, fontSize: 7, color: C.amber,
          letterSpacing: '0.14em', textTransform: 'uppercase',
          textAlign: 'right', opacity: 0.85,
        }}>
          Juno PJ62 V2 · Modeled Pass-Start Baseline
        </div>
        {[
          { key: 'PRODUCTS',  val: '403',      unit: 'eligible products' },
          { key: 'QUEUED',    val: '9.35',     unit: 'Gbit modeled queue' },
          { key: 'CAPACITY',  val: '81',       unit: 'Mbit modeled pass-start capacity' },
          { key: 'PRESSURE',  val: '~115×',    unit: 'modeled pass-start pressure' },
        ].map(({ key, val, unit }) => (
          <div key={key} style={{
            background: 'rgba(13,17,23,0.70)',
            border: `1px solid ${C.border}`,
            borderRadius: 3, padding: '6px 10px',
            textAlign: 'right',
          }}>
            <div style={{ fontFamily: MONO, fontSize: 8, color: C.textDim,
              letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 2 }}>
              {key}
            </div>
            <div style={{ fontFamily: MONO, fontSize: 18, fontWeight: 600, color: C.text, lineHeight: 1 }}>
              {val}
            </div>
            <div style={{ fontFamily: MONO, fontSize: 8, color: C.textSec, letterSpacing: '0.08em', marginTop: 2 }}>
              {unit}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

// ── Section wrapper ───────────────────────────────────────────────────────────

function Section({ id, children, style }: { id?: string; children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <section id={id} style={{
      padding: 'clamp(64px, 8vw, 112px) clamp(24px, 8vw, 120px)',
      ...style,
    }}>
      {children}
    </section>
  );
}

function SectionLabel({ children }: { children: string }) {
  return (
    <div style={{
      fontFamily: MONO, fontSize: 10, fontWeight: 500,
      color: C.accent, letterSpacing: '0.18em',
      textTransform: 'uppercase', marginBottom: 16,
      display: 'flex', alignItems: 'center', gap: 8,
    }}>
      <span style={{ display: 'inline-block', width: 20, height: 1, background: C.accent, opacity: 0.5 }} />
      {children}
    </div>
  );
}

function H2({ children }: { children: React.ReactNode }) {
  return (
    <h2 style={{
      fontFamily: SANS, fontSize: 'clamp(28px, 3vw, 38px)',
      fontWeight: 700, color: C.text,
      lineHeight: 1.15, letterSpacing: '-0.02em',
      marginBottom: 16,
    }}>
      {children}
    </h2>
  );
}

// ── Problem section ───────────────────────────────────────────────────────────

function ProblemSection() {
  const stats = [
    { val: '~115×', desc: 'Modeled Juno PJ62 V2 pass-start queue pressure' },
    { val: '~50 min', desc: 'One-way signal propagation at the Juno replay epoch' },
    { val: '15 min', desc: 'Modeled initial communication window' },
  ];

  return (
    <Section id="problem" style={{ background: C.surface }}>
      <div style={{ maxWidth: 900, margin: '0 auto' }}>
        <SectionLabel>The Problem</SectionLabel>
        <H2>Spacecraft Can't Send Everything.</H2>
        <p style={{ fontFamily: SANS, fontSize: 15, color: C.textSec, lineHeight: 1.7,
          maxWidth: 620, marginBottom: 48 }}>
          Every communication window is a bottleneck. Missions produce vastly more
          science data than the available downlink budget can carry. Without a
          principled prioritization process, high-value observations get delayed —
          or lost entirely.
        </p>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16 }}>
          {stats.map(({ val, desc }) => (
            <div key={val} style={{
              background: C.surfaceUp, border: `1px solid ${C.border}`,
              borderRadius: 4, padding: '20px 22px',
            }}>
              <div style={{ fontFamily: MONO, fontSize: 28, fontWeight: 700, color: C.text, marginBottom: 8 }}>
                {val}
              </div>
              <div style={{ fontFamily: SANS, fontSize: 12, color: C.textSec, lineHeight: 1.55 }}>
                {desc}
              </div>
            </div>
          ))}
        </div>

        <p style={{ fontFamily: SANS, fontSize: 14, color: C.textDim, lineHeight: 1.65,
          maxWidth: 640, marginTop: 32 }}>
          A naive AI answer is dangerous. A purely manual process is too slow.
          GCSI provides a structured decision workspace that combines AI-assisted triage
          with deterministic feasibility validation — and keeps the human operator in authority.
        </p>
      </div>
    </Section>
  );
}

// ── How it works ──────────────────────────────────────────────────────────────

function HowItWorksSection() {
  const steps = [
    {
      n: '01', title: 'Ingest',
      body: 'Load verified mission products and telemetry context from authoritative source representations. No hallucinated inventory.',
      badge: 'Source-Verified',
      color: C.accent,
    },
    {
      n: '02', title: 'Analyze',
      body: 'AI-assisted ranking and triage considers mission value, anomaly status, urgency, and science priority across all eligible products.',
      badge: 'AI-Assisted',
      color: C.accent,
    },
    {
      n: '03', title: 'Validate',
      body: 'Deterministic rules and communication constraint models evaluate whether the proposed plan is feasible within the actual downlink budget.',
      badge: 'Deterministic Check',
      color: C.amber,
    },
    {
      n: '04', title: 'Approve',
      body: 'The human operator reviews AI reasoning and feasibility evidence, then makes the final mission-authority decision. No auto-approval.',
      badge: 'Human Authority',
      color: C.green,
    },
  ];

  return (
    <Section id="how-it-works">
      <div style={{ maxWidth: 900, margin: '0 auto' }}>
        <SectionLabel>How It Works</SectionLabel>
        <H2>Four Stages from Data to Decision.</H2>
        <p style={{ fontFamily: SANS, fontSize: 14, color: C.textSec, lineHeight: 1.65,
          maxWidth: 560, marginBottom: 48 }}>
          GCSI structures the prioritization process across four deterministic stages,
          each with clear responsibilities and verifiable outputs.
        </p>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 16 }}>
          {steps.map(({ n, title, body, badge, color }) => (
            <div key={n} style={{
              background: C.surface, border: `1px solid ${C.border}`,
              borderTop: `2px solid ${color}`,
              borderRadius: 4, padding: '22px 20px',
              position: 'relative',
            }}>
              <div style={{ fontFamily: MONO, fontSize: 10, color: C.textDim,
                letterSpacing: '0.1em', marginBottom: 10 }}>
                {n}
              </div>
              <div style={{ fontFamily: SANS, fontSize: 16, fontWeight: 700,
                color: C.text, marginBottom: 10 }}>
                {title}
              </div>
              <div style={{ fontFamily: SANS, fontSize: 12, color: C.textSec,
                lineHeight: 1.6, marginBottom: 14 }}>
                {body}
              </div>
              <span style={{
                fontFamily: MONO, fontSize: 8, color,
                letterSpacing: '0.12em', textTransform: 'uppercase',
                border: `1px solid ${color}22`, borderRadius: 2,
                padding: '2px 6px', background: `${color}11`,
              }}>
                {badge}
              </span>
            </div>
          ))}
        </div>
      </div>
    </Section>
  );
}

// ── Capabilities ──────────────────────────────────────────────────────────────

function CapabilitiesSection() {
  const caps = [
    {
      title: 'AI Prioritization',
      desc: 'LLM-assisted ranking surfaces mission-critical products across instrument categories, anomaly status, and science value.',
      badge: 'AI Core',
    },
    {
      title: 'Deterministic Validation',
      desc: 'Every candidate plan is evaluated against real communication constraint models — no plan exceeds the actual downlink budget.',
      badge: 'Feasibility Engine',
    },
    {
      title: 'Human-in-the-Loop Approval',
      desc: 'Operators review AI reasoning, constraint evidence, and comparative plans before issuing final mission approval.',
      badge: 'Human Authority',
    },
    {
      title: 'Historical Replay',
      desc: 'Full reconstruction of Juno PJ62 using verified NASA/JPL/PDS product inventories, modeled link budgets, and real orbital geometry.',
      badge: 'Verified Source',
    },
    {
      title: 'Source Provenance Awareness',
      desc: 'Every product carries an authoritative, derived, or modeled provenance label — no undifferentiated data blobs.',
      badge: 'Traceability',
    },
    {
      title: 'Scenario Switching',
      desc: 'Switch between synthetic scenarios (ASTERIA-7) and historical replay (Juno PJ62) with full context isolation.',
      badge: 'Multi-Mission',
    },
    {
      title: 'Ground Reception Reporting',
      desc: 'Outcome telemetry records received products, discarded data, retry events, and final signal confirmation.',
      badge: 'End-to-End',
    },
    {
      title: 'What-If Analysis',
      desc: 'Evaluate alternative prioritization plans side-by-side with AI and manual orderings before committing.',
      badge: 'Comparison',
    },
  ];

  return (
    <Section style={{ background: C.surface }}>
      <div style={{ maxWidth: 960, margin: '0 auto' }}>
        <SectionLabel>Capabilities</SectionLabel>
        <H2>Built for the Complexity of Real Missions.</H2>
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
          gap: 12, marginTop: 40,
        }}>
          {caps.map(({ title, desc, badge }) => (
            <div key={title} style={{
              background: C.surfaceUp, border: `1px solid ${C.border}`,
              borderRadius: 4, padding: '18px 18px',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between',
                alignItems: 'flex-start', marginBottom: 8, gap: 8 }}>
                <div style={{ fontFamily: SANS, fontSize: 13, fontWeight: 600, color: C.text, lineHeight: 1.3 }}>
                  {title}
                </div>
                <span style={{
                  fontFamily: MONO, fontSize: 7, color: C.textDim,
                  letterSpacing: '0.10em', textTransform: 'uppercase',
                  border: `1px solid ${C.border}`, borderRadius: 2,
                  padding: '1px 5px', whiteSpace: 'nowrap', flexShrink: 0,
                }}>
                  {badge}
                </span>
              </div>
              <div style={{ fontFamily: SANS, fontSize: 12, color: C.textSec, lineHeight: 1.6 }}>
                {desc}
              </div>
            </div>
          ))}
        </div>
      </div>
    </Section>
  );
}

// ── Scenarios ─────────────────────────────────────────────────────────────────

interface ScenariosProps { onLaunch: () => void; onLaunchWithSource?: (sourceId: string) => void }

function ScenariosSection({ onLaunch, onLaunchWithSource }: ScenariosProps) {
  return (
    <Section id="scenarios">
      <div style={{ maxWidth: 900, margin: '0 auto' }}>
        <SectionLabel>Mission Scenarios</SectionLabel>
        <H2>Two Environments to Explore.</H2>
        <p style={{ fontFamily: SANS, fontSize: 14, color: C.textSec, lineHeight: 1.65,
          maxWidth: 560, marginBottom: 48 }}>
          GCSI ships with two mission scenarios: a synthetic testbed for workflow
          evaluation and a historical replay grounded in verified NASA/JPL/PDS evidence.
        </p>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 20 }}>
          {/* ASTERIA-7 */}
          <div style={{
            background: C.surface, border: `1px solid ${C.border}`,
            borderRadius: 4, padding: '28px 28px',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <div style={{ fontFamily: MONO, fontSize: 9, color: C.accent,
                letterSpacing: '0.14em', textTransform: 'uppercase' }}>
                Synthetic Scenario
              </div>
              <span style={{
                fontFamily: MONO, fontSize: 7, color: C.accent,
                border: `1px solid ${C.accent}33`, borderRadius: 2,
                padding: '1px 6px', background: `${C.accent}11`, letterSpacing: '0.10em',
                textTransform: 'uppercase',
              }}>
                Testbed
              </span>
            </div>
            <div style={{ fontFamily: SANS, fontSize: 20, fontWeight: 700, color: C.text, marginBottom: 10 }}>
              ASTERIA-7
            </div>
            <div style={{ fontFamily: SANS, fontSize: 13, color: C.textSec, lineHeight: 1.65, marginBottom: 20 }}>
              Pre-contact anomaly triage scenario for simulation and workflow testing.
              Synthetic telemetry with realistic data product inventories, communication
              constraints, and instrument priorities.
            </div>
            <div style={{ fontFamily: MONO, fontSize: 9, color: C.textDim,
              letterSpacing: '0.10em', textTransform: 'uppercase', marginBottom: 16 }}>
              Designed For · Onboarding · Workflow Testing · Demo
            </div>
            <button onClick={() => onLaunchWithSource ? onLaunchWithSource('asteria-7') : onLaunch()} style={{
              fontFamily: SANS, fontSize: 12, fontWeight: 500,
              color: C.accent, background: 'transparent',
              border: `1px solid ${C.accent}44`, borderRadius: 3,
              padding: '7px 16px', cursor: 'pointer',
              transition: 'border-color 0.15s, background 0.15s',
            }}
              onMouseEnter={e => { e.currentTarget.style.borderColor = C.accent; e.currentTarget.style.background = C.accentGlow; }}
              onMouseLeave={e => { e.currentTarget.style.borderColor = `${C.accent}44`; e.currentTarget.style.background = 'transparent'; }}
            >
              Open ASTERIA-7 →
            </button>
          </div>

          {/* Juno PJ62 */}
          <div style={{
            background: C.surface, border: `1px solid ${C.border}`,
            borderRadius: 4, padding: '28px 28px',
            position: 'relative', overflow: 'hidden',
          }}>
            <div style={{
              position: 'absolute', top: 0, left: 0, right: 0, height: 2,
              background: `linear-gradient(90deg, ${C.amber}, transparent)`,
            }} />
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <div style={{ fontFamily: MONO, fontSize: 9, color: C.amber,
                letterSpacing: '0.14em', textTransform: 'uppercase' }}>
                Historical Replay
              </div>
              <span style={{
                fontFamily: MONO, fontSize: 7, color: C.amber,
                border: `1px solid ${C.amber}33`, borderRadius: 2,
                padding: '1px 6px', background: `${C.amber}11`, letterSpacing: '0.10em',
                textTransform: 'uppercase',
              }}>
                Verified
              </span>
            </div>
            <div style={{ fontFamily: SANS, fontSize: 20, fontWeight: 700, color: C.text, marginBottom: 10 }}>
              Juno PJ62
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.textDim,
                fontWeight: 400, marginLeft: 10 }}>
                Historical Replay V2
              </span>
            </div>
            <div style={{ fontFamily: SANS, fontSize: 13, color: C.textSec, lineHeight: 1.65, marginBottom: 20 }}>
              Historical replay grounded in verified NASA/JPL/PDS Juno PJ62 archive
              evidence, with explicit GCSI-modeled communication constraints derived
              from real orbital geometry.
            </div>
            <div style={{ fontFamily: MONO, fontSize: 9, color: C.textDim,
              letterSpacing: '0.10em', textTransform: 'uppercase', marginBottom: 16 }}>
              403 Products · 9.35 Gbit Queue · 81 Mbit Budget · ~115× Pressure
            </div>
            <button onClick={() => onLaunchWithSource ? onLaunchWithSource('juno-pj62-v2') : onLaunch()} style={{
              fontFamily: SANS, fontSize: 12, fontWeight: 500,
              color: C.amber, background: 'transparent',
              border: `1px solid ${C.amber}44`, borderRadius: 3,
              padding: '7px 16px', cursor: 'pointer',
              transition: 'border-color 0.15s, background 0.15s',
            }}
              onMouseEnter={e => { e.currentTarget.style.borderColor = C.amber; e.currentTarget.style.background = `${C.amber}12`; }}
              onMouseLeave={e => { e.currentTarget.style.borderColor = `${C.amber}44`; e.currentTarget.style.background = 'transparent'; }}
            >
              Open Juno PJ62 Replay →
            </button>
          </div>
        </div>
      </div>
    </Section>
  );
}

// ── Trust section ─────────────────────────────────────────────────────────────

function TrustSection() {
  const rows = [
    {
      label: 'Source Baseline',
      value: 'Verified',
      desc: 'Juno PJ62 product inventory sourced directly from NASA/JPL Planetary Data System',
      color: C.green,
    },
    {
      label: 'Data Products',
      value: '403',
      desc: 'Eligible products in the Juno PJ62 historical replay with instrument, size, and priority metadata',
      color: C.text,
    },
    {
      label: 'Comm Constraint Ratio',
      value: '~115×',
      desc: '9.35 Gbit modeled queue against 81 Mbit modeled pass-start downlink capacity — realistic mission pressure',
      color: C.amber,
    },
    {
      label: 'Provenance Model',
      value: '3-Tier',
      desc: 'Every product and context element carries an Authoritative / Derived / Modeled label',
      color: C.accent,
    },
    {
      label: 'Frontend Tests',
      value: 'Hundreds',
      desc: 'Automated frontend tests covering decision logic, layout, transmission accounting, and scenario switching',
      color: C.text,
    },
    {
      label: 'Backend Tests',
      value: 'Thousands',
      desc: 'Comprehensive Python test suite covering prioritization, constraint evaluation, replay activation, and source verification',
      color: C.text,
    },
    {
      label: 'Replay Activation',
      value: 'Offline-Safe',
      desc: 'No live external dependency required — full historical replay activates from local verified snapshot',
      color: C.green,
    },
    {
      label: 'Approval Model',
      value: 'Human Final',
      desc: 'The system never auto-approves. Every mission action requires explicit human operator confirmation',
      color: C.amber,
    },
  ];

  return (
    <Section id="trust" style={{ background: C.surface }}>
      <div style={{ maxWidth: 900, margin: '0 auto' }}>
        <SectionLabel>Trust & Evidence</SectionLabel>
        <H2>Engineering Rigour, Not Demo Theatre.</H2>
        <p style={{ fontFamily: SANS, fontSize: 14, color: C.textSec, lineHeight: 1.65,
          maxWidth: 560, marginBottom: 48 }}>
          GCSI is built around verifiable evidence, auditable provenance, and
          deterministic constraint models. Every claim the system makes is traceable
          to source data.
        </p>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))', gap: 1,
          border: `1px solid ${C.border}`, borderRadius: 4, overflow: 'hidden' }}>
          {rows.map(({ label, value, desc, color }) => (
            <div key={label} style={{
              background: C.surfaceUp,
              borderRight: `1px solid ${C.border}`,
              borderBottom: `1px solid ${C.border}`,
              padding: '16px 18px',
              display: 'flex', gap: 16, alignItems: 'flex-start',
            }}>
              <div style={{ flexShrink: 0, minWidth: 68, textAlign: 'right' }}>
                <div style={{ fontFamily: MONO, fontSize: 17, fontWeight: 700, color, lineHeight: 1 }}>
                  {value}
                </div>
              </div>
              <div>
                <div style={{ fontFamily: SANS, fontSize: 12, fontWeight: 600,
                  color: C.text, marginBottom: 4 }}>
                  {label}
                </div>
                <div style={{ fontFamily: SANS, fontSize: 11, color: C.textSec, lineHeight: 1.55 }}>
                  {desc}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </Section>
  );
}

// ── Console preview / CTA bridge ──────────────────────────────────────────────

interface ConsolePreviewProps { onLaunch: () => void }

function ConsolePreview({ onLaunch }: ConsolePreviewProps) {
  return (
    <Section>
      <div style={{ maxWidth: 900, margin: '0 auto', textAlign: 'center' }}>
        <SectionLabel>Mission Console</SectionLabel>
        <H2>The Decision Surface Lives Here.</H2>
        <p style={{ fontFamily: SANS, fontSize: 14, color: C.textSec, lineHeight: 1.65,
          maxWidth: 560, margin: '0 auto 40px' }}>
          Behind this landing page is a fully operational mission decision workspace —
          3D visualization, AI reasoning panel, transmission queue, feasibility
          evaluation, and human approval workflow.
        </p>

        {/* Faux console screenshot */}
        <div style={{
          background: C.surface, border: `1px solid ${C.border}`,
          borderRadius: 6, overflow: 'hidden',
          maxWidth: 760, margin: '0 auto 40px',
          boxShadow: '0 24px 64px rgba(0,0,0,0.5)',
        }}>
          {/* Window chrome */}
          <div style={{
            background: '#0d1117', borderBottom: `1px solid ${C.border}`,
            padding: '10px 14px',
            display: 'flex', alignItems: 'center', gap: 6,
          }}>
            <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#f85149', display: 'inline-block' }} />
            <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#d29922', display: 'inline-block' }} />
            <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#3fb950', display: 'inline-block' }} />
            <span style={{ fontFamily: MONO, fontSize: 10, color: C.textDim, marginLeft: 8, letterSpacing: '0.06em' }}>
              GCSI — Ground Control Signal Insight
            </span>
          </div>

          {/* Content mockup */}
          <div style={{ display: 'grid', gridTemplateColumns: '44px 1fr 240px', height: 320 }}>
            {/* Nav rail */}
            <div style={{ background: '#0d1117', borderRight: `1px solid ${C.border}`,
              display: 'flex', flexDirection: 'column', alignItems: 'center',
              paddingTop: 12, gap: 8 }}>
              {['▣', '◈', '⊞', '◎'].map((icon, i) => (
                <div key={i} style={{
                  width: 28, height: 28, borderRadius: 4,
                  background: i === 0 ? C.accentGlow : 'transparent',
                  border: `1px solid ${i === 0 ? C.accent + '44' : 'transparent'}`,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontFamily: MONO, fontSize: 13,
                  color: i === 0 ? C.accent : C.textDim,
                }}>
                  {icon}
                </div>
              ))}
            </div>

            {/* Main area: viewport + status column */}
            <div style={{ background: '#060a12', display: 'flex', alignItems: 'center',
              justifyContent: 'center', flexDirection: 'column', gap: 6 }}>
              <div style={{ fontFamily: MONO, fontSize: 9, color: 'rgba(100,160,210,0.4)',
                letterSpacing: '0.10em', textTransform: 'uppercase' }}>
                3D Mission View
              </div>
              <div style={{ fontFamily: MONO, fontSize: 28, color: 'rgba(180,200,240,0.15)', letterSpacing: '0.04em' }}>
                ◎
              </div>
              <div style={{ fontFamily: MONO, fontSize: 8, color: 'rgba(47,129,247,0.35)',
                letterSpacing: '0.08em', textTransform: 'uppercase' }}>
                Earth ← · · · Spacecraft
              </div>
            </div>

            {/* Right panel */}
            <div style={{ background: C.surface, borderLeft: `1px solid ${C.border}`,
              padding: '14px 12px', display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ fontFamily: MONO, fontSize: 8, color: C.textDim,
                letterSpacing: '0.10em', textTransform: 'uppercase', marginBottom: 2 }}>
                Mission Status
              </div>
              {[
                { label: 'Queue', value: '9.35 Gbit', color: C.amber },
                { label: 'Budget', value: '81 Mbit', color: C.green },
                { label: 'Products', value: '403', color: C.text },
                { label: 'AI Status', value: 'Ready', color: C.accent },
              ].map(({ label, value, color }) => (
                <div key={label} style={{ display: 'flex', justifyContent: 'space-between',
                  alignItems: 'center', background: C.surfaceUp, borderRadius: 3, padding: '5px 8px' }}>
                  <span style={{ fontFamily: MONO, fontSize: 9, color: C.textDim, letterSpacing: '0.08em' }}>
                    {label}
                  </span>
                  <span style={{ fontFamily: MONO, fontSize: 10, fontWeight: 600, color }}>
                    {value}
                  </span>
                </div>
              ))}
              <div style={{ marginTop: 8,
                background: C.accentGlow, border: `1px solid ${C.accent}44`,
                borderRadius: 3, padding: '7px 10px', textAlign: 'center',
                fontFamily: SANS, fontSize: 11, color: C.accent, fontWeight: 600,
                cursor: 'pointer',
              }}
                onClick={onLaunch}
              >
                Open Console →
              </div>
            </div>
          </div>
        </div>

        <button
          onClick={onLaunch}
          style={{
            fontFamily: SANS, fontSize: 14, fontWeight: 600,
            color: '#fff', background: C.accent,
            border: 'none', borderRadius: 4,
            padding: '13px 28px', cursor: 'pointer',
            transition: 'background 0.15s',
          }}
          onMouseEnter={e => (e.currentTarget.style.background = '#4a94f8')}
          onMouseLeave={e => (e.currentTarget.style.background = C.accent)}
        >
          Launch Mission Console →
        </button>
      </div>
    </Section>
  );
}

// ── Final CTA ─────────────────────────────────────────────────────────────────

interface FinalCTAProps { onLaunch: () => void }

function FinalCTA({ onLaunch }: FinalCTAProps) {
  return (
    <Section style={{ background: C.surface, textAlign: 'center' }}>
      <div style={{ maxWidth: 600, margin: '0 auto' }}>
        <div style={{ fontFamily: MONO, fontSize: 10, color: C.textDim,
          letterSpacing: '0.16em', textTransform: 'uppercase', marginBottom: 20 }}>
          GCSI / Ground Control Signal Insight
        </div>
        <h2 style={{ fontFamily: SANS, fontSize: 'clamp(26px, 3vw, 36px)',
          fontWeight: 700, color: C.text, lineHeight: 1.2,
          letterSpacing: '-0.02em', marginBottom: 16 }}>
          From Data-Heavy Telemetry to<br />Actionable Mission Decisions.
        </h2>
        <p style={{ fontFamily: SANS, fontSize: 14, color: C.textSec, lineHeight: 1.65,
          marginBottom: 36 }}>
          Bring spacecraft inventory, AI reasoning, deterministic constraints,
          and human authority into one coherent mission workspace.
        </p>
        <div style={{ display: 'flex', gap: 12, justifyContent: 'center', flexWrap: 'wrap' }}>
          <button
            onClick={onLaunch}
            style={{
              fontFamily: SANS, fontSize: 13, fontWeight: 600,
              color: '#fff', background: C.accent,
              border: 'none', borderRadius: 4,
              padding: '12px 26px', cursor: 'pointer',
              transition: 'background 0.15s',
            }}
            onMouseEnter={e => (e.currentTarget.style.background = '#4a94f8')}
            onMouseLeave={e => (e.currentTarget.style.background = C.accent)}
          >
            Launch Mission Console
          </button>
          <a
            href="https://github.com/itsjo-bit/ground-control-signal-insight"
            target="_blank" rel="noopener noreferrer"
            style={{
              fontFamily: SANS, fontSize: 13, fontWeight: 500,
              color: C.textSec, background: 'transparent',
              border: `1px solid ${C.border}`, borderRadius: 4,
              padding: '12px 26px', cursor: 'pointer',
              textDecoration: 'none',
              transition: 'border-color 0.15s, color 0.15s',
              display: 'inline-block',
            }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = C.accent; e.currentTarget.style.color = C.text; }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = C.border; e.currentTarget.style.color = C.textSec; }}
          >
            View on GitHub
          </a>
        </div>
      </div>
    </Section>
  );
}

// ── Footer ────────────────────────────────────────────────────────────────────

function Footer() {
  return (
    <footer style={{
      borderTop: `1px solid ${C.border}`,
      padding: '28px clamp(24px, 8vw, 120px)',
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      flexWrap: 'wrap', gap: 12,
    }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600, color: C.textSec, letterSpacing: '0.06em' }}>
          GCSI
        </span>
        <span style={{ fontFamily: SANS, fontSize: 11, color: C.textDim }}>
          Ground Control Signal Insight
        </span>
      </div>
      <div style={{ display: 'flex', gap: 20 }}>
        <a
          href="https://github.com/itsjo-bit/ground-control-signal-insight"
          target="_blank" rel="noopener noreferrer"
          style={{ fontFamily: SANS, fontSize: 11, color: C.textDim,
            textDecoration: 'none', transition: 'color 0.15s' }}
          onMouseEnter={e => (e.currentTarget.style.color = C.text)}
          onMouseLeave={e => (e.currentTarget.style.color = C.textDim)}
        >
          GitHub
        </a>
        <span style={{ fontFamily: MONO, fontSize: 10, color: C.textDim, letterSpacing: '0.06em' }}>
          Built with IBM Bob
        </span>
      </div>
    </footer>
  );
}

// ── LandingPage root ──────────────────────────────────────────────────────────

interface LandingPageProps {
  onLaunchConsole: () => void;
  onLaunchWithSource: (sourceId: string) => void;
}

export function LandingPage({ onLaunchConsole, onLaunchWithSource }: LandingPageProps) {
  return (
    <div style={{
      background: C.bg, color: C.text,
      fontFamily: SANS, minHeight: '100vh',
      overflowX: 'hidden',
    }}>
      <TopNav onLaunch={onLaunchConsole} />
      <main>
        <Hero onLaunch={onLaunchConsole} onLaunchWithSource={onLaunchWithSource} />
        <ProblemSection />
        <HowItWorksSection />
        <CapabilitiesSection />
        <ScenariosSection onLaunch={onLaunchConsole} onLaunchWithSource={onLaunchWithSource} />
        <TrustSection />
        <ConsolePreview onLaunch={onLaunchConsole} />
        <FinalCTA onLaunch={onLaunchConsole} />
      </main>
      <Footer />
    </div>
  );
}
