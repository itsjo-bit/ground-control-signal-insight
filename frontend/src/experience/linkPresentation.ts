/**
 * linkPresentation.ts
 *
 * Presentation-only link state classification for GCSI Phase 4.2F.
 *
 * This helper classifies the link into NOMINAL / DEGRADED / CRITICAL
 * for display purposes ONLY. It does NOT change the scientific LinkState
 * values and is NOT used by the evaluator risk formulas.
 *
 * Key rule: very low BER alone must NOT classify ASTERIA as NOMINAL
 * when SNR is clearly degraded (e.g. SNR 2.8 dB, stability 68%).
 *
 * Tests must import from this module.
 */

import type { LinkState } from '../types/domain';
import type { HistoricalSnrPoint } from '../types/experience';

// ── Display classification ────────────────────────────────────────────────────

export type PresentationLinkStatus = 'NOMINAL' | 'DEGRADED' | 'CRITICAL';

/**
 * Classify the link for presentation purposes.
 *
 * CRITICAL:  (SNR < 5 AND stability < 0.5) OR SNR < 2
 * DEGRADED:  SNR < 10 OR stability < 0.75
 * NOMINAL:   otherwise
 *
 * NOTE: BER is NOT used as the sole classifier here. A very low BER can
 * coexist with degraded SNR (ASTERIA scenario: BER ~3e-10, SNR 2.8 dB).
 */
export function presentationLinkStatus(linkState: LinkState): PresentationLinkStatus {
  const { snr_db, link_stability } = linkState;

  if (snr_db < 2 || (snr_db < 5 && link_stability < 0.5)) return 'CRITICAL';
  if (snr_db < 10 || link_stability < 0.75) return 'DEGRADED';
  return 'NOMINAL';
}

// ── SNR trend ─────────────────────────────────────────────────────────────────

export type SnrTrend = '↑ RISING' | '→ STABLE' | '↓ DECLINING';

/**
 * Derive a display trend label from the SNR history.
 *
 * Uses the last few samples (if available) or falls back to a single-point
 * classification based on current SNR.
 */
export function presentationSnrTrend(
  currentSnr: number,
  snrHistory?: HistoricalSnrPoint[],
): SnrTrend {
  if (snrHistory && snrHistory.length >= 3) {
    const recent = snrHistory.slice(-3);
    const first = recent[0].snr_db;
    const last = recent[recent.length - 1].snr_db;
    const delta = last - first;
    if (delta < -0.3) return '↓ DECLINING';
    if (delta > 0.3) return '↑ RISING';
    return '→ STABLE';
  }
  // Fallback: single-value heuristic
  if (currentSnr < 5) return '↓ DECLINING';
  if (currentSnr > 15) return '↑ RISING';
  return '→ STABLE';
}

// ── Three.js link health status ───────────────────────────────────────────────

/**
 * Map presentation link status to the Three.js CommunicationLink health bucket.
 * Kept as a helper so the 3D scene and 2D panels share the same logic.
 */
export type ThreeDLinkStatus = 'good' | 'warning' | 'critical' | 'transmitting';

export function toThreeDLinkStatus(
  presentationStatus: PresentationLinkStatus,
  transmitting: boolean,
): ThreeDLinkStatus {
  if (transmitting) return 'transmitting';
  if (presentationStatus === 'CRITICAL') return 'critical';
  if (presentationStatus === 'DEGRADED') return 'warning';
  return 'good';
}
