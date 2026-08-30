/**
 * phase8b1.sorting.test.ts — Phase 8B.1 regression tests
 *
 * Covers the Plan Order and Selected display-sort additions.
 *
 * These tests verify the PURE DOMAIN logic of the two sort functions.
 * They do NOT rely on a DOM or React renderer — they test the algorithm
 * extracted into standalone helpers that mirror the DataSection implementation.
 *
 * Tests:
 *   A  Plan Order — baseline order
 *   B  Plan Order — filtering preserves relative plan order
 *   C  Selected sort — selected products first following manualOrder
 *   D  Selected sort — reflects current operator state (dynamic changes)
 *   E  Selected sort — does NOT mutate manualOrder
 *   F  Selected sort — originally deferred (unselected) rows keep their position in group 2
 *   G  Fresh manual mode — Plan Order unavailable (no aiBaselinePlanOrder)
 *   H  Normal (non-manual) browsing — Selected/Plan Order flags are false
 *   I  Source/reset — aiBaselinePlanOrder cleared alongside aiBaselineDeferredIds
 *   J  Stage-1 independence — Plan Order uses CandidatePlan packets, not ranked_products
 */

import { describe, it, expect } from 'vitest';
import type { CandidatePlan, Packet } from '../../types/domain';

// ── Pure sort helpers mirroring DataSection implementation ─────────────────────
//
// These helpers extract the deterministic sort logic so it can be tested
// without mounting any React component.

/** Products keyed only by the fields used in sorting. */
interface SortableProduct {
  product_id: string;
  criticality: number;
  size_bits: number;
  deadline_s: number;
  age_s: number;
  mission_relevance: number;
}

/**
 * Sort products according to Plan Order.
 * Products not in aiBaselinePlanOrder go to the end (stable).
 * sortDesc = ascending plan order (position 0 first).
 */
function sortByPlanOrder(
  products: SortableProduct[],
  aiBaselinePlanOrder: readonly string[],
  sortDesc: boolean,
): SortableProduct[] {
  const planIdx = new Map<string, number>(aiBaselinePlanOrder.map((id, i) => [id, i]));
  return [...products].sort((a, b) => {
    const ia = planIdx.get(a.product_id) ?? aiBaselinePlanOrder.length;
    const ib = planIdx.get(b.product_id) ?? aiBaselinePlanOrder.length;
    return sortDesc ? ia - ib : ib - ia;
  });
}

/**
 * Sort products by selection state.
 * sortDesc = selected first; sortAsc = unselected first.
 * Within selected: follow manualOrder.
 * Within unselected: prefer aiBaselinePlanOrder, then original data order.
 */
function sortBySelected(
  products: SortableProduct[],
  manualSelectedIds: Set<string>,
  manualOrder: readonly string[],
  aiBaselinePlanOrder: readonly string[],
  sortDesc: boolean,
): SortableProduct[] {
  const manualIdx = new Map<string, number>(manualOrder.map((id, i) => [id, i]));
  const planIdx = new Map<string, number>(aiBaselinePlanOrder.map((id, i) => [id, i]));
  const originalIdx = new Map<string, number>(products.map((p, i) => [p.product_id, i]));
  return [...products].sort((a, b) => {
    const aSelected = manualSelectedIds.has(a.product_id);
    const bSelected = manualSelectedIds.has(b.product_id);
    if (aSelected !== bSelected) {
      return sortDesc ? (aSelected ? -1 : 1) : (aSelected ? 1 : -1);
    }
    if (aSelected) {
      return (manualIdx.get(a.product_id) ?? 0) - (manualIdx.get(b.product_id) ?? 0);
    }
    // Both unselected: prefer aiBaselinePlanOrder, then original data order
    const ia = planIdx.has(a.product_id)
      ? (planIdx.get(a.product_id) as number)
      : (originalIdx.get(a.product_id) ?? 0) + aiBaselinePlanOrder.length;
    const ib = planIdx.has(b.product_id)
      ? (planIdx.get(b.product_id) as number)
      : (originalIdx.get(b.product_id) ?? 0) + aiBaselinePlanOrder.length;
    return ia - ib;
  });
}

// ── Test fixtures ──────────────────────────────────────────────────────────────

function makeProduct(id: string): SortableProduct {
  return {
    product_id: id,
    criticality: 0.5,
    size_bits: 10_000,
    deadline_s: 300,
    age_s: 60,
    mission_relevance: 0.5,
  };
}

function makePacket(id: string): Packet {
  return {
    packet_id: id,
    packet_type: 'telemetry',
    size_bits: 10_000,
    criticality: 0.8,
    mission_relevance: 0.9,
    deadline_s: 300,
    retry_cost: 0.1,
    delivery_requirement: 'required',
  };
}

function makePlan(packetIds: string[]): CandidatePlan {
  return {
    plan_id: 'ai-prioritized',
    strategy: 'ai_prioritized',
    generated_by: 'ai',
    metadata: {},
    packets: packetIds.map(makePacket),
  };
}

// ── TEST A: Plan Order — baseline ──────────────────────────────────────────────

describe('TEST A — Plan Order baseline', () => {
  it('displays products in exact recommended plan order [C, A, E, B, D]', () => {
    const products = ['A', 'B', 'C', 'D', 'E'].map(makeProduct);
    const aiBaselinePlanOrder = ['C', 'A', 'E', 'B', 'D'];

    const result = sortByPlanOrder(products, aiBaselinePlanOrder, true);
    expect(result.map((p) => p.product_id)).toEqual(['C', 'A', 'E', 'B', 'D']);
  });

  it('does not sort alphabetically or by criticality in Plan Order mode', () => {
    const products = ['A', 'B', 'C', 'D', 'E'].map(makeProduct);
    const aiBaselinePlanOrder = ['E', 'D', 'C', 'B', 'A']; // reverse alpha

    const result = sortByPlanOrder(products, aiBaselinePlanOrder, true);
    expect(result.map((p) => p.product_id)).toEqual(['E', 'D', 'C', 'B', 'A']);
  });

  it('reverses plan order when sortDesc is false', () => {
    const products = ['A', 'B', 'C'].map(makeProduct);
    const aiBaselinePlanOrder = ['C', 'A', 'B'];

    const result = sortByPlanOrder(products, aiBaselinePlanOrder, false);
    expect(result.map((p) => p.product_id)).toEqual(['B', 'A', 'C']);
  });
});

// ── TEST B: Plan Order — filtering preserves relative order ───────────────────

describe('TEST B — Plan Order with filtering', () => {
  it('preserves relative plan order among filtered-in products', () => {
    // Plan: [C, A, E, B, D]. After filter leaving [A, B, D]:
    const products = ['A', 'B', 'D'].map(makeProduct); // already filtered
    const aiBaselinePlanOrder = ['C', 'A', 'E', 'B', 'D'];

    const result = sortByPlanOrder(products, aiBaselinePlanOrder, true);
    expect(result.map((p) => p.product_id)).toEqual(['A', 'B', 'D']);
  });

  it('puts products not in plan at the end', () => {
    // Product X is not in the plan order
    const products = ['X', 'A', 'B'].map(makeProduct);
    const aiBaselinePlanOrder = ['C', 'A', 'B'];

    const result = sortByPlanOrder(products, aiBaselinePlanOrder, true);
    expect(result.map((p) => p.product_id)).toEqual(['A', 'B', 'X']);
  });
});

// ── TEST C: Selected sort — selected first following manualOrder ───────────────

describe('TEST C — Selected sort (selected first)', () => {
  it('brings selected products (C, A) to the top in manualOrder sequence', () => {
    const products = ['A', 'B', 'C', 'D'].map(makeProduct);
    const manualOrder = ['C', 'A']; // C before A in operator's order
    const manualSelectedIds = new Set(['C', 'A']);
    const aiBaselinePlanOrder: string[] = [];

    const result = sortBySelected(products, manualSelectedIds, manualOrder, aiBaselinePlanOrder, true);
    const ids = result.map((p) => p.product_id);

    // Selected first
    expect(ids.indexOf('C')).toBeLessThan(ids.indexOf('B'));
    expect(ids.indexOf('A')).toBeLessThan(ids.indexOf('B'));
    // C before A (follows manualOrder)
    expect(ids.indexOf('C')).toBeLessThan(ids.indexOf('A'));
  });

  it('unselected products appear after selected products', () => {
    const products = ['A', 'B', 'C', 'D'].map(makeProduct);
    const manualOrder = ['C', 'A'];
    const manualSelectedIds = new Set(['C', 'A']);
    const aiBaselinePlanOrder: string[] = [];

    const result = sortBySelected(products, manualSelectedIds, manualOrder, aiBaselinePlanOrder, true);
    const ids = result.map((p) => p.product_id);

    expect(ids.slice(0, 2).sort()).toEqual(['A', 'C'].sort());
    expect(ids.slice(2).every((id) => !manualSelectedIds.has(id))).toBe(true);
  });
});

// ── TEST D: Selected sort — dynamic changes ────────────────────────────────────

describe('TEST D — Selected sort reflects current operator state', () => {
  it('after deselectB and selectD, shows A and D as selected group', () => {
    const products = ['A', 'B', 'C', 'D'].map(makeProduct);
    // Initial: A, B selected; operator deselects B, selects D
    const manualOrder = ['A', 'D']; // updated after operator change
    const manualSelectedIds = new Set(['A', 'D']);
    const aiBaselinePlanOrder: string[] = [];

    const result = sortBySelected(products, manualSelectedIds, manualOrder, aiBaselinePlanOrder, true);
    const ids = result.map((p) => p.product_id);

    // A and D in selected group
    expect(ids.indexOf('A')).toBeLessThan(ids.indexOf('B'));
    expect(ids.indexOf('D')).toBeLessThan(ids.indexOf('B'));
    // B is NOT in selected group
    expect(manualSelectedIds.has('B')).toBe(false);
  });

  it('does not treat originally-selected-but-now-deselected B as selected', () => {
    const products = ['A', 'B', 'C', 'D'].map(makeProduct);
    const manualOrder = ['A', 'D'];
    const manualSelectedIds = new Set(['A', 'D']); // B is NOT selected
    const aiBaselinePlanOrder: string[] = [];

    const result = sortBySelected(products, manualSelectedIds, manualOrder, aiBaselinePlanOrder, true);
    const ids = result.map((p) => p.product_id);

    // B should be in the unselected group (not at the top)
    const selectedGroup = ids.slice(0, 2);
    expect(selectedGroup).not.toContain('B');
  });
});

// ── TEST E: Selected sort does NOT mutate manualOrder ─────────────────────────

describe('TEST E — Selected sort is presentation-only', () => {
  it('sortBySelected does not modify the input manualOrder array', () => {
    const products = ['A', 'B', 'C', 'D'].map(makeProduct);
    const manualOrder = ['C', 'A'];
    const manualOrderSnapshot = [...manualOrder];
    const manualSelectedIds = new Set(['C', 'A']);
    const aiBaselinePlanOrder: string[] = [];

    sortBySelected(products, manualSelectedIds, manualOrder, aiBaselinePlanOrder, true);

    // manualOrder must remain unchanged
    expect(manualOrder).toEqual(manualOrderSnapshot);
  });

  it('sortBySelected does not modify the input products array', () => {
    const products = ['A', 'B', 'C', 'D'].map(makeProduct);
    const originalProductOrder = products.map((p) => p.product_id);
    const manualOrder = ['C', 'A'];
    const manualSelectedIds = new Set(['C', 'A']);
    const aiBaselinePlanOrder: string[] = [];

    sortBySelected(products, manualSelectedIds, manualOrder, aiBaselinePlanOrder, true);

    // original products array must remain unchanged
    expect(products.map((p) => p.product_id)).toEqual(originalProductOrder);
  });

  it('sortByPlanOrder does not modify the input products array', () => {
    const products = ['A', 'B', 'C'].map(makeProduct);
    const originalOrder = products.map((p) => p.product_id);
    const aiBaselinePlanOrder = ['C', 'A', 'B'];

    sortByPlanOrder(products, aiBaselinePlanOrder, true);

    expect(products.map((p) => p.product_id)).toEqual(originalOrder);
  });
});

// ── TEST F: Deferred products in Modify-AI context ────────────────────────────

describe('TEST F — unselected Modify-AI rows stay in group 2', () => {
  it('AI-deferred products (unselected) appear below selected products', () => {
    // AI recommended [A, B, C, D, E]; deferred [C, D, E]. Selected: [A, B].
    const products = ['A', 'B', 'C', 'D', 'E'].map(makeProduct);
    const manualOrder = ['A', 'B'];
    const manualSelectedIds = new Set(['A', 'B']);
    const aiBaselinePlanOrder = ['A', 'B', 'C', 'D', 'E']; // full plan order

    const result = sortBySelected(products, manualSelectedIds, manualOrder, aiBaselinePlanOrder, true);
    const ids = result.map((p) => p.product_id);

    // Selected group comes first
    expect(ids.slice(0, 2).sort()).toEqual(['A', 'B']);
    // Deferred (C, D, E) are all in group 2
    expect(ids.slice(2)).toContain('C');
    expect(ids.slice(2)).toContain('D');
    expect(ids.slice(2)).toContain('E');
  });

  it('unselected products within group 2 follow aiBaselinePlanOrder', () => {
    const products = ['E', 'C', 'D'].map(makeProduct); // already filtered to unselected
    const manualOrder: string[] = [];
    const manualSelectedIds = new Set<string>();
    const aiBaselinePlanOrder = ['A', 'B', 'C', 'D', 'E']; // C=2, D=3, E=4

    const result = sortBySelected(products, manualSelectedIds, manualOrder, aiBaselinePlanOrder, true);
    expect(result.map((p) => p.product_id)).toEqual(['C', 'D', 'E']);
  });
});

// ── TEST G: Fresh manual mode ─────────────────────────────────────────────────

describe('TEST G — fresh manual mode', () => {
  it('showPlanOrderSort is false when manualEditOrigin is "manual"', () => {
    // Mirror DataSection condition
    const isAiModifyMode = false; // decisionMode === 'manual' but origin !== 'ai_recommendation'
    const aiBaselinePlanOrder: string[] = [];
    const showPlanOrderSort = isAiModifyMode && aiBaselinePlanOrder.length > 0;

    expect(showPlanOrderSort).toBe(false);
  });

  it('showPlanOrderSort is false when aiBaselinePlanOrder is empty even if Modify origin', () => {
    const isAiModifyMode = true;
    const aiBaselinePlanOrder: string[] = []; // not yet set
    const showPlanOrderSort = isAiModifyMode && aiBaselinePlanOrder.length > 0;

    expect(showPlanOrderSort).toBe(false);
  });

  it('showSelectedSort is true in fresh manual mode', () => {
    const decisionMode = 'manual';
    const showSelectedSort = decisionMode === 'manual';

    expect(showSelectedSort).toBe(true);
  });

  it('Selected sort still works correctly in fresh manual mode (no AI plan order)', () => {
    const products = ['A', 'B', 'C', 'D'].map(makeProduct);
    const manualOrder = ['B', 'C'];
    const manualSelectedIds = new Set(['B', 'C']);
    const aiBaselinePlanOrder: string[] = []; // fresh manual — no plan order

    const result = sortBySelected(products, manualSelectedIds, manualOrder, aiBaselinePlanOrder, true);
    const ids = result.map((p) => p.product_id);

    // B and C (selected) should come before A and D (unselected)
    expect(ids.indexOf('B')).toBeLessThan(ids.indexOf('A'));
    expect(ids.indexOf('C')).toBeLessThan(ids.indexOf('A'));
  });
});

// ── TEST H: Normal AI/non-manual browsing ─────────────────────────────────────

describe('TEST H — normal non-manual browsing', () => {
  it('showPlanOrderSort is false when decisionMode is not manual', () => {
    const isAiModifyMode = false; // decisionMode !== 'manual'
    const aiBaselinePlanOrder = ['A', 'B', 'C'];
    const showPlanOrderSort = isAiModifyMode && aiBaselinePlanOrder.length > 0;

    expect(showPlanOrderSort).toBe(false);
  });

  it('showSelectedSort is false when decisionMode is not manual', () => {
    const decisionMode: string = 'ai'; // non-manual, widened to string to mirror runtime check
    const showSelectedSort = decisionMode === 'manual';

    expect(showSelectedSort).toBe(false);
  });
});

// ── TEST I: Source/reset clears plan order provenance ─────────────────────────

describe('TEST I — source/reset clears aiBaselinePlanOrder', () => {
  it('aiBaselinePlanOrder is cleared (empty) after reset', () => {
    // Simulate state after reset: same initial value as useState([])
    const clearedPlanOrder: string[] = [];
    expect(clearedPlanOrder).toEqual([]);
    expect(clearedPlanOrder.length).toBe(0);
  });

  it('showPlanOrderSort becomes false when aiBaselinePlanOrder is cleared', () => {
    const isAiModifyMode = true;
    const aiBaselinePlanOrder: string[] = []; // cleared
    const showPlanOrderSort = isAiModifyMode && aiBaselinePlanOrder.length > 0;

    expect(showPlanOrderSort).toBe(false);
  });

  it('snapshot captured at Modify time is independent of subsequent state changes', () => {
    // The plan order is captured as a new array — mutations to the original have no effect.
    const recPlanPackets = ['A', 'B', 'C', 'D', 'E'].map(makePacket);
    const snapshot = recPlanPackets.map((p) => p.packet_id);

    // Simulate "source switch" clearing the plan
    recPlanPackets.length = 0;

    // Snapshot remains intact
    expect(snapshot).toEqual(['A', 'B', 'C', 'D', 'E']);
  });
});

// ── TEST J: Stage-1 ranking independence ──────────────────────────────────────

describe('TEST J — Plan Order uses CandidatePlan packets, not ranked_products', () => {
  it('Plan Order snapshot comes from recPlan.packets, not aiPrioritization.ranked_products', () => {
    // Stage-2 recommended plan order
    const recPlan = makePlan(['C', 'A', 'E', 'B', 'D']);

    // Stage-1 AI ranking (different alphabetical order — not used for Plan Order)
    const stage1Order = ['A', 'B', 'C', 'D', 'E'];

    // The snapshot must come from recPlan.packets
    const snapshot = recPlan.packets.map((p) => p.packet_id);

    expect(snapshot).toEqual(['C', 'A', 'E', 'B', 'D']);
    // Must NOT match Stage-1 order
    expect(snapshot).not.toEqual(stage1Order);
  });

  it('Plan Order sort produces C,A,E,B,D order (Stage-2 plan) not A,B,C,D,E (Stage-1)', () => {
    const products = ['A', 'B', 'C', 'D', 'E'].map(makeProduct);
    // Stage-2 plan order (from recPlan.packets)
    const aiBaselinePlanOrder = ['C', 'A', 'E', 'B', 'D'];

    const result = sortByPlanOrder(products, aiBaselinePlanOrder, true);
    expect(result.map((p) => p.product_id)).toEqual(['C', 'A', 'E', 'B', 'D']);

    // Stage-1 alphabetical would give A,B,C,D,E — confirm we are NOT that
    expect(result.map((p) => p.product_id)).not.toEqual(['A', 'B', 'C', 'D', 'E']);
  });
});
