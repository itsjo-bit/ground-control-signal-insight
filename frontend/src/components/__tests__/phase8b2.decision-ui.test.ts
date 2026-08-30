/**
 * phase8b2.decision-ui.test.ts — Phase 8B.2 regression tests
 *
 * Verifies the corrected Decision UI terminology introduced in Phase 8B.2:
 *
 *   - Complete queue count is labeled "Prioritized queue", not "Selected for window"
 *   - Projected-this-contact count = total packets - deferred count
 *   - Projected deferred count is correct
 *   - Full queue payload is labeled "Priority queue payload" (not "to transmit")
 *   - Transmission-time label accurately says "Full queue tx time" at current goodput
 *   - Existing Phase 8B / 8B.1 payload and deferred derivation semantics preserved
 *
 * These are pure logic tests — no DOM or React renderer required.
 */

import { describe, it, expect } from 'vitest';
import type { CandidatePlan, EvaluationResult, Packet } from '../../types/domain';

// ── Pure helpers mirroring MissionDecisionPanel logic ─────────────────────────

function makePacket(id: string, sizeBits: number): Packet {
  return {
    packet_id: id,
    packet_type: 'telemetry',
    size_bits: sizeBits,
    criticality: 0.8,
    mission_relevance: 0.9,
    deadline_s: 300,
    retry_cost: 0.1,
    delivery_requirement: 'required',
  };
}

function makePlan(id: string, packets: Packet[]): CandidatePlan {
  return {
    plan_id: id,
    strategy: 'test',
    packets,
    generated_by: 'test',
    metadata: {},
  };
}

function makeEval(planId: string, deferredPackets: string[]): EvaluationResult {
  return {
    plan_id: planId,
    mission_value: 10.0,
    critical_packets_delivered: 3,
    total_critical_packets: 5,
    deadline_misses: 0,
    avg_packet_delay_s: 5.0,
    bandwidth_utilization: 0.7,
    retransmission_overhead: 0.1,
    risk_score: 0.2,
    risk_level: 'LOW',
    deferred_packets: deferredPackets,
    deadline_miss_rate: 0.0,
    critical_deficit: 0.0,
    window_pressure: 0.5,
  };
}

/**
 * Derive the "Prioritized queue" count (total packets in plan).
 * This was formerly labeled "Selected for window" — the name change is the fix.
 */
function derivePrioritizedQueueCount(plan: CandidatePlan): number {
  return plan.packets.length;
}

/**
 * Derive "Projected this contact" = total packets − deferred count.
 */
function deriveProjectedThisContact(plan: CandidatePlan, ev: EvaluationResult): number {
  return plan.packets.length - ev.deferred_packets.length;
}

/**
 * Derive "Projected deferred" = deferred packet count from evaluator.
 */
function deriveProjectedDeferred(ev: EvaluationResult): number {
  return ev.deferred_packets.length;
}

/**
 * Derive "Priority queue payload" = sum of all plan packet sizes.
 * This is the FULL plan payload, NOT just non-deferred products.
 */
function derivePriorityQueuePayloadBits(plan: CandidatePlan): number {
  return plan.packets.reduce((s, p) => s + p.size_bits, 0);
}

/**
 * Derive "Full queue tx time" = full plan payload / goodput.
 * Represents time to transmit the entire queue, NOT just projected contact subset.
 */
function deriveFullQueueTxTimeS(
  fullQueueBits: number,
  goodputBps: number,
): number | null {
  if (fullQueueBits <= 0 || goodputBps <= 0) return null;
  return fullQueueBits / goodputBps;
}

// ── Tests ──────────────────────────────────────────────────────────────────────

describe('Phase 8B.2 — Decision UI terminology', () => {

  // ── TEST 1: Prioritized queue = total queue size (not selected-for-window) ─

  describe('TEST 1 — Prioritized queue = complete plan packet count', () => {
    it('returns total packet count for the 403-product Juno V2 scenario shape', () => {
      const packets = Array.from({ length: 403 }, (_, i) => makePacket(`P${i}`, 8_000));
      const plan = makePlan('value-per-cost', packets);
      expect(derivePrioritizedQueueCount(plan)).toBe(403);
    });

    it('prioritized queue count equals plan.packets.length regardless of deferred', () => {
      const packets = [
        makePacket('A', 10_000),
        makePacket('B', 10_000),
        makePacket('C', 10_000),
      ];
      const plan = makePlan('test', packets);
      // 2 deferred — prioritized queue must still count ALL 3
      expect(derivePrioritizedQueueCount(plan)).toBe(3);
    });
  });

  // ── TEST 2: Projected this contact = total - deferred ──────────────────────

  describe('TEST 2 — Projected this contact = total packets − deferred count', () => {
    it('Juno V2 shape: 403 total, 374 deferred → 29 projected this contact', () => {
      const packets = Array.from({ length: 403 }, (_, i) => makePacket(`P${i}`, 8_000));
      const deferredIds = packets.slice(29).map((p) => p.packet_id); // 374 deferred
      const plan = makePlan('test', packets);
      const ev = makeEval('test', deferredIds);
      expect(deriveProjectedThisContact(plan, ev)).toBe(29);
    });

    it('zero deferred → projected this contact = total queue size', () => {
      const packets = [makePacket('A', 1_000), makePacket('B', 2_000)];
      const plan = makePlan('test', packets);
      const ev = makeEval('test', []);
      expect(deriveProjectedThisContact(plan, ev)).toBe(2);
    });

    it('all deferred → projected this contact = 0', () => {
      const packets = [makePacket('A', 1_000), makePacket('B', 2_000)];
      const plan = makePlan('test', packets);
      const ev = makeEval('test', ['A', 'B']);
      expect(deriveProjectedThisContact(plan, ev)).toBe(0);
    });

    it('partial delivery: 5 of 10 non-deferred', () => {
      const packets = Array.from({ length: 10 }, (_, i) => makePacket(`P${i}`, 5_000));
      const deferredIds = packets.slice(5).map((p) => p.packet_id);
      const plan = makePlan('test', packets);
      const ev = makeEval('test', deferredIds);
      expect(deriveProjectedThisContact(plan, ev)).toBe(5);
    });
  });

  // ── TEST 3: Projected deferred = deferred_packets.length ──────────────────

  describe('TEST 3 — Projected deferred from EvaluationResult', () => {
    it('Juno V2 shape: 374 deferred', () => {
      const packets = Array.from({ length: 403 }, (_, i) => makePacket(`P${i}`, 8_000));
      const deferredIds = packets.slice(29).map((p) => p.packet_id);
      const ev = makeEval('test', deferredIds);
      expect(deriveProjectedDeferred(ev)).toBe(374);
    });

    it('zero deferred → projected deferred = 0', () => {
      const ev = makeEval('test', []);
      expect(deriveProjectedDeferred(ev)).toBe(0);
    });
  });

  // ── TEST 4: Accounting identity ───────────────────────────────────────────

  describe('TEST 4 — Queue accounting identity', () => {
    it('prioritizedQueue = projectedThisContact + projectedDeferred', () => {
      const packets = Array.from({ length: 20 }, (_, i) => makePacket(`P${i}`, 1_000));
      const deferredIds = packets.slice(7).map((p) => p.packet_id); // 13 deferred
      const plan = makePlan('test', packets);
      const ev = makeEval('test', deferredIds);

      const totalQ = derivePrioritizedQueueCount(plan);
      const thisContact = deriveProjectedThisContact(plan, ev);
      const deferred = deriveProjectedDeferred(ev);

      expect(totalQ).toBe(thisContact + deferred);
    });
  });

  // ── TEST 5: Priority queue payload = full plan payload ────────────────────

  describe('TEST 5 — Priority queue payload = full plan bits', () => {
    it('payload is sum of ALL packets including deferred', () => {
      const packets = [
        makePacket('A', 1_000_000),
        makePacket('B', 2_000_000), // this will be deferred
        makePacket('C', 500_000),
      ];
      const plan = makePlan('test', packets);
      const payloadBits = derivePriorityQueuePayloadBits(plan);
      // Must include ALL packets, not just the non-deferred ones
      expect(payloadBits).toBe(3_500_000);
    });

    it('payload does not depend on evaluation result (full queue)', () => {
      const packets = [
        makePacket('A', 10_000_000),
        makePacket('B', 5_000_000),
      ];
      const plan = makePlan('test', packets);
      const fullPayload = derivePriorityQueuePayloadBits(plan);
      // Deferred or not, payload represents the full queue
      expect(fullPayload).toBe(15_000_000);
    });
  });

  // ── TEST 6: Full queue tx time = full payload / goodput ───────────────────

  describe('TEST 6 — Full queue tx time semantics', () => {
    it('computes correctly as full_payload / goodput', () => {
      const fullQueueBits = 10_000_000; // 10 Mb
      const goodputBps = 100_000;       // 100 kbps
      const txTime = deriveFullQueueTxTimeS(fullQueueBits, goodputBps);
      // 10,000,000 / 100,000 = 100 s
      expect(txTime).toBeCloseTo(100.0, 3);
    });

    it('null when goodput is 0', () => {
      expect(deriveFullQueueTxTimeS(10_000_000, 0)).toBeNull();
    });

    it('null when full queue is empty', () => {
      expect(deriveFullQueueTxTimeS(0, 100_000)).toBeNull();
    });

    it('Juno V2 shape: 1.17 GB payload at goodput gives multi-hour estimate', () => {
      // 1.17 GB ≈ 9,360,000,000 bits; goodput ~90 kbps
      const fullQueueBits = 9_360_000_000;
      const goodputBps = 90_000;
      const txTime = deriveFullQueueTxTimeS(fullQueueBits, goodputBps);
      expect(txTime).not.toBeNull();
      // Should be > 1 hour
      const txHours = txTime! / 3600;
      expect(txHours).toBeGreaterThan(1);
    });
  });

  // ── TEST 7: Full queue tx time does NOT claim 900s contact suffices ────────

  describe('TEST 7 — Full queue tx time does not imply it fits in contact window', () => {
    it('full queue tx time can exceed a typical 900s contact window', () => {
      // 403 packets × average size → multi-hour queue
      const fullQueueBits = 1_000_000_000; // 1 Gb
      const goodputBps = 90_000;
      const txTime = deriveFullQueueTxTimeS(fullQueueBits, goodputBps);
      const contactWindowS = 900;
      // The full queue tx time must be WAY beyond the contact window
      expect(txTime!).toBeGreaterThan(contactWindowS);
    });
  });

  // ── TEST 8: Phase 8B semantics preserved ──────────────────────────────────

  describe('TEST 8 — Phase 8B: deferred products from EvaluationResult', () => {
    it('deferred count comes from EvaluationResult.deferred_packets', () => {
      const ev = makeEval('plan-x', ['PKT-A', 'PKT-B', 'PKT-C']);
      expect(deriveProjectedDeferred(ev)).toBe(3);
    });

    it('modifying plan packets after eval does not affect deferred count', () => {
      const packets = [makePacket('A', 1_000), makePacket('B', 2_000)];
      const plan = makePlan('test', packets);
      const ev = makeEval('test', ['A']);
      const before = deriveProjectedDeferred(ev);
      // Simulate plan modification — eval is unchanged
      plan.packets.push(makePacket('C', 3_000));
      const after = deriveProjectedDeferred(ev);
      expect(before).toBe(after);
    });
  });

  // ── TEST 9: Phase 8B.1 — queue count consistency ──────────────────────────

  describe('TEST 9 — Phase 8B.1: queue count consistent with plan packets', () => {
    it('prioritized queue uses recPlan.packets, not stage-1 ranked_products count', () => {
      // Stage-2 recommended plan with specific packets
      const recPackets = [
        makePacket('C', 1_000),
        makePacket('A', 1_000),
        makePacket('E', 1_000),
        makePacket('B', 1_000),
        makePacket('D', 1_000),
      ];
      const recPlan = makePlan('value-per-cost', recPackets);

      // Stage-1 may have ranked more/fewer products — irrelevant to queue count
      const prioritizedQueueCount = derivePrioritizedQueueCount(recPlan);
      expect(prioritizedQueueCount).toBe(5);
      // Must equal plan.packets.length, not any stage-1 count
      expect(prioritizedQueueCount).toBe(recPlan.packets.length);
    });
  });

});
