/**
 * urgentCandidates.ts
 *
 * Production presentation helper for "urgent / operationally relevant" predicate.
 *
 * This is the same rule that Phase 4.2A tests verify produces exactly 23 candidates
 * from the 50 semantic candidates for ASTERIA-7. The predicate must NOT be used in:
 *   - CandidatePrioritizer
 *   - AI ranking
 *   - PlanEvaluator
 *   - benchmark
 *
 * Tests must import isUrgentOperationallyRelevant from here.
 */

import type { DataProduct } from '../types/domain';

/**
 * Determine if a data product is urgent / operationally relevant.
 *
 * A product meets the predicate if ANY of the following are true:
 *   1. It is linked to an applicable active anomaly (anomaly_id in applicableAnomalyIds)
 *   2. delivery_requirement == "required"
 *   3. deadline_s <= effectiveWindowS
 *
 * @param product            DataProduct to evaluate.
 * @param applicableAnomalyIds  Set of active anomaly IDs relevant to this contact.
 * @param effectiveWindowS   Contact window duration in seconds.
 */
export function isUrgentOperationallyRelevant(
  product: DataProduct,
  applicableAnomalyIds: ReadonlySet<string>,
  effectiveWindowS: number,
): boolean {
  if (product.anomaly_id !== null && applicableAnomalyIds.has(product.anomaly_id)) {
    return true;
  }
  if (product.delivery_requirement === 'required') {
    return true;
  }
  if (product.deadline_s <= effectiveWindowS) {
    return true;
  }
  return false;
}

/**
 * Count urgent / operationally relevant products from a list.
 *
 * @param products           Array of DataProducts (candidates or all products).
 * @param applicableAnomalyIds  Set of active anomaly IDs.
 * @param effectiveWindowS   Contact window in seconds.
 */
export function countUrgentProducts(
  products: DataProduct[],
  applicableAnomalyIds: ReadonlySet<string>,
  effectiveWindowS: number,
): number {
  return products.filter((p) =>
    isUrgentOperationallyRelevant(p, applicableAnomalyIds, effectiveWindowS),
  ).length;
}

/**
 * Filter to urgent / operationally relevant products.
 */
export function filterUrgentProducts(
  products: DataProduct[],
  applicableAnomalyIds: ReadonlySet<string>,
  effectiveWindowS: number,
): DataProduct[] {
  return products.filter((p) =>
    isUrgentOperationallyRelevant(p, applicableAnomalyIds, effectiveWindowS),
  );
}
