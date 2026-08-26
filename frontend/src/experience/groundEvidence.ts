/**
 * groundEvidence.ts
 *
 * Production ground-evidence helper for GCSI Phase 4.2F.
 *
 * Computes objective coverage based on actually delivered packet IDs and
 * the ground_information_objectives map from the experience manifest.
 *
 * PRESENTATION LOGIC ONLY — do not add these thresholds to MissionOutcomeEvaluator.
 * Tests must import from this module, not reimplement.
 */

// ── Coverage levels ───────────────────────────────────────────────────────────

export type GroundEvidenceLevel = 'LOW' | 'MEDIUM' | 'HIGH';

/** Coverage level thresholds (fraction, not percentage). */
export const EVIDENCE_THRESHOLD_HIGH = 0.80;
export const EVIDENCE_THRESHOLD_MEDIUM = 0.40;

/**
 * Classify a coverage fraction into a display level.
 *
 * LOW:    < 40%
 * MEDIUM: >= 40% and < 80%
 * HIGH:   >= 80%
 */
export function groundEvidenceLevel(fraction: number): GroundEvidenceLevel {
  if (fraction >= EVIDENCE_THRESHOLD_HIGH) return 'HIGH';
  if (fraction >= EVIDENCE_THRESHOLD_MEDIUM) return 'MEDIUM';
  return 'LOW';
}

// ── Per-objective coverage ────────────────────────────────────────────────────

export interface ObjectiveCoverage {
  name: string;
  requiredIds: string[];
  deliveredIds: string[];
  fraction: number;           // 0.0 – 1.0
  level: GroundEvidenceLevel; // derived from fraction
}

/**
 * Compute per-objective coverage as fraction of required products delivered.
 *
 * @param deliveredIds  Set of product IDs confirmed delivered by the simulator.
 * @param objectives    Map of objective name → required product IDs.
 * @returns             Array of ObjectiveCoverage (one per objective).
 */
export function assessGroundObjectives(
  deliveredIds: ReadonlySet<string>,
  objectives: Readonly<Record<string, string[]>>,
): ObjectiveCoverage[] {
  return Object.entries(objectives).map(([name, ids]) => {
    if (ids.length === 0) {
      return {
        name,
        requiredIds: [],
        deliveredIds: [],
        fraction: 1.0,
        level: 'HIGH' as GroundEvidenceLevel,
      };
    }
    const delivered = ids.filter((id) => deliveredIds.has(id));
    const fraction = delivered.length / ids.length;
    return {
      name,
      requiredIds: ids,
      deliveredIds: delivered,
      fraction,
      level: groundEvidenceLevel(fraction),
    };
  });
}

// ── Overall coverage ──────────────────────────────────────────────────────────

/**
 * Compute overall coverage as fraction of *all* required objective IDs delivered.
 *
 * @param deliveredIds  Set of product IDs confirmed delivered.
 * @param objectives    Map of objective name → required product IDs.
 * @returns             Fraction in [0, 1].
 */
export function overallGroundEvidenceCoverage(
  deliveredIds: ReadonlySet<string>,
  objectives: Readonly<Record<string, string[]>>,
): number {
  const allIds = Object.values(objectives).flat();
  if (allIds.length === 0) return 1.0;
  const deliveredCount = allIds.filter((id) => deliveredIds.has(id)).length;
  return deliveredCount / allIds.length;
}

// ── Deterministic mission update text ────────────────────────────────────────

/**
 * Generate a deterministic template-based mission update text.
 * NO AI call. Derives text from objective availability only.
 *
 * @param objectives  Per-objective coverage computed by assessGroundObjectives.
 * @param overallFraction  Overall coverage fraction from overallGroundEvidenceCoverage.
 * @returns  Mission update text (2-3 sentences max).
 */
export function generateMissionUpdateText(
  objectives: ObjectiveCoverage[],
  overallFraction: number,
): string {
  const byName = Object.fromEntries(objectives.map((o) => [o.name, o]));
  const thermalHistory = byName['fresh_thermal_history'];
  const faultContext = byName['fault_control_context'];
  const anomalyTimeline = byName['anomaly_event_timeline'];

  const level = groundEvidenceLevel(overallFraction);

  if (level === 'HIGH') {
    const thermalNote =
      thermalHistory?.fraction >= 1.0
        ? 'Ground now has the current high-rate thermal history and associated fault-control context required for detailed anomaly review.'
        : 'Ground received most thermal evidence required for anomaly review.';
    return thermalNote + ' Spacecraft thermal anomaly ANOM-THERM-017 remains active — no physical resolution has occurred.';
  }

  if (level === 'MEDIUM') {
    const missing: string[] = [];
    if ((thermalHistory?.fraction ?? 0) < 1.0) missing.push('fresh thermal history');
    if ((faultContext?.fraction ?? 0) < 1.0) missing.push('fault/control context');
    if ((anomalyTimeline?.fraction ?? 0) < 1.0) missing.push('anomaly event timeline');
    const missingNote = missing.length > 0
      ? ` Additional anomaly context remains unavailable: ${missing.join(', ')}.`
      : '';
    return `Ground received partial thermal evidence.${missingNote} Prioritize missing context in a later contact.`;
  }

  // LOW
  return 'Ground received minimal thermal context. The key anomaly products were not delivered or deferred. Prioritize these products in the next contact window.';
}

// ── Availability label ────────────────────────────────────────────────────────

/** Convert a fraction to a human-readable availability label. */
export function objectiveAvailabilityLabel(fraction: number): 'AVAILABLE' | 'PARTIAL' | 'UNAVAILABLE' {
  if (fraction >= 1.0) return 'AVAILABLE';
  if (fraction > 0.0) return 'PARTIAL';
  return 'UNAVAILABLE';
}
