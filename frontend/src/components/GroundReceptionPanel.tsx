/**
 * GroundReceptionPanel — Phase 4.2F5 + Transmission Result Clarity
 *
 * Displays the ground reception sequence after signal arrives at Earth:
 *   0. Transmission result summary header (Queue / Selected / Received / Deferred /
 *      Not Selected / Failed / Retries) with mode label (MANUAL / AI-ASSISTED)
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
import type { ApproveResponse, AnomalyEvent, DecisionMode } from '../types/domain';
import type { GroundInformationObjectives } from '../types/experience';
import {
  assessGroundObjectives,
  overallGroundEvidenceCoverage,
  groundEvidenceLevel,
  objectiveAvailabilityLabel,
  generateMissionUpdateText,
} from '../experience/groundEvidence';
import { formatBitsAsMbit } from '../utils/formatters';
import {
  computeTransmissionAccounting,
  checkAccountingInvariants,
} from '../utils/transmissionResultAccounting';

// ── Style helpers ─────────────────────────────────────────────────────────────

const CARD: React.CSSProperties = {
  background: '#161b22',
  border: '1px solid #30363d',
  borderRadius: 4,
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
  color: '#656d76',
  letterSpacing: '0.07em',
  textTransform: 'uppercase' as const,
  marginBottom: 2,
};

function levelColor(level: 'LOW' | 'MEDIUM' | 'HIGH'): string {
  if (level === 'HIGH') return '#3fb950';
  if (level === 'MEDIUM') return '#d29922';
  return '#f85149';
}

function availabilityColor(label: 'AVAILABLE' | 'PARTIAL' | 'UNAVAILABLE'): string {
  if (label === 'AVAILABLE') return '#3fb950';
  if (label === 'PARTIAL') return '#d29922';
  return '#656d76';
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
  /** Total number of active DataProducts in the full queue. */
  queueTotal: number;
  /** Total size in bits of all active DataProducts in the full queue. */
  queueDataBits: number;
  /** Modeled contact-window capacity in bits. */
  availableCapacityBits: number;
  /**
   * Decision mode at transmission time.
   * Drives the mode label (MANUAL TRANSMISSION / AI-ASSISTED TRANSMISSION).
   */
  decisionMode: DecisionMode;
}

// ── Accounting summary stat cell ──────────────────────────────────────────────

interface StatCellProps {
  label: string;
  value: number | string;
  color?: string;
  dim?: boolean;
  tooltip?: string;
}

function StatCell({ label, value, color, dim, tooltip }: StatCellProps) {
  const effectiveColor = dim
    ? '#656d76'
    : (color ?? '#e6edf3');
  return (
    <div
      style={{
        background: '#21262d',
        border: '1px solid #30363d',
        borderRadius: 3,
        padding: '5px 7px',
        minWidth: 60,
      }}
      title={tooltip}
    >
      <div style={LABEL}>{label}</div>
      <div style={{ ...MONO, fontSize: 15, fontWeight: 700, color: effectiveColor }}>
        {value}
      </div>
    </div>
  );
}

// ── Component ─────────────────────────────────────────────────────────────────

export function GroundReceptionPanel({
  approveResult,
  anomalies,
  groundInformationObjectives,
  groundStationName,
  queueTotal,
  queueDataBits,
  availableCapacityBits,
  decisionMode,
}: GroundReceptionPanelProps) {
  const sim = approveResult.simulation_result;
  const executedPlan = approveResult.executed_plan;
  const deliveredIds = useMemo(() => new Set(sim.delivered_packets), [sim.delivered_packets]);

  // ── Transmission accounting ───────────────────────────────────────────────

  const accounting = useMemo(() => {
    const selectedCount = executedPlan.packets.length;
    const selectedDataBits = executedPlan.packets.reduce((s, p) => s + p.size_bits, 0);

    const result = computeTransmissionAccounting({
      queue_total: queueTotal,
      queue_data_bits: queueDataBits,
      delivered_packets: sim.delivered_packets,
      deferred_packets: sim.deferred_packets,
      failed_packets: sim.failed_packets,
      retransmission_counts: sim.retransmission_counts ?? {},
      selected_data_bits: selectedDataBits,
      selected_count: selectedCount,
      capacity_bits: availableCapacityBits,
    });

    // Log accounting invariant violations in dev — never alter UI data
    const violations = checkAccountingInvariants(result);
    if (violations.length > 0) {
      console.warn('[GCSI] Transmission accounting invariant violations:', violations);
    }

    return result;
  }, [executedPlan, sim, queueTotal, queueDataBits, availableCapacityBits]);

  const modeLabel = decisionMode === 'manual' ? 'MANUAL TRANSMISSION' : 'AI-ASSISTED TRANSMISSION';
  const modeColor = decisionMode === 'manual' ? '#3fb950' : '#2f81f7';
  const modeBorderColor = decisionMode === 'manual' ? 'rgba(63,185,80,0.25)' : 'rgba(47,129,247,0.22)';
  const modeBgColor = decisionMode === 'manual' ? 'rgba(63,185,80,0.06)' : 'rgba(47,129,247,0.06)';

  const capacityFill = accounting.capacity_bits > 0
    ? Math.min(1, accounting.selected_data_bits / accounting.capacity_bits)
    : 0;
  const isOverCapacity = accounting.selected_data_bits > accounting.capacity_bits && accounting.capacity_bits > 0;

  // ── Ground evidence assessment ────────────────────────────────────────────

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
      {/* ── Transmission result summary ──────────────────────────────────────── */}
      <div style={{
        ...CARD,
        borderColor: modeBorderColor,
        background: modeBgColor,
        marginBottom: 10,
      }}>
        {/* Mode label */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          marginBottom: 8,
        }}>
          <div style={{ ...MONO, fontSize: 10, fontWeight: 700, color: modeColor, letterSpacing: '0.08em' }}>
            {modeLabel}
          </div>
          <div style={{ ...MONO, fontSize: 8, color: '#656d76', letterSpacing: '0.06em' }}>
            TRANSMISSION RESULT
          </div>
        </div>

        {/* Queue / Selected / Received / Deferred / Not Selected */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 4, marginBottom: 8 }}>
          <StatCell
            label="Queue"
            value={accounting.queue_total}
            tooltip="All active DataProducts available for transmission consideration"
          />
          <StatCell
            label="Selected"
            value={accounting.selected}
            color="#2f81f7"
            tooltip="Products included in the submitted transmission plan"
          />
          <StatCell
            label="Received"
            value={accounting.received}
            color={accounting.received > 0 ? '#3fb950' : '#656d76'}
            tooltip="Selected products successfully delivered in the modeled contact"
          />
          <StatCell
            label="Deferred"
            value={accounting.deferred}
            color={accounting.deferred > 0 ? '#d29922' : undefined}
            dim={accounting.deferred === 0}
            tooltip="Selected but not transmitted — contact window exhausted"
          />
          <StatCell
            label="Not Selected"
            value={accounting.not_selected}
            dim={accounting.not_selected === 0}
            tooltip="In full queue but not included in this transmission plan"
          />
        </div>

        {/* Failed / Retries */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 4, marginBottom: 8 }}>
          <StatCell
            label="Failed"
            value={accounting.failed}
            color={accounting.failed > 0 ? '#f85149' : undefined}
            dim={accounting.failed === 0}
            tooltip="Selected products that failed all delivery attempts"
          />
          <StatCell
            label="Retries"
            value={accounting.retries}
            dim
            tooltip="Realized retransmission attempts (attempt count, not product count)"
          />
        </div>

        {/* Tooltip hint */}
        <div style={{ ...SANS, fontSize: 9, color: '#656d76', lineHeight: 1.4, marginBottom: 8 }}>
          Deferred = selected but not transmitted. · Not Selected = remained outside this plan.
        </div>

        {/* Selected data vs capacity bar */}
        {accounting.capacity_bits > 0 && (
          <div>
            <div style={{
              display: 'flex', justifyContent: 'space-between',
              ...MONO, fontSize: 8, color: '#656d76',
              textTransform: 'uppercase' as const, letterSpacing: '0.07em',
              marginBottom: 3,
            }}>
              <span>Selected Data / Capacity</span>
              <span style={{ color: isOverCapacity ? '#f85149' : '#656d76' }}>
                {formatBitsAsMbit(accounting.selected_data_bits)} / {formatBitsAsMbit(accounting.capacity_bits)}
                {accounting.capacity_bits > 0 && (
                  <span style={{ marginLeft: 6, color: isOverCapacity ? '#f85149' : '#8b949e' }}>
                    ({(capacityFill * 100).toFixed(1)}% of window)
                  </span>
                )}
                {isOverCapacity && <span style={{ marginLeft: 4 }}>⚠ EXCEEDS CAPACITY</span>}
              </span>
            </div>
            <div style={{ height: 4, background: '#21262d', borderRadius: 3, overflow: 'hidden' }}>
              <div style={{
                height: '100%',
                width: `${(capacityFill * 100).toFixed(1)}%`,
                background: isOverCapacity ? '#f85149' : capacityFill > 0.85 ? '#d29922' : '#3fb950',
                borderRadius: 3,
                transition: 'width 0.4s ease',
              }} />
            </div>
            {/* Queue data vs capacity context */}
            {accounting.queue_data_bits > 0 && (
              <div style={{ ...MONO, fontSize: 8, color: '#656d76', marginTop: 3, letterSpacing: '0.05em' }}>
                Full queue {formatBitsAsMbit(accounting.queue_data_bits)} · Capacity {formatBitsAsMbit(accounting.capacity_bits)}
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── Ground station header ─────────────────────────────────────────────── */}
      <div style={{ ...CARD, borderColor: 'rgba(63,185,80,0.22)', background: 'rgba(63,185,80,0.05)', marginBottom: 10 }}>
        <div style={{ ...MONO, fontSize: 9, color: '#3fb950', letterSpacing: '0.08em', marginBottom: 4 }}>
          GROUND STATION · {stationLabel}
        </div>
        <div style={{ ...MONO, fontSize: 15, fontWeight: 700, color: '#3fb950', marginBottom: 2 }}>
          INCOMING DOWNLINK
        </div>
        <div style={{ ...SANS, fontSize: 10, color: '#8b949e', marginBottom: 8 }}>
          SIMULATED RECEPTION CONFIRMED — not a physical link simulation
        </div>

        {/* Received / Failed / Deferred / Retries (simulation outcome compact) */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 6 }}>
          {[
            { label: 'RECEIVED', value: sim.delivered_packets.length, color: '#3fb950' },
            { label: 'FAILED', value: sim.failed_packets.length, color: sim.failed_packets.length > 0 ? '#f85149' : '#656d76' },
            { label: 'DEFERRED', value: sim.deferred_packets.length, color: sim.deferred_packets.length > 0 ? '#d29922' : '#656d76' },
            { label: 'RETRIES', value: Object.values(sim.retransmission_counts ?? {}).reduce((s, v) => s + v, 0), color: '#656d76' },
          ].map(({ label, value, color }) => (
            <div key={label} style={{ background: '#21262d', border: '1px solid #30363d', borderRadius: 3, padding: '5px 7px' }}>
              <div style={LABEL}>{label}</div>
              <div style={{ ...MONO, fontSize: 16, fontWeight: 700, color }}>{value}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Delivered products list */}
      {sim.delivered_packets.length > 0 && (
        <div style={{ ...CARD, marginBottom: 10 }}>
          <div style={{ ...MONO, fontSize: 9, color: '#8b949e', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 6 }}>
            Priority Products Received
          </div>
          <div style={{ maxHeight: 180, overflowY: 'auto' }}>
            {sim.delivered_packets.slice(0, 20).map((pid) => (
              <div key={pid} style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '3px 0', borderBottom: '1px solid #30363d' }}>
                <span style={{ color: '#3fb950', fontSize: 10, flexShrink: 0 }}>✓</span>
                <span style={{ ...MONO, fontSize: 10, color: '#e6edf3', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {pid}
                </span>
              </div>
            ))}
            {sim.delivered_packets.length > 20 && (
              <div style={{ ...SANS, fontSize: 10, color: '#656d76', padding: '4px 0', textAlign: 'center' }}>
                …and {sim.delivered_packets.length - 20} more
              </div>
            )}
          </div>
          {sim.failed_packets.length > 0 && (
            <div style={{ marginTop: 6, paddingTop: 6, borderTop: '1px solid #30363d' }}>
              <div style={{ ...MONO, fontSize: 9, color: '#f85149', letterSpacing: '0.07em', marginBottom: 4 }}>FAILED ATTEMPTS</div>
              {sim.failed_packets.map((pid) => (
                <div key={pid} style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '2px 0' }}>
                  <span style={{ color: '#f85149', fontSize: 10, flexShrink: 0 }}>✕</span>
                  <span style={{ ...MONO, fontSize: 10, color: '#f85149', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
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
          <div style={{ ...MONO, fontSize: 9, color: '#8b949e', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 8 }}>
            Ground Information Objectives — Before / After
          </div>

          {/* Overall coverage row */}
          <div style={{
            display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8,
            marginBottom: 10, paddingBottom: 10,
            borderBottom: '1px solid #30363d',
          }}>
            <div style={{ background: '#21262d', border: '1px solid #30363d', borderRadius: 3, padding: '7px 10px' }}>
              <div style={{ ...MONO, fontSize: 8, color: '#656d76', marginBottom: 3 }}>BEFORE CONTACT</div>
              <div style={{ ...MONO, fontSize: 12, fontWeight: 700, color: levelColor(beforeLevel) }}>
                {beforeLevel} · {(beforeFraction * 100).toFixed(0)}%
              </div>
              <div style={{ ...SANS, fontSize: 10, color: '#8b949e', marginTop: 2 }}>
                Ground evidence coverage
              </div>
            </div>
            <div style={{ background: overallLevel === 'HIGH' ? 'rgba(63,185,80,0.06)' : overallLevel === 'MEDIUM' ? 'rgba(210,153,34,0.06)' : 'rgba(248,81,73,0.06)', border: `1px solid ${overallLevel === 'HIGH' ? 'rgba(63,185,80,0.25)' : overallLevel === 'MEDIUM' ? 'rgba(210,153,34,0.25)' : 'rgba(248,81,73,0.22)'}`, borderRadius: 3, padding: '7px 10px' }}>
              <div style={{ ...MONO, fontSize: 8, color: '#656d76', marginBottom: 3 }}>AFTER RECEPTION</div>
              <div style={{ ...MONO, fontSize: 12, fontWeight: 700, color: levelColor(overallLevel!) }}>
                {overallLevel} · {(overallFraction * 100).toFixed(0)}%
              </div>
              <div style={{ ...SANS, fontSize: 10, color: '#8b949e', marginTop: 2 }}>
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
                  padding: '4px 6px', borderBottom: '1px solid #30363d',
                }}>
                  <span style={{ ...SANS, fontSize: 11, color: '#8b949e' }}>
                    {prettyObjectiveName(obj.name)}
                  </span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ ...SANS, fontSize: 10, color: '#656d76' }}>
                      BEFORE: UNAVAILABLE
                    </span>
                    <span style={{ color: '#656d76', fontSize: 10 }}>→</span>
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
          <div style={{ ...MONO, fontSize: 9, color: '#8b949e', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 6 }}>
            Ground Mission Update
          </div>
          <div style={{ ...SANS, fontSize: 12, color: '#8b949e', lineHeight: 1.6 }}>
            {missionUpdateText}
          </div>
        </div>
      )}

      {/* Spacecraft anomaly — still active */}
      {thermalAnomaly && (
        <div style={{
          ...CARD,
          borderColor: 'rgba(220,38,38,0.22)',
          background: 'rgba(220,38,38,0.04)',
        }}>
          <div style={{ ...MONO, fontSize: 9, color: '#f85149', letterSpacing: '0.07em', marginBottom: 4 }}>
            ⚠ SPACECRAFT ANOMALY — STILL ACTIVE
          </div>
          <div style={{ ...MONO, fontSize: 11, color: '#f85149', marginBottom: 3 }}>
            {thermalAnomaly.anomaly_id}
          </div>
          <div style={{ ...SANS, fontSize: 11, color: '#8b949e', lineHeight: 1.5, marginBottom: 6 }}>
            Ground received data products but the spacecraft thermal anomaly has not been physically resolved.
            Transmission improves ground knowledge — it does not repair onboard hardware.
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <span style={{ ...MONO, fontSize: 9, background: 'rgba(248,81,73,0.1)', color: '#f85149', border: '1px solid rgba(248,81,73,0.25)', borderRadius: 3, padding: '2px 6px' }}>
              ACTIVE
            </span>
            <span style={{ ...MONO, fontSize: 9, color: '#8b949e' }}>
              SEVERITY {(thermalAnomaly.severity * 100).toFixed(0)}%
            </span>
          </div>
        </div>
      )}

      {/* No objectives available for generic scenario */}
      {!groundInformationObjectives && (
        <div style={{ ...CARD }}>
          <div style={{ ...SANS, fontSize: 12, color: '#8b949e', lineHeight: 1.5 }}>
            Ground information objectives are not defined for this scenario.
          </div>
        </div>
      )}
    </div>
  );
}
