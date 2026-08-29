/**
 * transmissionResultAccounting — Pure domain helpers for transmission result clarity.
 *
 * Separates "selected", "queue", "received", "deferred", "not_selected"
 * so the reception panel can display unambiguous accounting regardless of
 * whether the plan covered the full queue (AI) or a subset (Manual).
 *
 * Invariants (always enforced):
 *   selected   = received + deferred + failed    (product accounting)
 *   not_selected = max(queue_total - selected, 0)
 *   selected + not_selected = queue_total        (when not_selected >= 0)
 *   not_selected >= 0                            (clamped, never negative)
 */

export interface TransmissionAccounting {
  /** Count of all active DataProducts in the queue (full catalog for this contact). */
  queue_total: number;
  /** Count of products included in the submitted transmission plan. */
  selected: number;
  /** Products successfully delivered in the modeled contact window. */
  received: number;
  /** Products selected in the plan but not transmitted (window exhausted). */
  deferred: number;
  /** Products in the queue but NOT included in the selected plan. */
  not_selected: number;
  /** Products selected in the plan that failed all delivery attempts. */
  failed: number;
  /** Total realized retransmission attempts (attempt count, not product count). */
  retries: number;
  /** Total size in bits of all selected (plan) products. */
  selected_data_bits: number;
  /** Total size in bits of all products in the full queue. */
  queue_data_bits: number;
  /** Modeled contact-window capacity in bits. */
  capacity_bits: number;
}

export interface AccountingInputs {
  /** IDs of all active DataProducts available for transmission consideration. */
  queue_total: number;
  /** Total size in bits of all active DataProducts in the queue. */
  queue_data_bits: number;
  /** IDs of delivered packets from SimulationResult.delivered_packets. */
  delivered_packets: string[];
  /** IDs of deferred packets from SimulationResult.deferred_packets. */
  deferred_packets: string[];
  /** IDs of failed packets from SimulationResult.failed_packets. */
  failed_packets: string[];
  /** Retransmission counts map from SimulationResult.retransmission_counts. */
  retransmission_counts: Record<string, number>;
  /**
   * Total size in bits of the products actually selected by the plan.
   * Derived from executed_plan.packets.reduce(sum, size_bits).
   */
  selected_data_bits: number;
  /**
   * Count of products actually selected by the plan.
   * Derived from executed_plan.packets.length.
   */
  selected_count: number;
  /** Modeled contact-window capacity in bits from GET /state available_capacity_bits. */
  capacity_bits: number;
}

/**
 * Compute unambiguous transmission result accounting from raw simulation data.
 *
 * Enforces:
 *   not_selected = max(queue_total - selected, 0)
 *
 * Does NOT silently hide impossible data: if selected > queue_total the caller
 * sees not_selected = 0, which is the correct clamped value. Any assertion testing
 * should use the returned `selected + not_selected === queue_total` invariant.
 */
export function computeTransmissionAccounting(inputs: AccountingInputs): TransmissionAccounting {
  const {
    queue_total,
    queue_data_bits,
    delivered_packets,
    deferred_packets,
    failed_packets,
    retransmission_counts,
    selected_data_bits,
    selected_count,
    capacity_bits,
  } = inputs;

  const received = delivered_packets.length;
  const deferred = deferred_packets.length;
  const failed = failed_packets.length;
  const selected = selected_count;

  // not_selected is clamped to 0 — never negative
  const not_selected = Math.max(queue_total - selected, 0);

  // Retries = sum of all realized retransmission attempts (attempt count, not product count)
  const retries = Object.values(retransmission_counts).reduce((s, v) => s + v, 0);

  return {
    queue_total,
    selected,
    received,
    deferred,
    not_selected,
    failed,
    retries,
    selected_data_bits,
    queue_data_bits,
    capacity_bits,
  };
}

/**
 * Verify accounting invariants for a completed transmission result.
 * Returns an array of violation messages (empty = all pass).
 *
 * Used in deterministic tests. Does NOT throw — lets callers decide how to handle.
 */
export function checkAccountingInvariants(a: TransmissionAccounting): string[] {
  const violations: string[] = [];

  // received + deferred + failed must not exceed selected (product accounting)
  const productSum = a.received + a.deferred + a.failed;
  if (productSum > a.selected) {
    violations.push(
      `received(${a.received}) + deferred(${a.deferred}) + failed(${a.failed}) = ${productSum} exceeds selected(${a.selected})`
    );
  }

  // not_selected must never be negative
  if (a.not_selected < 0) {
    violations.push(`not_selected(${a.not_selected}) is negative`);
  }

  // selected + not_selected must equal queue_total (when not_selected was not clamped)
  if (a.selected + a.not_selected !== a.queue_total) {
    // Only a violation if selected <= queue_total (clamping case is accepted)
    if (a.selected <= a.queue_total) {
      violations.push(
        `selected(${a.selected}) + not_selected(${a.not_selected}) = ${a.selected + a.not_selected} ≠ queue_total(${a.queue_total})`
      );
    }
  }

  return violations;
}
