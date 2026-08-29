/**
 * SourceContextBanner — Phase 6E-C7
 *
 * Pure presentation component. Accepts source summary from MissionControl
 * (populated from GET /state). Never calls APIs directly.
 *
 * V4.1: Restrained dark engineering theme. Same analytical layout as V4.0.
 * Historical replay reads like a precise mission brief, not a marketing banner.
 * Provenance counts use typographic hierarchy, not boxed cards.
 */
import type { SourceSummary } from '../types/domain';

export interface SourceContextBannerProps {
  source: SourceSummary | null;
  missionId?: string | null;
}

const MONO = '"IBM Plex Mono", ui-monospace, "SF Mono", monospace';
const SANS = '"IBM Plex Sans", system-ui, sans-serif';

function ProvenanceCount({ label, count, title }: { label: string; count: number; title: string }) {
  return (
    <div
      title={title}
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'flex-start',
        gap: 1,
        minWidth: 52,
      }}
    >
      <span style={{
        fontFamily: MONO, fontSize: 15, fontWeight: 700,
        color: '#e6edf3',
        lineHeight: 1,
      }}>
        {count}
      </span>
      <span style={{
        fontFamily: SANS, fontSize: 9, fontWeight: 500,
        color: '#656d76',
        textTransform: 'uppercase', letterSpacing: '0.06em',
        lineHeight: 1,
      }}>
        {label}
      </span>
    </div>
  );
}

export function SourceContextBanner({ source, missionId }: SourceContextBannerProps) {
  if (!source) return null;

  // Synthetic scenario — minimal neutral indicator only
  if (source.mode === 'synthetic_scenario') {
    return (
      <div
        aria-label="Source mode: synthetic scenario"
        style={{
          background: 'rgba(210,153,34,0.07)',
          borderBottom: '1px solid rgba(210,153,34,0.20)',
          padding: '5px 16px',
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          flexShrink: 0,
          zIndex: 49,
        }}
      >
        <span style={{
          fontFamily: MONO, fontSize: 9, fontWeight: 700,
          letterSpacing: '0.07em', color: '#d29922',
        }}>
          SYNTHETIC SCENARIO
        </span>
        <span style={{
          fontFamily: SANS, fontSize: 10, color: '#8b949e',
        }}>
          Simulated data — not real mission telemetry
        </span>
      </div>
    );
  }

  // Historical replay — analytical document header
  if (source.mode === 'historical_replay') {
    const counts = source.provenance_kind_counts;
    const authCount   = counts.external_authoritative ?? 0;
    const derivCount  = counts.derived ?? 0;
    const modelCount  = counts.modeled ?? 0;

    return (
      <section
        aria-label="Historical replay active — not live telemetry"
        style={{
          background: '#0f172a',
          borderBottom: '1px solid rgba(47,129,247,0.20)',
          padding: '8px 16px',
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: 0,
          flexShrink: 0,
          zIndex: 49,
        }}
      >
        {/* Left block: headline + mission identifier */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 1, marginRight: 20, flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{
              fontFamily: MONO, fontSize: 9, fontWeight: 700,
              letterSpacing: '0.08em', color: '#2f81f7',
            }}>
              HISTORICAL REPLAY
            </span>
            {missionId && (
              <span style={{
                fontFamily: MONO, fontSize: 9, fontWeight: 600,
                color: '#2f81f7',
                opacity: 0.7,
              }}>
                {missionId}
              </span>
            )}
          </div>
          <span style={{
            fontFamily: SANS, fontSize: 10, color: '#8b949e',
          }}>
            Reconstructed scenario — not live telemetry
          </span>
        </div>

        {/* Thin divider */}
        <div style={{ width: 1, height: 28, background: 'rgba(47,129,247,0.20)', marginRight: 16, flexShrink: 0 }} />

        {/* Context note */}
        <span style={{
          fontFamily: SANS, fontSize: 10, color: '#656d76',
          flex: '1 1 140px', minWidth: 0, marginRight: 16,
        }}>
          Verified NASA/JPL/PDS source facts + explicit GCSI modeled communications policy.
          Provenance describes the loaded source baseline.
        </span>

        {/* Provenance counts — typographic hierarchy, no boxed cards */}
        {source.provenance_available && (
          <div style={{
            display: 'flex', alignItems: 'flex-end', gap: 20, flexShrink: 0,
          }}>
            <ProvenanceCount
              label="Authoritative"
              count={authCount}
              title="Verified facts retained from external NASA/JPL/PDS source artifacts."
            />
            <ProvenanceCount
              label="Derived"
              count={derivCount}
              title="Values deterministically calculated or normalized from source facts."
            />
            <ProvenanceCount
              label="Modeled"
              count={modelCount}
              title="Explicit GCSI replay assumptions used to construct the decision scenario."
            />
          </div>
        )}

        {/* Replay descriptor — secondary metadata, far right */}
        {source.source_ref && (
          <div style={{
            fontFamily: SANS, fontSize: 9,
            color: '#484f58',
            marginLeft: 16,
            flexShrink: 0,
            alignSelf: 'flex-end',
          }}>
            <code style={{
              fontFamily: MONO, fontSize: 9, color: '#484f58',
              background: 'transparent', padding: 0,
            }}>
              {source.source_ref}
            </code>
          </div>
        )}
      </section>
    );
  }

  return null;
}
