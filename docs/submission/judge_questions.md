# GCSI Hard Judge Questions & Answers

Prepared answers to the most likely adversarial questions from a technically-competent judge.

---

## Q: Why AI? Couldn't you just sort by criticality?

**A**: Criticality sorting cannot account for anomaly context or semantic relationships between
products. During an active thermal anomaly, a moderate-criticality thermal telemetry stream from
the last 11 minutes may be more mission-critical than a high-criticality routine survey from six
days ago — but a numeric sort cannot reason about "fresh thermal history during unresolved anomaly."

AI semantic reasoning understands what the data *means* in the current mission context. That is
the specific gap it fills. Deterministic scoring handles the rest.

---

## Q: Does the AI read all 1,284 products?

**A**: No. GCSI deterministically screens the 1,284-product queue to a bounded 50-candidate set
before any AI call. The `CandidatePrioritizer` selects anomaly-linked, critical, near-deadline,
and high-relevance products. The AI semantically prioritizes this representative 50-candidate set.

This is intentional architecture: the AI sees the most operationally relevant candidates, not
all 1,284 products. Context quality is controlled; token usage is bounded; the most important
products are always represented regardless of AI output.

---

## Q: Why should I trust the AI?

**A**: You shouldn't trust it blindly — and GCSI is designed so you don't have to.

The AI recommendation is advisory. It competes against four deterministic baseline plans under
identical evaluation metrics. The Stage-2 recommendation is provenance-blind: the AI sees
OPTION-A through OPTION-E aliases and cannot know which option it generated. Evidence values are
bound from deterministic evaluators — not AI-supplied.

The operator sees all five plans, the evaluation evidence, and the AI reasoning. They can accept,
modify, or reject. The trust model is "informed evidence for a human decision," not "autonomous AI action."

---

## Q: What happens when the AI is wrong?

**A**: Several safety layers:

1. If the AI recommends a plan that is objectively worse on the primary metrics, the operator can
   see this in the plan comparison and choose a better deterministic baseline.
2. If the AI provider fails, the system falls back to `LocalRuleBasedProvider` deterministically.
   The fallback is clearly labeled — not silently substituted.
3. If the AI recommendation references an invalid plan, finalization fails closed to Local.
4. If the operator disagrees, they can reject or modify the plan.
5. No transmission occurs without explicit operator approval.

---

## Q: Can the AI change packet facts?

**A**: No. The AI receives product metadata for reasoning purposes. When the operator approves a
plan, the backend reconstructs authoritative packet facts from its own registry. Client-submitted
data is used for intent verification only — the backend rejects any factual tampering.

The AI cannot modify packet_id, size_bits, subsystem, anomaly_id, or any other authoritative field.

---

## Q: Does the AI calculate link feasibility?

**A**: No. Link feasibility is determined by the deterministic `PlanEvaluator` using a
BPSK/AWGN analytical model: Eb/N0, BER, goodput, transmission time, expected cost. These
calculations are performed by `backend/app/telecom/formulas.py` — a single authoritative source.

The AI never receives formulas, performs RF calculations, or outputs link metrics. It ranks
candidates by mission importance; the deterministic evaluator determines what can actually fit.

---

## Q: Why is Granite not in your current benchmark results?

**A**: The 2-trial IAM authentication pilot on 2026-08-26 failed with `GraniteAPIError` —
both trials failed before any model inference was completed. Zero model inferences were produced.
This is correctly documented in `benchmarks/results/run-20260826-110706-530179c2/README.md`.

The official 60-trial core benchmark requires valid IBM Cloud IAM credentials with watsonx.ai
project access. When those credentials are available, the methodology is ready to execute.

We do not claim "Granite improved mission outcomes" because that experiment has not been run.

---

## Q: What is the purpose of Stage 2?

**A**: Stage 2 is a plan *recommendation* layer — separate from Stage 1 (candidate prioritization).

After Stage 1 produces an AI-ordered plan and the four deterministic baselines are evaluated,
Stage 2 receives compact metric summaries for all five plans under OPTION-A through OPTION-E
aliases. The AI recommends which option to approve, with reasoning.

Stage 2 adds value by explaining trade-offs across five evaluated plans in natural language.
Critically, Stage 2 is provenance-blind: the AI cannot tell which option it generated in Stage 1,
preventing self-preference bias.

---

## Q: How realistic is the telecom model?

**A**: GCSI uses a simplified deterministic BPSK/AWGN analytical link model.

It models: Eb/N0, BER (BPSK over AWGN), packet success probability, link goodput, transmission
time, and expected retransmission cost. Protocol efficiency (0.9) is configurable.

It does NOT model: real RF path loss from distance, antenna gain, Doppler shift, multipath fading,
CCSDS protocol stack, or ACK/NACK timing. Distance determines propagation delay only — not SNR.

This is an explicitly-bounded research abstraction for studying prioritization decisions, not a
flight-qualified link-budget tool. All limitations are documented in `docs/telecom_model.md`.

---

## Q: Why isn't propagation included in simulator elapsed time?

**A**: `elapsed_time_s` measures the sum of actual transmission-attempt durations consumed within
the communication window. It is the window consumption time — what determines whether packets fit.

The ~608 s one-way propagation delay is displayed separately in the UI and documented in the
scenario. It is not part of the transmission window budget: packets are transmitted once the
antenna is pointed, not delayed by light-travel time in the scheduler.

This distinction is explicitly documented in `docs/telecom_model.md`.

---

## Q: Is this meant to run onboard spacecraft?

**A**: No. GCSI is a ground-control decision-support tool. All reasoning happens on Earth, before
the communication window opens. The operator approves a plan, and the simulated result shows what
would be transmitted if that plan were executed during the contact window.

---

## Q: Does receiving telemetry resolve the anomaly?

**A**: No — and GCSI explicitly states this. Receiving thermal diagnostic data does not fix the
spacecraft or resolve the anomaly. It changes what the ground team *knows*: the information state
improves, enabling better engineering decisions. The anomaly is physically unresolved until the
ground team acts on the received data.

The ground reception panel shows "information objectives" — not "anomaly resolved."

---

## Q: What exactly is novel here?

**A**: The combination of:

1. **Bounded semantic pre-screening** (1,284 → 50 before AI) — not brute-force LLM over all data
2. **Five-plan objective comparison** — AI plan competes against deterministic baselines under identical evaluation
3. **Provenance-blind Stage-2** (OPTION aliases) — prevents AI self-preference bias
4. **Authoritative fact reconstruction** on approval — client intent cannot tamper with packet facts
5. **Evidence binding from deterministic evaluators** — AI cannot fabricate favorable metrics
6. **Complete mission story** — from triage to transmission to ground reception evidence update

Each of these is individually defensible. Together, they constitute a trust architecture for
human-in-the-loop AI-assisted mission communication planning.

---

*GCSI Judge Questions — Phase 5*
