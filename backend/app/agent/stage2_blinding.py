"""Stage-2 provenance blinding for external AI providers.

This module hides the real plan identity from external LLMs during Stage-2
plan recommendation.  External providers receive **opaque option aliases**
(``OPTION-A``, ``OPTION-B``, ...) rather than real plan identifiers such as
``"ai-prioritized"`` or ``"baseline"``.

Motivation
----------
External LLMs can recognise their own output from labels like
``"ai-prioritized"`` or strategy names.  This creates:

* **self-preference bias** — the model may favour the plan it or its
  architecture produced.
* **provenance bias** — "AI-prioritized" may sound more sophisticated and
  receive higher recommendation rates regardless of actual metrics.
* **automation bias** — the model may defer to the AI plan without reasoning
  about comparative metrics.

By replacing real plan identities with neutral aliases, external Stage-2
reasoning is forced to rely only on objective evaluation metrics.

Deterministic mapping
----------------------
Alias assignment uses a SHA-256-based stable hash::

    alias_rank = SHA-256(scenario_id + plan_id)[:8]  (hex, compare as int)

This ensures:
* same scenario + same plans → same aliases on every run
* AI plan is not guaranteed to receive any particular alias
* aliases reveal no information about provenance

The mapping is maintained purely inside trusted backend logic and is never
exposed to the external provider.

Compact plan summary
--------------------
Instead of serialising the full 150-packet plan (which would multiply by 5
plans = 750 packet structs), each plan is summarised into a
:class:`Stage2PlanSummary` containing only evaluation metrics.

The external LLM receives only the alias → summary mapping.  It selects an
alias.  The backend translates the alias back to the real plan.

Invalid-alias rejection
-----------------------
If the external provider returns an alias not in the current mapping,
:func:`map_alias_to_plan_id` raises :class:`InvalidStage2AliasError`.

Provenance-leak detection
--------------------------
:func:`assert_no_provenance_leak` checks the serialized context for known
provenance strings.  Use it in tests to verify the blinding works.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional, Sequence

from pydantic import BaseModel, Field

from ..models.anomaly_event import AnomalyEvent
from ..models.candidate_plan import CandidatePlan
from ..models.evaluation_result import EvaluationResult
from ..models.link_state import LinkState
from ..models.mission_state import MissionState

# Strings that must NEVER appear in a provenance-blind Stage-2 context.
_FORBIDDEN_PROVENANCE_STRINGS = frozenset([
    "ai-prioritized",
    "ai_prioritized",
    "baseline",
    "deadline-first",
    "deadline_first",
    "mission-critical-first",
    "mission_critical_first",
    "value-per-cost",
    "value_per_cost",
    "generated_by",
    "plan_type",
    "stage1_provider",
    "fallback_used",
    "ai_semantic",
    "semantic-rule-based",
    "semantic_rule_based",
    "strategy",
    "metadata",
])


# ---------------------------------------------------------------------------
# Stage-2 system prompt (external providers)
# ---------------------------------------------------------------------------

STAGE2_SYSTEM_PROMPT = """You are a spacecraft ground-control trade-off analysis agent.
Your task is to recommend one transmission plan option for the current communication pass.

You will receive:
- mission_context: current mission phase, event, risk level
- link_context: remaining window, bit error rate, goodput
- active_anomalies: anomalies currently affecting the spacecraft
- candidate_options: a set of opaque options (OPTION-A, OPTION-B, ...) with objective metrics

RULES (non-negotiable):
1. Select exactly ONE option from the provided candidate_options keys (e.g. "OPTION-C").
   Do NOT return a real plan name such as "baseline", "ai-prioritized", or any other identifier.
2. You may ONLY cite fields that appear in the provided data.
3. You must NOT perform calculations or invent metric values.
4. Respond ONLY with a valid JSON object matching the schema below.

METRIC CATEGORIES — consider BOTH for a balanced recommendation:

Telecom / feasibility:
  risk_score                    — overall transmission risk [0,1]; lower is safer
  critical_packets_delivered    — count of critical packets successfully delivered
  deadline_misses               — number of packets missing their deadline
  deadline_miss_rate            — fraction of packets missing deadlines [0,1]
  bandwidth_utilization         — fraction of link bandwidth used [0,1]
  window_pressure               — pressure on remaining window [0,1]
  mission_value                 — weighted delivery value

Mission semantic outcome:
  scientific_value_capture_rate — fraction of total scientific value delivered [0,1]
  required_delivery_rate        — fraction of required products delivered [0,1]
  active_anomaly_delivery_rate  — fraction of anomaly-linked products delivered [0,1]
  high_severity_anomaly_coverage_rate — fraction of high-severity anomalies with coverage [0,1]
  anomaly_weighted_coverage     — severity-weighted anomaly coverage [0,1]
  average_delivered_age_s       — mean age of delivered data (lower = fresher)

DECISION GUIDANCE:
- Select the option whose trade-offs are BEST SUPPORTED by the authoritative metrics
  and current mission context.
- Do NOT assume any option is AI-generated or rule-based. Treat all options equally.
- Do NOT automatically favor the highest scientific value if it compromises mission safety.
- Do NOT automatically favor the lowest risk if it sacrifices required anomaly diagnostics.
- Consider active anomaly severity and coverage when diagnostic data is at stake.
- Higher active_anomaly_delivery_rate matters more when high-severity anomalies are present.
- Your role is advisory trade-off interpretation, not autonomous decision-making.

RESPONSE SCHEMA:
{
  "recommended_option_id": "<OPTION-X — must be an exact key from candidate_options>",
  "reasoning": "<string — human-readable trade-off explanation citing specific metrics>",
  "confidence": <float in [0.0, 1.0]>,
  "evidence": [
    {
      "option_id": "<OPTION-X or null for link/mission context>",
      "source": "<candidate_option|link_state|mission_state>",
      "field": "<exact field name from the metric categories above>",
      "interpretation": "<string explaining why this metric supports your recommendation>"
    }
  ],
  "alternative_option_id": "<OPTION-Y or null>"
}

NOTE: Do NOT include risk_score or risk_level in your response — the backend will
supply authoritative values. Do NOT echo metric values; provide only interpretation."""


class InvalidStage2AliasError(Exception):
    """Raised when an external provider returns an alias not in the mapping."""


# ---------------------------------------------------------------------------
# Compact plan summary sent to Stage-2 LLM
# ---------------------------------------------------------------------------


class Stage2PlanSummary(BaseModel):
    """Compact, provenance-free summary sent to external Stage-2 providers.

    Contains only evaluation metrics — no plan identity, strategy, or
    packet lists.  The ``option_id`` is the opaque alias assigned by the
    blinding layer (e.g. ``"OPTION-A"``).
    """

    option_id: str = Field(description="Opaque alias assigned by the provenance-blinding layer")

    # Physical / telecom metrics (from PlanEvaluator)
    total_packets: int = Field(ge=0)
    deferred_count: int = Field(ge=0)
    risk_score: float = Field(ge=0.0, le=1.0)
    risk_level: str = Field(description="Categorical risk level (LOW/MEDIUM/HIGH/CRITICAL)")
    mission_value: float = Field(ge=0.0)
    critical_packets_delivered: int = Field(ge=0)
    total_critical_packets: int = Field(ge=0)
    deadline_misses: int = Field(ge=0)
    deadline_miss_rate: float = Field(ge=0.0, le=1.0)
    bandwidth_utilization: float = Field(ge=0.0, le=1.0)
    retransmission_overhead: float = Field(ge=0.0)
    window_pressure: float = Field(ge=0.0, le=1.0)

    # Semantic mission outcome metrics (from MissionOutcomeEvaluator) — all optional
    delivery_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    scientific_value_capture_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    required_delivery_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    active_anomaly_delivery_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    high_severity_anomaly_coverage_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    anomaly_weighted_coverage: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    average_delivered_age_s: Optional[float] = Field(default=None, ge=0.0)


# ---------------------------------------------------------------------------
# Blinding layer
# ---------------------------------------------------------------------------


def _stable_alias_rank(scenario_id: str, plan_id: str) -> int:
    """Return a stable integer used to assign alias order.

    Uses SHA-256 to produce a consistent hash across Python processes.
    (Python's built-in ``hash()`` is randomized per-process — not usable here.)
    """
    key = f"{scenario_id}:{plan_id}".encode()
    digest = hashlib.sha256(key).hexdigest()
    return int(digest[:16], 16)  # first 8 bytes (16 hex chars) as int


def build_blind_mapping(
    plans: Sequence[CandidatePlan],
    scenario_id: str = "default",
) -> dict[str, str]:
    """Build a deterministic alias → real_plan_id mapping.

    Args:
        plans:       List of candidate plans to anonymise.
        scenario_id: Scenario identifier used to stabilise alias ordering.
                     Using the same scenario_id with the same plans always
                     produces the same aliases.

    Returns:
        Dict mapping ``"OPTION-A"`` → real ``plan_id``, etc.
        The mapping is ordered by stable hash so the AI plan does not
        always receive a predictable alias.
    """
    if not plans:
        return {}

    # Sort plans by stable hash rank
    sorted_plans = sorted(
        plans,
        key=lambda p: _stable_alias_rank(scenario_id, p.plan_id),
    )

    option_labels = [f"OPTION-{chr(65 + i)}" for i in range(len(sorted_plans))]
    return {label: plan.plan_id for label, plan in zip(option_labels, sorted_plans)}


def map_alias_to_plan_id(alias: str, alias_map: dict[str, str]) -> str:
    """Translate an opaque alias returned by an external provider to the real plan_id.

    Args:
        alias:     The alias returned by the external provider, e.g. ``"OPTION-C"``.
        alias_map: The mapping returned by :func:`build_blind_mapping`.

    Returns:
        The real ``plan_id`` corresponding to the alias.

    Raises:
        InvalidStage2AliasError: If the alias is not in the mapping.  This
            includes the case where the provider leaked a real plan name such as
            ``"ai-prioritized"`` instead of the assigned alias.
    """
    real_id = alias_map.get(alias)
    if real_id is None:
        valid = sorted(alias_map.keys())
        raise InvalidStage2AliasError(
            f"External provider returned invalid option alias '{alias}'. "
            f"Valid aliases: {valid}. "
            "If the provider returned a real plan name, this indicates a provenance leak."
        )
    return real_id


def build_stage2_summaries(
    alias_map: dict[str, str],
    plans: Sequence[CandidatePlan],
    evaluations: Sequence[EvaluationResult],
    mission_outcomes: "Sequence[MissionOutcomeResult] | None" = None,
) -> list[Stage2PlanSummary]:
    """Build compact plan summaries for external Stage-2 providers.

    Args:
        alias_map:        Alias → real plan_id mapping from :func:`build_blind_mapping`.
        plans:            All candidate plans (used to get packet count).
        evaluations:      Deterministic PlanEvaluator results.
        mission_outcomes: Optional MissionOutcomeEvaluator results.  When provided,
                          semantic metrics are included in the summaries.

    Returns:
        List of :class:`Stage2PlanSummary` objects keyed by their alias.
    """
    # Build lookups keyed by real plan_id
    plan_map: dict[str, CandidatePlan] = {p.plan_id: p for p in plans}
    eval_map: dict[str, EvaluationResult] = {e.plan_id: e for e in evaluations}

    # Build mission outcome lookup if provided
    from ..evaluator.mission_outcome_evaluator import MissionOutcomeResult
    outcome_map: dict[str, MissionOutcomeResult] = {}
    if mission_outcomes:
        outcome_map = {mo.plan_id: mo for mo in mission_outcomes}

    summaries: list[Stage2PlanSummary] = []
    for alias, real_plan_id in alias_map.items():
        plan = plan_map.get(real_plan_id)
        ev = eval_map.get(real_plan_id)
        if plan is None or ev is None:
            continue  # should not happen in normal operation

        mo = outcome_map.get(real_plan_id)

        summaries.append(Stage2PlanSummary(
            option_id=alias,
            # Physical metrics
            total_packets=len(plan.packets),
            deferred_count=len(ev.deferred_packets),
            risk_score=ev.risk_score,
            risk_level=ev.risk_level.value,
            mission_value=ev.mission_value,
            critical_packets_delivered=ev.critical_packets_delivered,
            total_critical_packets=ev.total_critical_packets,
            deadline_misses=ev.deadline_misses,
            deadline_miss_rate=ev.deadline_miss_rate,
            bandwidth_utilization=ev.bandwidth_utilization,
            retransmission_overhead=ev.retransmission_overhead,
            window_pressure=ev.window_pressure,
            # Semantic metrics from MissionOutcomeEvaluator (when available)
            delivery_rate=mo.delivery_rate if mo else None,
            scientific_value_capture_rate=mo.scientific_value_capture_rate if mo else None,
            required_delivery_rate=mo.required_delivery_rate if mo else None,
            active_anomaly_delivery_rate=mo.active_anomaly_delivery_rate if mo else None,
            high_severity_anomaly_coverage_rate=mo.high_severity_anomaly_coverage_rate if mo else None,
            anomaly_weighted_coverage=mo.anomaly_weighted_coverage if mo else None,
            average_delivered_age_s=mo.average_delivered_age_s if mo else None,
        ))

    return summaries


def build_blind_context_json(summaries: list[Stage2PlanSummary]) -> str:
    """Serialise Stage-2 summaries as a compact JSON string (no provenance).

    The resulting JSON maps option alias → metrics dict.  It never contains
    plan identifiers, strategy names, or any other provenance information.

    Args:
        summaries: List produced by :func:`build_stage2_summaries`.

    Returns:
        Compact JSON string safe for inclusion in external LLM prompts.
    """
    ctx: dict[str, dict] = {}
    for s in summaries:
        ctx[s.option_id] = {
            k: v
            for k, v in s.model_dump(mode="json").items()
            if k != "option_id" and v is not None
        }
    return json.dumps(ctx, indent=2)


def build_stage2_user_message(
    summaries: list[Stage2PlanSummary],
    link_state: LinkState,
    mission_state: MissionState,
    anomalies: list[AnomalyEvent] | None = None,
) -> str:
    """Build the complete user message sent to an external Stage-2 provider.

    Produces a single JSON string containing:
    - ``mission_context``: compact mission snapshot (no raw scores except what
      the LLM may use for context)
    - ``link_context``: key link parameters
    - ``active_anomalies``: applicable anomalies (id, severity, subsystem)
    - ``candidate_options``: the compact provenance-blind metrics per option

    This is the SOLE source of the external LLM user message for Stage-2.
    Both ``build_stage2_summaries()`` and ``build_blind_context_json()``
    are used internally.

    Args:
        summaries:     Compact summaries from :func:`build_stage2_summaries`.
        link_state:    Current link snapshot.
        mission_state: Current mission snapshot.
        anomalies:     Anomaly events to include (any status; callers should
                       supply applicable anomalies only).

    Returns:
        JSON string for use as the LLM user message.  Contains no provenance.
    """
    ctx: dict = {
        "mission_context": {
            "mission_phase": mission_state.mission_phase,
            "current_event": mission_state.current_event,
            "comm_window_remaining_s": mission_state.comm_window_remaining_s,
            "risk_level": mission_state.risk_level.value,
        },
        "link_context": {
            "remaining_window_s": link_state.remaining_window_s,
            "ber": link_state.ber,
            "link_goodput_bps": link_state.link_goodput_bps,
        },
        "active_anomalies": [
            {
                "anomaly_id": ae.anomaly_id,
                "severity": ae.severity,
                "subsystem": ae.subsystem,
                "status": ae.status,
            }
            for ae in (anomalies or [])
        ],
        "candidate_options": json.loads(build_blind_context_json(summaries)),
    }
    return json.dumps(ctx, indent=2)


# ---------------------------------------------------------------------------
# Stage-2 evidence field registry
# ---------------------------------------------------------------------------

# Fields that Stage-2 evidence may cite from Stage2PlanSummary (candidate_option)
_STAGE2_CANDIDATE_FIELDS: frozenset[str] = frozenset(
    f for f in Stage2PlanSummary.model_fields if f != "option_id"
)

# Fields from LinkState that Stage-2 evidence may cite
# (imported lazily to avoid circular imports)
def _get_stage2_link_fields() -> frozenset[str]:
    from ..models.link_state import LinkState as _LS
    return frozenset(_LS.model_fields.keys())


def _get_stage2_mission_fields() -> frozenset[str]:
    from ..models.mission_state import MissionState as _MS
    return frozenset(_MS.model_fields.keys())


def get_stage2_citeable_fields() -> frozenset[str]:
    """Return the complete set of field names that Stage-2 evidence may cite.

    Includes:
    * All :class:`Stage2PlanSummary` metric fields (except ``option_id``)
    * All :class:`~backend.app.models.link_state.LinkState` fields
    * All :class:`~backend.app.models.mission_state.MissionState` fields

    Returns:
        Frozenset of valid citeable field names for Stage-2 evidence validation.
    """
    return _STAGE2_CANDIDATE_FIELDS | _get_stage2_link_fields() | _get_stage2_mission_fields()


def parse_stage2_response(
    raw: str,
    alias_map: dict[str, str],
) -> tuple[str, str, float, list[dict], str | None]:
    """Parse and validate a raw Stage-2 LLM response.

    Validates:
    * Response is valid JSON
    * ``recommended_option_id`` is a valid current alias
    * ``confidence`` is in [0, 1]
    * ``alternative_option_id`` is null or a valid current alias
    * Evidence field names are in the Stage-2 citeable set

    Args:
        raw:       Raw text from the LLM.
        alias_map: The alias → real_plan_id mapping for this request.

    Returns:
        Tuple of (recommended_option_id, reasoning, confidence, evidence_dicts,
        alternative_option_id).
        Both IDs are validated OPTION aliases (never real plan IDs).

    Raises:
        InvalidStage2AliasError: If ``recommended_option_id`` is not a valid alias.
        ValueError:              If JSON parsing fails or required fields are missing.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0].strip()

    try:
        data, _ = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Stage-2 response is not valid JSON: {exc}\nRaw: {raw[:200]}") from exc

    # Validate required fields
    required = {"recommended_option_id", "reasoning", "confidence"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"Stage-2 response missing fields: {missing}")

    # Validate recommended_option_id is a valid current alias
    rec_alias = str(data["recommended_option_id"])
    if rec_alias not in alias_map:
        raise InvalidStage2AliasError(
            f"Stage-2 provider returned invalid option alias '{rec_alias}'. "
            f"Valid aliases: {sorted(alias_map.keys())}."
        )

    # Validate confidence
    try:
        confidence = float(data["confidence"])
        if not (0.0 <= confidence <= 1.0):
            confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.5

    # Validate alternative option (drop if invalid, don't reject whole response)
    alt_raw = data.get("alternative_option_id")
    alt_alias: str | None = None
    if alt_raw is not None:
        alt_str = str(alt_raw)
        if alt_str in alias_map and alt_str != rec_alias:
            alt_alias = alt_str
        # Invalid alternative is silently dropped (per policy: drop alternative only)

    # Validate and filter evidence
    citeable = get_stage2_citeable_fields()
    evidence_out: list[dict] = []
    for item in data.get("evidence", []):
        field_name = item.get("field", "")
        if field_name not in citeable:
            continue  # silently drop unknown fields
        evidence_out.append({
            "option_id": item.get("option_id"),
            "source": item.get("source", "candidate_option"),
            "field": field_name,
            "interpretation": item.get("interpretation", ""),
        })

    reasoning = str(data.get("reasoning", ""))
    return rec_alias, reasoning, confidence, evidence_out, alt_alias


def assert_no_provenance_leak(context_json: str) -> None:
    """Assert that a Stage-2 context JSON contains no known provenance strings.

    Raises:
        AssertionError: if any forbidden provenance string is found.

    Args:
        context_json: The serialized Stage-2 context to inspect.
    """
    lower = context_json.lower()
    found = [s for s in _FORBIDDEN_PROVENANCE_STRINGS if s.lower() in lower]
    if found:
        raise AssertionError(
            f"Stage-2 context contains forbidden provenance strings: {found}. "
            "This would allow an external LLM to identify plan provenance."
        )
