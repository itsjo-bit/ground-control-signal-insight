/**
 * GroundReceptionPanel — Phase 4.2F5
 *
 * Displays the ground reception sequence after signal arrives at Earth:
 *   1. Ground station receiving
 *   2. Actual delivered products (from SimulationResult.delivered_packets)
 *   3. Per-objective evidence coverage (BEFORE → AFTER)
 *   4. Deterministic mission update text
 *   5. Anomaly remains physically unresolved
 *
 * Uses production groundEvidence.ts helpers.
 * Does NOT claim integrity verification or anomaly resolution.
 */

import { useMemo } from 'react';
import type { ApproveResponse, AnomalyEvent } from '../types/domain';
import type { GroundInformationObjectives } from '../types/experience';
import {
  assessGroundObjectives,
  overallGroundEvidenceCoverage,
  groundEvidenceLevel,
  objectiveAvailabilityLabel,
  generateMissionUpdateText,
} from '../experience/groundEvidence';

// ── Style helpers ─────────────────────────────────────────────────────────────

const CARD: React.CSSProperties = {
  background: 'rgba(18,24,34,0.7)',
  border: '1px solid rgba(46,58,79,0.8)',
  borderRadius: 8,
  padding: '10px 12px',
  marginBottom: 8,
};

const MONO: React.CSSProperties = {
  fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
};

const SANS: React.CSSProperties = {
  fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
};

const LABEL: React.CSSProperties = {
  ...MONO,
  fontSize: 8,
  color: 'rgba(147,160,180,0.55)',
  letterSpacing: '0.08em',
  textTransform: 'uppercase' as const,
  marginBottom: 2,
};

function levelColor(level: 'LOW' | 'MEDIUM' | 'HIGH'): string {
  if (level === 'HIGH') return '#34d399';
  if (level === 'MEDIUM') return '#f59e0b';
  return '#f87171';
}

function availabilityColor(label: 'AVAILABLE' | 'PARTIAL' | 'UNAVAILABLE'): string {
  if (label === 'AVAILABLE') return '#34d399';
  if (label === 'PARTIAL') return '#f59e0b';
  return 'rgba(147,160,180,0.4)';
}

// Pretty-print objective names
function prettyObjectiveName(name: string): string {
  return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

// ── Props ─────────────────────────────────────────────────────────────────────

interface GroundReceptionPanelProps {
  approveResult: ApproveResponse;
  anomalies: AnomalyEvent[];
  groundInformationObjectives: GroundInformationObjectives | null;
  /** Display name of the ground station, e.g. "GCSI-GS-03" */
  groundStationName?: string;
}

// ── Component ─────────────────────────────────────────────────────────────────

export function GroundReceptionPanel({
  approveResult,
  anomalies,
  groundInformationObjectives,
  groundStationName,
}: GroundReceptionPanelProps) {
  const sim = approveResult.simulation_result;
  const deliveredIds = useMemo(() => new Set(sim.delivered_packets), [sim.delivered_packets]);

  // Ground evidence assessment
  const objectiveCoverages = useMemo(() => {
    if (!groundInformationObjectives) return null;
    return assessGroundObjectives(deliveredIds, groundInformationObjectives);
  }, [deliveredIds, groundInformationObjectives]);

  const overallFraction = useMemo(() => {
    if (!groundInformationObjectives) return null;
    return overallGroundEvidenceCoverage(deliveredIds, groundInformationObjectives);
  }, [deliveredIds, groundInformationObjectives]);

  const overallLevel = overallFraction !== null ? groundEvidenceLevel(overallFraction) : null;

  // Before coverage is always 0 (pre-contact state)
  const beforeFraction = 0.0;
  const beforeLevel = groundEvidenceLevel(beforeFraction);

  // Mission update text
  const missionUpdateText = useMemo(() => {
    if (!objectiveCoverages || overallFraction === null) return null;
    return generateMissionUpdateText(objectiveCoverages, overallFraction);
  }, [objectiveCoverages, overallFraction]);

  // Active thermal anomaly (remains unresolved)
  const thermalAnomaly = anomalies.find((a) => a.anomaly_id === 'ANOM-THERM-017') ?? anomalies[0] ?? null;

  const stationLabel = groundStationName ?? 'GROUND STATION';

  return (
    <div>
      {/* Ground station header */}
      <div style={{ ...CARD, borderColor: 'rgba(52,211,153,0.25)', background: 'rgba(52,211,153,0.04)', marginBottom: 10 }}>
        <div style={{ ...MONO, fontSize: 9, color: 'rgba(52,211,153,0.7)', letterSpacing: '0.12em', marginBottom: 4 }}>
          GROUND STATION · {stationLabel}
        </div>
        <div style={{ ...MONO, fontSize: 15, fontWeight: 700, color: '#34d399', marginBottom: 2 }}>
          INCOMING DOWNLINK
        </div>
        <div style={{ ...SANS, fontSize: 10, color: 'rgba(147,160,180,0.6)', marginBottom: 8 }}>
          SIMULATED RECEPTION CONFIRMED — not a physical link simulation
        </div>

        {/* Totals */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 6 }}>
          {[
            { label: 'RECEIVED', value: sim.delivered_packets.length, color: '#34d399' },
            { label: 'FAILED', value: sim.failed_packets.length, color: sim.failed_packets.length > 0 ? '#f87171' : 'rgba(147,160,180,0.5)' },
            { label: 'DEFERRED', value: sim.deferred_packets.length, color: sim.deferred_packets.length > 0 ? '#f59e0b' : 'rgba(147,160,180,0.5)' },
            { label: 'RETRIES', value: Object.values(sim.retransmission_counts ?? {}).reduce((s, v) => s + v, 0), color: 'rgba(147,160,180,0.6)' },
          ].map(({ label, value, color }) => (
            <div key={label} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(46,58,79,0.5)', borderRadius: 4, padding: '5px 7px' }}>
              <div style={LABEL}>{label}</div>
              <div style={{ ...MONO, fontSize: 16, fontWeight: 700, color }}>{value}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Delivered products list */}
      {sim.delivered_packets.length > 0 && (
        <div style={{ ...CARD, marginBottom: 10 }}>
          <div style={{ ...MONO, fontSize: 9, color: 'rgba(147,160,180,0.55)', letterSpacing: '0.08em', marginBottom: 6 }}>
            PRIORITY PRODUCTS RECEIVED
          </div>
          <div style={{ maxHeight: 180, overflowY: 'auto' }}>
            {sim.delivered_packets.slice(0, 20).map((pid) => (
              <div key={pid} style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '3px 0', borderBottom: '1px solid rgba(46,58,79,0.25)' }}>
                <span style={{ color: '#34d399', fontSize: 10, flexShrink: 0 }}>✓</span>
                <span style={{ ...MONO, fontSize: 10, color: '#e2e8f4', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {pid}
                </span>
              </div>
            ))}
            {sim.delivered_packets.length > 20 && (
              <div style={{ ...SANS, fontSize: 10, color: 'rgba(147,160,180,0.5)', padding: '4px 0', textAlign: 'center' }}>
                …and {sim.delivered_packets.length - 20} more
              </div>
            )}
          </div>
          {sim.failed_packets.length > 0 && (
            <div style={{ marginTop: 6, paddingTop: 6, borderTop: '1px solid rgba(46,58,79,0.4)' }}>
              <div style={{ ...MONO, fontSize: 9, color: '#f87171', letterSpacing: '0.07em', marginBottom: 4 }}>FAILED ATTEMPTS</div>
              {sim.failed_packets.map((pid) => (
                <div key={pid} style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '2px 0' }}>
                  <span style={{ color: '#f87171', fontSize: 10, flexShrink: 0 }}>✕</span>
                  <span style={{ ...MONO, fontSize: 10, color: 'rgba(248,113,113,0.7)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {pid}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Ground information objectives — BEFORE / AFTER */}
      {objectiveCoverages && overallFraction !== null && (
        <div style={{ ...CARD, marginBottom: 10 }}>
          <div style={{ ...MONO, fontSize: 9, color: 'rgba(147,160,180,0.55)', letterSpacing: '0.08em', marginBottom: 8 }}>
            GROUND INFORMATION OBJECTIVES — BEFORE / AFTER
          </div>

          {/* Overall coverage row */}
          <div style={{
            display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8,
            marginBottom: 10, paddingBottom: 10,
            borderBottom: '1px solid rgba(46,58,79,0.5)',
          }}>
            <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(46,58,79,0.5)', borderRadius: 4, padding: '7px 10px' }}>
              <div style={{ ...MONO, fontSize: 8, color: 'rgba(147,160,180,0.4)', marginBottom: 3 }}>BEFORE CONTACT</div>
              <div style={{ ...MONO, fontSize: 12, fontWeight: 700, color: levelColor(beforeLevel) }}>
                {beforeLevel} · {(beforeFraction * 100).toFixed(0)}%
              </div>
              <div style={{ ...SANS, fontSize: 10, color: 'rgba(147,160,180,0.5)', marginTop: 2 }}>
                Ground evidence coverage
              </div>
            </div>
            <div style={{ background: 'rgba(52,211,153,0.04)', border: `1px solid ${overallLevel === 'HIGH' ? 'rgba(52,211,153,0.25)' : overallLevel === 'MEDIUM' ? 'rgba(245,158,11,0.25)' : 'rgba(248,113,113,0.2)'}`, borderRadius: 4, padding: '7px 10px' }}>
              <div style={{ ...MONO, fontSize: 8, color: 'rgba(147,160,180,0.4)', marginBottom: 3 }}>AFTER RECEPTION</div>
              <div style={{ ...MONO, fontSize: 12, fontWeight: 700, color: levelColor(overallLevel!) }}>
                {overallLevel} · {(overallFraction * 100).toFixed(0)}%
              </div>
              <div style={{ ...SANS, fontSize: 10, color: 'rgba(147,160,180,0.5)', marginTop: 2 }}>
                Ground evidence coverage
              </div>
            </div>
          </div>

          {/* Per-objective table */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            {objectiveCoverages.map((obj) => {
              const label = objectiveAvailabilityLabel(obj.fraction);
              const color = availabilityColor(label);
              return (
                <div key={obj.name} style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  padding: '4px 6px',
                  background: 'rgba(255,255,255,0.02)',
                  border: '1px solid rgba(46,58,79,0.4)',
                  borderRadius: 3,
                }}>
                  <span style={{ ...SANS, fontSize: 11, color: 'rgba(147,160,180,0.75)' }}>
                    {prettyObjectiveName(obj.name)}
                  </span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ ...SANS, fontSize: 10, color: 'rgba(147,160,180,0.4)' }}>
                      BEFORE: UNAVAILABLE
                    </span>
                    <span style={{ color: 'rgba(147,160,180,0.3)', fontSize: 10 }}>→</span>
                    <span style={{ ...MONO, fontSize: 10, fontWeight: 600, color }}>{label}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Mission update text */}
      {missionUpdateText && (
        <div style={{ ...CARD, marginBottom: 10 }}>
          <div style={{ ...MONO, fontSize: 9, color: 'rgba(147,160,180,0.55)', letterSpacing: '0.08em', marginBottom: 6 }}>
            GROUND MISSION UPDATE
          </div>
          <div style={{ ...SANS, fontSize: 12, color: 'rgba(147,160,180,0.85)', lineHeight: 1.6 }}>
            {missionUpdateText}
          </div>
        </div>
      )}

      {/* Spacecraft anomaly — still active */}
      {thermalAnomaly && (
        <div style={{
          ...CARD,
          borderColor: 'rgba(248,113,113,0.28)',
          background: 'rgba(248,113,113,0.04)',
        }}>
          <div style={{ ...MONO, fontSize: 9, color: '#f87171', letterSpacing: '0.08em', marginBottom: 4 }}>
            ⚠ SPACECRAFT ANOMALY — STILL ACTIVE
          </div>
          <div style={{ ...MONO, fontSize: 11, color: '#f87171', marginBottom: 3 }}>
            {thermalAnomaly.anomaly_id}
          </div>
          <div style={{ ...SANS, fontSize: 11, color: 'rgba(147,160,180,0.75)', lineHeight: 1.5, marginBottom: 6 }}>
            Ground received data products but the spacecraft thermal anomaly has not been physically resolved.
            Transmission improves ground knowledge — it does not repair onboard hardware.
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <span style={{ ...MONO, fontSize: 9, background: 'rgba(248,113,113,0.12)', color: '#f87171', border: '1px solid rgba(248,113,113,0.3)', borderRadius: 3, padding: '2px 6px' }}>
              ACTIVE
            </span>
            <span style={{ ...MONO, fontSize: 9, color: 'rgba(147,160,180,0.5)' }}>
              SEVERITY {(thermalAnomaly.severity * 100).toFixed(0)}%
            </span>
          </div>
        </div>
      )}

      {/* No objectives available for generic scenario */}
      {!groundInformationObjectives && (
        <div style={{ ...CARD, borderColor: 'rgba(46,58,79,0.5)' }}>
          <div style={{ ...SANS, fontSize: 12, color: 'rgba(147,160,180,0.5)', lineHeight: 1.5 }}>
            Ground information objectives are not defined for this scenario.
          </div>
        </div>
      )}
    </div>
  );
}
