/**
 * SourceContextBanner — Phase 6E-C7
 *
 * Pure presentation component. Accepts source summary from MissionControl
 * (populated from GET /state). Never calls APIs directly.
 *
 * Historical replay: renders a compact, clearly labeled banner that communicates:
 *   - HISTORICAL REPLAY headline
 *   - Reconstructed mission / not live telemetry
 *   - Provenance category counts (from backend — never hard-coded)
 *   - Source-baseline scope limitation
 *
 * Synthetic scenario: renders a minimal neutral indicator only.
 * Null source: renders nothing (graceful no-op).
 */
import type { SourceSummary } from '../types/domain';

export interface SourceContextBannerProps {
  source: SourceSummary | null;
  missionId?: string | null;
}

// ── Shared style constants ────────────────────────────────────────────────────

const MONO = '"IBM Plex Mono", ui-monospace, "SF Mono", monospace';
const SANS = '"IBM Plex Sans", system-ui, sans-serif';

function ProvenanceCount({ label, count, title }: { label: string; count: number; title: string }) {
  return (
    <div
      title={title}
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 2,
        minWidth: 56,
      }}
    >
      <span style={{
        fontFamily: MONO, fontSize: 14, fontWeight: 700,
        color: 'rgba(110,168,255,0.90)',
        lineHeight: 1,
      }}>
        {count}
      </span>
      <span style={{
        fontFamily: SANS, fontSize: 9, fontWeight: 600,
        color: 'rgba(110,168,255,0.55)',
        textTransform: 'uppercase', letterSpacing: '0.07em',
        lineHeight: 1,
      }}>
        {label}
      </span>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function SourceContextBanner({ source, missionId }: SourceContextBannerProps) {
  // Null source — render nothing
  if (!source) return null;

  // Synthetic scenario — minimal neutral indicator only
  if (source.mode === 'synthetic_scenario') {
    return (
      <div
        aria-label="Source mode: synthetic scenario"
        style={{
          background: 'rgba(245,158,11,0.04)',
          borderBottom: '1px solid rgba(245,158,11,0.10)',
          padding: '4px 16px',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          flexShrink: 0,
          zIndex: 49,
        }}
      >
        <span style={{
          fontFamily: MONO, fontSize: 9, fontWeight: 600,
          letterSpacing: '0.06em', color: 'rgba(245,158,11,0.65)',
        }}>
          SYNTHETIC SCENARIO
        </span>
      </div>
    );
  }

  // Historical replay — full provenance banner
  if (source.mode === 'historical_replay') {
    const counts = source.provenance_kind_counts;
    const authCount   = counts.external_authoritative ?? 0;
    const derivCount  = counts.derived ?? 0;
    const modelCount  = counts.modeled ?? 0;

    return (
      <section
        aria-label="Historical replay active — not live telemetry"
        style={{
          background: 'rgba(76,141,255,0.05)',
          borderBottom: '1px solid rgba(76,141,255,0.20)',
          padding: '8px 16px',
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: 12,
          flexShrink: 0,
          zIndex: 49,
        }}
      >
        {/* Headline + mission */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
          <span style={{
            fontFamily: MONO, fontSize: 10, fontWeight: 700,
            letterSpacing: '0.07em', color: '#6EA8FF',
          }}>
            HISTORICAL REPLAY
          </span>
          {missionId && (
            <span style={{
              fontFamily: SANS, fontSize: 10, fontWeight: 600,
              color: 'rgba(110,168,255,0.75)',
              padding: '1px 6px',
              background: 'rgba(76,141,255,0.10)',
              border: '1px solid rgba(76,141,255,0.20)',
              borderRadius: 3,
            }}>
              {missionId}
            </span>
          )}
        </div>

        {/* Not-live wording */}
        <span style={{
          fontFamily: SANS, fontSize: 10.5, color: 'rgba(147,160,180,0.80)',
          flex: '1 1 160px', minWidth: 0,
        }}>
          Reconstructed mission scenario — not live telemetry.{' '}
          Verified NASA/JPL/PDS source facts + explicit GCSI modeled communications policy.
        </span>

        {/* Provenance counts — from backend, never hard-coded */}
        {source.provenance_available && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0,
            padding: '4px 10px',
            background: 'rgba(76,141,255,0.06)',
            border: '1px solid rgba(76,141,255,0.14)',
            borderRadius: 5,
          }}>
            <ProvenanceCount
              label="Authoritative"
              count={authCount}
              title="Verified facts retained from external NASA/JPL/PDS source artifacts."
            />
            <div style={{ width: 1, height: 28, background: 'rgba(76,141,255,0.15)' }} />
            <ProvenanceCount
              label="Derived"
              count={derivCount}
              title="Values deterministically calculated or normalized from source facts."
            />
            <div style={{ width: 1, height: 28, background: 'rgba(76,141,255,0.15)' }} />
            <ProvenanceCount
              label="Modeled"
              count={modelCount}
              title="Explicit GCSI replay assumptions used to construct the decision scenario."
            />
          </div>
        )}

        {/* Source-baseline scope note */}
        <div style={{
          fontFamily: SANS, fontSize: 9.5,
          color: 'rgba(110,130,165,0.65)',
          lineHeight: 1.45,
          flex: '1 1 200px', minWidth: 0,
        }}>
          Provenance describes the loaded source baseline.
          Runtime simulation or approved actions may mutate current state.
          {source.source_ref && (
            <span style={{ display: 'block', marginTop: 2 }}>
              <span style={{ color: 'rgba(110,130,165,0.50)' }}>Replay descriptor: </span>
              <code style={{
                fontFamily: MONO, fontSize: 9, color: 'rgba(110,168,255,0.55)',
                background: 'transparent', padding: 0,
              }}>
                {source.source_ref}
              </code>
            </span>
          )}
        </div>
      </section>
    );
  }

  // Unknown mode — render nothing
  return null;
}
