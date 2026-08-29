/**
 * TransmissionResultAccounting — deterministic domain tests
 *
 * Tests verify the pure accounting helper computeTransmissionAccounting()
 * and the invariant checker checkAccountingInvariants() against the five
 * required test cases from the feature specification.
 *
 * Test cases:
 *   TC-1  Manual 18 of 403 (small subset fits capacity)
 *   TC-2  AI full-queue 403 of 403 (29 received, 374 deferred)
 *   TC-3  Manual over-capacity (selected subset exceeds window)
 *   TC-4  Small manual selection (selected data < capacity)
 *   TC-5  No negative not_selected (selected > queue_total input guard)
 *
 * Framework: Vitest (runs via npm test).
 */

import { describe, it, expect } from 'vitest';
import {
  computeTransmissionAccounting,
  checkAccountingInvariants,
} from '../../utils/transmissionResultAccounting';
import type { AccountingInputs } from '../../utils/transmissionResultAccounting';

// ── Fixture helpers ────────────────────────────────────────────────────────────

function makeIds(prefix: string, count: number): string[] {
  return Array.from({ length: count }, (_, i) => `${prefix}${i + 1}`);
}

function makeInputs(overrides: Partial<AccountingInputs> = {}): AccountingInputs {
  return {
    queue_total: 10,
    queue_data_bits: 1_000_000,
    delivered_packets: [],
    deferred_packets: [],
    failed_packets: [],
    retransmission_counts: {},
    selected_data_bits: 0,
    selected_count: 0,
    capacity_bits: 500_000,
    ...overrides,
  };
}

// ── TC-1: Manual 18 of 403 ────────────────────────────────────────────────────

describe('TC-1 — Manual 18 of 403 (small subset, fits capacity)', () => {
  const QUEUE_TOTAL = 403;
  const SELECTED = 18;
  const delivered = makeIds('P', SELECTED);

  const inputs = makeInputs({
    queue_total: QUEUE_TOTAL,
    queue_data_bits: 9_350_000_000, // 9.35 Gbit
    delivered_packets: delivered,
    deferred_packets: [],
    failed_packets: [],
    retransmission_counts: {},
    selected_data_bits: 63_700_000, // 63.7 Mbit
    selected_count: SELECTED,
    capacity_bits: 81_000_000,      // 81 Mbit
  });

  const result = computeTransmissionAccounting(inputs);

  it('queue_total = 403', () => expect(result.queue_total).toBe(403));
  it('selected = 18', () => expect(result.selected).toBe(18));
  it('received = 18', () => expect(result.received).toBe(18));
  it('deferred = 0', () => expect(result.deferred).toBe(0));
  it('not_selected = 385', () => expect(result.not_selected).toBe(385));
  it('failed = 0', () => expect(result.failed).toBe(0));
  it('retries = 0', () => expect(result.retries).toBe(0));

  it('selected + not_selected = queue_total', () => {
    expect(result.selected + result.not_selected).toBe(result.queue_total);
  });

  it('passes all accounting invariants', () => {
    const violations = checkAccountingInvariants(result);
    expect(violations).toHaveLength(0);
  });
});

// ── TC-2: AI full-queue 403 of 403 ───────────────────────────────────────────

describe('TC-2 — AI full-queue 403 of 403 (29 received, 374 deferred)', () => {
  const QUEUE_TOTAL = 403;
  const SELECTED = 403;
  const RECEIVED = 29;
  const DEFERRED = 374;

  const delivered = makeIds('P', RECEIVED);
  const deferred = makeIds('D', DEFERRED);

  const inputs = makeInputs({
    queue_total: QUEUE_TOTAL,
    queue_data_bits: 9_350_000_000,
    delivered_packets: delivered,
    deferred_packets: deferred,
    failed_packets: [],
    retransmission_counts: {},
    selected_data_bits: 9_350_000_000, // full queue selected
    selected_count: SELECTED,
    capacity_bits: 81_000_000,
  });

  const result = computeTransmissionAccounting(inputs);

  it('queue_total = 403', () => expect(result.queue_total).toBe(403));
  it('selected = 403', () => expect(result.selected).toBe(403));
  it('received = 29', () => expect(result.received).toBe(29));
  it('deferred = 374', () => expect(result.deferred).toBe(374));
  it('not_selected = 0', () => expect(result.not_selected).toBe(0));
  it('failed = 0', () => expect(result.failed).toBe(0));

  it('received + deferred = selected (capacity bottleneck)', () => {
    expect(result.received + result.deferred).toBe(result.selected);
  });

  it('selected + not_selected = queue_total', () => {
    expect(result.selected + result.not_selected).toBe(result.queue_total);
  });

  it('passes all accounting invariants', () => {
    const violations = checkAccountingInvariants(result);
    expect(violations).toHaveLength(0);
  });
});

// ── TC-3: Manual over-capacity ────────────────────────────────────────────────

describe('TC-3 — Manual over-capacity (selected subset exceeds window)', () => {
  const QUEUE_TOTAL = 50;
  const SELECTED = 23;
  const RECEIVED = 22;
  const DEFERRED = 1;

  const delivered = makeIds('P', RECEIVED);
  const deferred = makeIds('DEF', DEFERRED);

  const inputs = makeInputs({
    queue_total: QUEUE_TOTAL,
    queue_data_bits: 500_000_000,
    delivered_packets: delivered,
    deferred_packets: deferred,
    failed_packets: [],
    retransmission_counts: {},
    selected_data_bits: 95_000_000,
    selected_count: SELECTED,
    capacity_bits: 90_000_000,
  });

  const result = computeTransmissionAccounting(inputs);

  it('deferred > 0 (manual can also defer)', () => expect(result.deferred).toBeGreaterThan(0));
  it('deferred = 1', () => expect(result.deferred).toBe(1));
  it('received = 22', () => expect(result.received).toBe(22));
  it('selected = 23', () => expect(result.selected).toBe(23));
  it('not_selected = queue_total - selected', () => {
    expect(result.not_selected).toBe(QUEUE_TOTAL - SELECTED);
  });

  it('selected + not_selected = queue_total', () => {
    expect(result.selected + result.not_selected).toBe(result.queue_total);
  });

  it('passes all accounting invariants', () => {
    const violations = checkAccountingInvariants(result);
    expect(violations).toHaveLength(0);
  });
});

// ── TC-4: Small manual selection (selected data < capacity) ──────────────────

describe('TC-4 — Small manual selection (selected data < capacity)', () => {
  const QUEUE_TOTAL = 100;
  const SELECTED = 5;
  const RECEIVED = 5;

  const delivered = makeIds('P', RECEIVED);

  const inputs = makeInputs({
    queue_total: QUEUE_TOTAL,
    queue_data_bits: 2_000_000_000,
    delivered_packets: delivered,
    deferred_packets: [],
    failed_packets: [],
    retransmission_counts: {},
    selected_data_bits: 10_000_000,  // 10 Mbit — well within capacity
    selected_count: SELECTED,
    capacity_bits: 500_000_000,      // 500 Mbit capacity
  });

  const result = computeTransmissionAccounting(inputs);

  it('all selected received', () => expect(result.received).toBe(result.selected));
  it('deferred = 0', () => expect(result.deferred).toBe(0));
  it('not_selected > 0', () => expect(result.not_selected).toBeGreaterThan(0));
  it('not_selected = 95', () => expect(result.not_selected).toBe(95));
  it('selected + not_selected = queue_total', () => {
    expect(result.selected + result.not_selected).toBe(result.queue_total);
  });

  it('passes all accounting invariants', () => {
    const violations = checkAccountingInvariants(result);
    expect(violations).toHaveLength(0);
  });
});

// ── TC-5: No negative not_selected (selected > queue_total guard) ─────────────

describe('TC-5 — No negative not_selected (selected > queue_total)', () => {
  // Malformed input: selected_count > queue_total (should never happen in production
  // but the helper must clamp safely, not return a negative value).
  const inputs = makeInputs({
    queue_total: 5,
    selected_count: 10,
    delivered_packets: makeIds('P', 10),
    deferred_packets: [],
    failed_packets: [],
    selected_data_bits: 100_000,
    capacity_bits: 200_000,
  });

  const result = computeTransmissionAccounting(inputs);

  it('not_selected is never negative', () => {
    expect(result.not_selected).toBeGreaterThanOrEqual(0);
  });

  it('not_selected is clamped to 0 when selected > queue_total', () => {
    expect(result.not_selected).toBe(0);
  });
});

// ── TC-6: Retries are attempt counts, not product counts ─────────────────────

describe('TC-6 — Retries are attempt counts, not product counts', () => {
  const inputs = makeInputs({
    queue_total: 10,
    selected_count: 3,
    delivered_packets: ['P1', 'P2', 'P3'],
    deferred_packets: [],
    failed_packets: [],
    retransmission_counts: { P1: 2, P2: 0, P3: 1 },
    selected_data_bits: 30_000,
    capacity_bits: 500_000,
  });

  const result = computeTransmissionAccounting(inputs);

  it('retries = sum of retransmission_counts values', () => {
    expect(result.retries).toBe(3); // 2 + 0 + 1
  });

  it('retries does not affect product accounting (received+deferred+failed=selected)', () => {
    const productSum = result.received + result.deferred + result.failed;
    expect(productSum).toBe(result.selected);
  });
});

// ── TC-7: formatBitsAsMbit utility ───────────────────────────────────────────

import { formatBitsAsMbit } from '../../utils/formatters';

describe('formatBitsAsMbit — bit-unit formatter', () => {
  it('formats Gbit range', () => {
    expect(formatBitsAsMbit(9_350_000_000)).toMatch(/Gbit/);
  });

  it('formats Mbit range', () => {
    expect(formatBitsAsMbit(81_000_000)).toMatch(/Mbit/);
  });

  it('formats kbit range', () => {
    expect(formatBitsAsMbit(1_500)).toMatch(/kbit/);
  });

  it('formats bit range', () => {
    expect(formatBitsAsMbit(500)).toMatch(/bit/);
    expect(formatBitsAsMbit(500)).not.toMatch(/kbit|Mbit|Gbit/);
  });

  it('does NOT present bits as bytes (81 Mbit ≠ 81 MB)', () => {
    // 81 Mbit = 81,000,000 bits; as bytes that would be 10.125 MB — but we want Mbit
    const result = formatBitsAsMbit(81_000_000);
    expect(result).toMatch(/81\.0 Mbit/);
    expect(result).not.toMatch(/MB/);
    expect(result).not.toMatch(/GB/);
  });
});
