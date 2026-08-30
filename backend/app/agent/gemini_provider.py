"""GeminiProvider — Google Gemini AI recommendation provider.

``GeminiProvider`` calls the Google Gemini REST API to generate structured
plan recommendations.  It reuses the same prompt context, system prompt,
response-validation logic, and evidence-field registry as the Granite and
Ollama providers so the route layer treats it identically.

This is an **optional, additive** provider.  The existing IBM Granite provider
is unaffected.  If ``GCSI_GEMINI_API_KEY`` is not set this file is never
imported by the provider factory.

API
---
POST https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent?key=<API_KEY>
{
  "system_instruction": {"parts": [{"text": "<system prompt>"}]},
  "contents": [{"role": "user", "parts": [{"text": "<user message>"}]}],
  "generationConfig": {
    "response_mime_type": "application/json",
    "temperature": 0.0,
    "maxOutputTokens": 4096
  }
}

Configuration
-------------
GCSI_GEMINI_API_KEY   — Google AI API key (required to activate this provider)
GCSI_GEMINI_MODEL     — Model name (default: gemini-2.5-flash-lite-preview-06-17)
GCSI_GEMINI_TIMEOUT   — HTTP timeout in seconds (default: 30.0)

Error handling
--------------
AIProviderError       — API key missing, network failure, non-200 response, timeout
AIResponseError       — Response is malformed, missing required fields, invalid, or
                        truncated due to output-token limit
AIHallucinationError  — Evidence item cites a field not in the known citeable set
"""

from __future__ import annotations

import json
import os
from typing import Any, Sequence

import httpx

from ..models.anomaly_event import AnomalyEvent
from ..models.candidate_plan import CandidatePlan
from ..models.candidate_prioritization import CandidatePrioritization
from ..models.candidate_summary import CandidateSummary
from ..models.evaluation_result import EvaluationResult
from ..models.evidence_item import EvidenceItem
from ..models.link_state import LinkState
from ..models.mission_state import MissionState
from ..models.recommendation import AIRecommendation
from ..models.risk_level import RiskLevel
from .base_provider import AIHallucinationError, AIPrioritizationError, AIProviderError, AIResponseError, BaseAIProvider
from .granite_agent import (
    _ALL_CITEABLE_FIELDS,
    _PRIORITIZATION_SYSTEM_PROMPT,
    _SYSTEM_PROMPT,
)
from .prioritization_helpers import (
    build_prioritization_message as _build_prioritization_message,
    parse_prioritization_response as _parse_prioritization_response,
)
from .stage2_blinding import (
    STAGE2_SYSTEM_PROMPT as _STAGE2_SYSTEM_PROMPT,
    Stage2PlanSummary,
    build_stage2_user_message,
    parse_stage2_response,
    InvalidStage2AliasError,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_MODEL = "gemini-2.5-flash-lite-preview-06-17"
_DEFAULT_TIMEOUT = 30.0
_GEMINI_BASE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models"
)

# Gemini 3.x model name prefix used to detect thinking-capable models.
# Only models whose name starts with "gemini-3" receive thinkingConfig.
_GEMINI_3X_PREFIX = "gemini-3"

# Stage-2 output token budget — generous enough for a complete structured
# response including verbose evidence arrays.
_STAGE2_MAX_OUTPUT_TOKENS = 4096

# Finish-reason values that indicate the model stopped normally.
_NORMAL_FINISH_REASONS = frozenset({"STOP", "END_OF_TURN"})

# Finish-reason values that indicate the output was cut short by token limit.
_TRUNCATION_FINISH_REASONS = frozenset({"MAX_TOKENS", "MAX_OUTPUT_TOKENS"})


def _is_gemini_3x(model: str) -> bool:
    """Return True when *model* identifies a Gemini 3.x series model.

    Only Gemini 3.x models support ``thinkingConfig`` in GenerateContent.
    Sending this parameter to older models (2.x, 1.x) may trigger a 400 error.
    """
    return model.lower().startswith(_GEMINI_3X_PREFIX)


def _build_stage2_response_schema(option_aliases: list[str]) -> dict:
    """Build the Gemini GenerateContent responseSchema for a Stage-2 request.

    The schema enforces:
    * Root is an OBJECT (not an array).
    * ``recommended_option_id`` is a STRING constrained to the current opaque
      OPTION aliases — no real plan IDs are enumerated.
    * Required fields: recommended_option_id, reasoning, confidence, evidence.
    * ``evidence`` items require source, field, interpretation.
    * ``alternative_option_id`` is optional and aliased to the same enum.

    Args:
        option_aliases: The list of current OPTION aliases (e.g. ["OPTION-A",
                        "OPTION-B", ...]).  These are the ONLY values the model
                        may return in recommended_option_id / alternative_option_id.

    Returns:
        A dict describing the Gemini GenerateContent responseSchema.
    """
    option_id_schema: dict = {
        "type": "STRING",
        "enum": option_aliases,
    }
    return {
        "type": "OBJECT",
        "properties": {
            "recommended_option_id": option_id_schema,
            "reasoning": {"type": "STRING"},
            "confidence": {
                "type": "NUMBER",
            },
            "evidence": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "option_id": {"type": "STRING"},
                        "source": {"type": "STRING"},
                        "field": {"type": "STRING"},
                        "interpretation": {"type": "STRING"},
                    },
                    "required": ["source", "field", "interpretation"],
                },
            },
            "alternative_option_id": option_id_schema,
        },
        "required": [
            "recommended_option_id",
            "reasoning",
            "confidence",
            "evidence",
        ],
    }


def _extract_text_from_candidate(body: dict) -> str:
    """Extract the text content from a Gemini GenerateContent response body.

    Also inspects the candidate's ``finishReason`` when present.  If the
    response was terminated due to hitting the output-token limit, raises
    ``AIResponseError`` with a diagnostic message before the caller attempts
    to parse truncated content as valid JSON.

    Args:
        body: Parsed JSON body of the Gemini API response.

    Returns:
        The raw text string from the first candidate.

    Raises:
        AIProviderError: If the response body has an unexpected shape.
        AIResponseError: If the candidate's finishReason indicates truncation.
    """
    try:
        candidate = body["candidates"][0]
    except (KeyError, IndexError) as exc:
        raise AIProviderError(
            f"Unexpected Gemini API response shape: {type(exc).__name__} — {str(exc)[:200]}"
        ) from exc

    # Inspect finishReason when present — truncation must be diagnosed before
    # attempting to parse the (likely incomplete) text as JSON.
    finish_reason: str | None = candidate.get("finishReason")
    if finish_reason is not None:
        if finish_reason in _TRUNCATION_FINISH_REASONS:
            raise AIResponseError(
                f"Gemini model output was truncated due to output-token limit "
                f"(finishReason={finish_reason!r}).  "
                "Increase maxOutputTokens or simplify the prompt."
            )
        # Unknown finish reasons (e.g. SAFETY, RECITATION) are allowed to fall
        # through; the subsequent JSON parse will surface any content issues.

    try:
        text: str = candidate["content"]["parts"][0]["text"]
        return text
    except (KeyError, IndexError, TypeError) as exc:
        raise AIProviderError(
            f"Unexpected Gemini API response shape: {type(exc).__name__} — {str(exc)[:200]}"
        ) from exc


class GeminiProvider(BaseAIProvider):
    """Google Gemini recommendation provider.

    Sends pre-computed deterministic facts (``LinkState``, ``MissionState``,
    candidate plans, evaluations) to the Gemini API and parses the structured
    JSON response into an ``AIRecommendation``.

    The provider never performs telecom calculations.  All metrics are
    pre-computed by the deterministic pipeline before this provider is invoked.

    Args:
        api_key:   Google AI API key.  Defaults to ``GCSI_GEMINI_API_KEY``.
        model:     Model identifier.  Defaults to ``GCSI_GEMINI_MODEL`` or
                   ``gemini-2.5-flash-lite-preview-06-17``.
        timeout_s: HTTP timeout in seconds.  Defaults to ``GCSI_GEMINI_TIMEOUT``
                   or ``30.0``.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.getenv("GCSI_GEMINI_API_KEY", "")
        self._model = model or os.getenv("GCSI_GEMINI_MODEL", _DEFAULT_MODEL)
        self._timeout_s = float(
            timeout_s if timeout_s is not None
            else os.getenv("GCSI_GEMINI_TIMEOUT", str(_DEFAULT_TIMEOUT))
        )

    @property
    def provider_name(self) -> str:
        return "Gemini"

    def recommend(
        self,
        link_state: LinkState,
        mission_state: MissionState,
        plans: list[CandidatePlan],
        evaluations: list[EvaluationResult],
        *,
        anomalies: list[AnomalyEvent] | None = None,
    ) -> AIRecommendation:
        """Generate a plan recommendation using the Gemini API.

        Builds a structured user message from the pre-computed facts, calls
        the Gemini REST API, then parses and validates the structured JSON
        response into an ``AIRecommendation``.

        Args:
            link_state:    Current link snapshot.
            mission_state: Current mission snapshot.
            plans:         All candidate plans (baseline + alternatives).
            evaluations:   Deterministic evaluation results for each plan.

        Returns:
            A validated :class:`AIRecommendation`.

        Raises:
            AIProviderError:      If the API key is missing, the API is
                                  unreachable, authentication fails, or a
                                  non-200 response is returned.
            AIResponseError:      If the Gemini response is malformed or fails
                                  schema validation.
            AIHallucinationError: If evidence cites a non-existent field.
        """
        ctx: dict[str, Any] = {
            "link_state": link_state.model_dump(mode="json"),
            "mission_state": mission_state.model_dump(mode="json"),
            "candidate_plans": [p.model_dump(mode="json") for p in plans],
            "evaluations": [e.model_dump(mode="json") for e in evaluations],
        }
        if anomalies:
            ctx["anomalies"] = [a.model_dump(mode="json") for a in anomalies]
        user_message = json.dumps(ctx, indent=2)
        raw = self._call_api(user_message)
        return self._parse_response(raw, plans, evaluations)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _call_api(self, user_message: str) -> str:
        """POST to the Gemini generateContent endpoint and return raw text.

        Raises:
            AIProviderError: if the API key is absent, the network fails,
                             authentication fails, or a non-200 response is
                             returned.
            AIResponseError: if the candidate finishReason indicates truncation.
        """
        if not self._api_key:
            raise AIProviderError(
                "GCSI_GEMINI_API_KEY is not set.  Gemini API is unavailable."
            )

        url = f"{_GEMINI_BASE_URL}/{self._model}:generateContent"
        params = {"key": self._api_key}

        payload = {
            "system_instruction": {
                "parts": [{"text": _SYSTEM_PROMPT}]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_message}],
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.0,
                "maxOutputTokens": 4096,
            },
        }

        try:
            with httpx.Client(timeout=self._timeout_s) as client:
                resp = client.post(url, params=params, json=payload)
        except httpx.TimeoutException as exc:
            raise AIProviderError(
                f"Gemini API request timed out after {self._timeout_s}s"
            ) from exc
        except httpx.RequestError as exc:
            raise AIProviderError(
                f"Gemini API request failed (connection error): {type(exc).__name__}"
            ) from exc

        if resp.status_code == 400:
            raise AIProviderError(
                f"Gemini API returned HTTP 400 (bad request): {resp.text[:500]}"
            )
        if resp.status_code in (401, 403):
            raise AIProviderError(
                f"Gemini API returned HTTP {resp.status_code}: authentication / "
                "authorisation failed.  Verify that GCSI_GEMINI_API_KEY is valid."
            )
        if resp.status_code != 200:
            raise AIProviderError(
                f"Gemini API returned HTTP {resp.status_code}: {resp.text[:500]}"
            )

        try:
            body = resp.json()
            return _extract_text_from_candidate(body)
        except (json.JSONDecodeError, AIProviderError) as exc:
            raise AIProviderError(
                f"Unexpected Gemini API response shape: {type(exc).__name__} — "
                f"{str(exc)[:200]}"
            ) from exc

    def _parse_response(
        self,
        raw: str,
        plans: list[CandidatePlan],
        evaluations: list[EvaluationResult],
    ) -> AIRecommendation:
        """Parse and validate the raw Gemini text into an AIRecommendation.

        Raises:
            AIResponseError:      If the JSON is malformed, required fields are
                                  missing/invalid, or plan_id is unknown.
            AIHallucinationError: If an EvidenceItem.field is not a known
                                  citeable field name.
        """
        text = raw.strip()
        # Strip markdown fences if present (some models add them even when JSON
        # mime type is requested).
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.rsplit("```", 1)[0].strip()

        try:
            # raw_decode tolerates trailing non-JSON content (e.g. stray text).
            data, _ = json.JSONDecoder().raw_decode(text)
        except json.JSONDecodeError as exc:
            raise AIResponseError(
                f"Gemini response is not valid JSON: {exc}\nRaw: {raw[:200]}"
            ) from exc

        # ── Validate required fields ──────────────────────────────────────────
        required = {
            "recommended_plan_id", "reasoning", "confidence",
            "risk_score", "risk_level", "evidence",
        }
        missing = required - data.keys()
        if missing:
            raise AIResponseError(f"Gemini response missing fields: {missing}")

        # ── Validate recommended_plan_id ──────────────────────────────────────
        valid_plan_ids = {p.plan_id for p in plans}
        if data["recommended_plan_id"] not in valid_plan_ids:
            raise AIResponseError(
                f"Gemini recommended unknown plan_id "
                f"'{data['recommended_plan_id']}'. Valid: {valid_plan_ids}"
            )

        # ── Bind risk_score / risk_level from the authoritative EvaluationResult
        # (Gemini's self-reported values are discarded — the evaluator is the
        # sole authority for risk metrics, exactly as in GraniteAgent.)
        recommended_eval = next(
            (e for e in evaluations if e.plan_id == data["recommended_plan_id"]),
            None,
        )
        if recommended_eval is None:
            raise AIResponseError(
                f"No EvaluationResult found for recommended plan "
                f"'{data['recommended_plan_id']}'. Cannot bind authoritative risk values."
            )

        # ── Validate and construct EvidenceItems ──────────────────────────────
        evidence_items: list[EvidenceItem] = []
        for i, item in enumerate(data.get("evidence", [])):
            field_name = item.get("field", "")
            if field_name not in _ALL_CITEABLE_FIELDS:
                raise AIHallucinationError(
                    f"EvidenceItem[{i}] cites unknown field '{field_name}'. "
                    f"Citeable fields: {sorted(_ALL_CITEABLE_FIELDS)}"
                )
            evidence_items.append(
                EvidenceItem(
                    source=item.get("source", "unknown"),
                    field=field_name,
                    value=item.get("value"),
                    interpretation=item.get("interpretation", ""),
                )
            )

        # ── Validate alternative_plan_id ──────────────────────────────────────
        alt_plan_id: str | None = data.get("alternative_plan_id")
        if alt_plan_id is not None and alt_plan_id not in valid_plan_ids:
            raise AIResponseError(
                f"Gemini returned unknown alternative_plan_id '{alt_plan_id}'. "
                f"Valid plan IDs: {valid_plan_ids}"
            )

        # ── Build packet_actions ──────────────────────────────────────────────
        recommended_plan = next(
            (p for p in plans if p.plan_id == data["recommended_plan_id"]), None
        )
        packet_actions: list[dict] = []
        if recommended_plan is not None:
            for rank, pkt in enumerate(recommended_plan.packets, start=1):
                packet_actions.append({
                    "packet_id": pkt.packet_id,
                    "action": "transmit",
                    "rank": rank,
                })

        # ── Construct AIRecommendation ────────────────────────────────────────
        # risk_score and risk_level come from the authoritative EvaluationResult.
        # confidence comes from Gemini (validated by Pydantic's ge/le constraints).
        try:
            return AIRecommendation(
                recommended_plan_id=data["recommended_plan_id"],
                packet_actions=packet_actions,
                reasoning=data["reasoning"],
                confidence=float(data["confidence"]),
                risk_score=recommended_eval.risk_score,
                risk_level=recommended_eval.risk_level,
                evidence=evidence_items,
                alternative_plan_id=alt_plan_id,
            )
        except Exception as exc:  # noqa: BLE001  (catches pydantic.ValidationError)
            raise AIResponseError(
                f"Gemini response failed AIRecommendation validation: {exc}"
            ) from exc

    def recommend_from_summaries(
        self,
        summaries: list[Stage2PlanSummary],
        link_state: LinkState,
        mission_state: MissionState,
        anomalies: list[AnomalyEvent] | None = None,
    ) -> AIRecommendation:
        """Generate a Stage-2 recommendation using compact provenance-blind summaries.

        Sends the compact option summaries to Gemini using the Stage-2 system prompt.
        The model returns an opaque option alias (OPTION-X), not a real plan ID.

        Stage-2 uses:
        - maxOutputTokens = 4096 (increased from 1024 to prevent truncation)
        - response_mime_type = application/json
        - temperature = 0.0

        Raises:
            AIProviderError:  If the API key is missing or the API fails.
            AIResponseError:  If the response fails validation or is truncated.
        """
        if not self._api_key:
            raise AIProviderError(
                "GCSI_GEMINI_API_KEY is not set.  Gemini API is unavailable."
            )
        alias_map = {s.option_id: s.option_id for s in summaries}
        user_message = build_stage2_user_message(summaries, link_state, mission_state, anomalies)

        url = f"{_GEMINI_BASE_URL}/{self._model}:generateContent"
        params = {"key": self._api_key}

        payload = {
            "system_instruction": {
                "parts": [{"text": _STAGE2_SYSTEM_PROMPT}]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_message}],
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.0,
                "maxOutputTokens": _STAGE2_MAX_OUTPUT_TOKENS,
            },
        }
        try:
            with httpx.Client(timeout=self._timeout_s) as client:
                resp = client.post(url, params=params, json=payload)
        except httpx.TimeoutException as exc:
            raise AIProviderError(
                f"Gemini Stage-2 API request timed out after {self._timeout_s}s"
            ) from exc
        except httpx.RequestError as exc:
            raise AIProviderError(
                f"Gemini Stage-2 API request failed: {type(exc).__name__}"
            ) from exc
        if resp.status_code == 400:
            raise AIProviderError(
                f"Gemini Stage-2 API returned HTTP 400 (bad request): {resp.text[:500]}"
            )
        if resp.status_code in (401, 403):
            raise AIProviderError(
                f"Gemini Stage-2 API returned HTTP {resp.status_code}: authentication / "
                "authorisation failed.  Verify that GCSI_GEMINI_API_KEY is valid."
            )
        if resp.status_code != 200:
            raise AIProviderError(
                f"Gemini Stage-2 API returned HTTP {resp.status_code}: {resp.text[:500]}"
            )

        try:
            body = resp.json()
            raw = _extract_text_from_candidate(body)
        except AIResponseError:
            # Re-raise truncation errors from _extract_text_from_candidate directly.
            raise
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            raise AIProviderError(f"Unexpected Gemini Stage-2 response shape: {exc}") from exc

        try:
            rec_alias, reasoning, confidence, evidence_dicts, alt_alias = parse_stage2_response(
                raw, alias_map, summaries=summaries
            )
        except InvalidStage2AliasError as exc:
            raise AIResponseError(str(exc)) from exc
        except ValueError as exc:
            raise AIResponseError(str(exc)) from exc

        # Preserve option_id from the parser (OPTION alias at this point).
        # Routes_agent maps it to the real plan identity after binding.
        evidence_items = [
            EvidenceItem(
                option_id=item.get("option_id"),     # preserved OPTION alias
                source=item.get("source", "candidate_option"),
                field=item["field"],
                value=None,  # backend will rebind from authoritative data
                interpretation=item.get("interpretation", ""),
            )
            for item in evidence_dicts
        ]
        try:
            return AIRecommendation(
                recommended_plan_id=rec_alias,
                packet_actions=[],
                reasoning=reasoning,
                confidence=confidence,
                risk_score=0.0,
                risk_level=RiskLevel.LOW,  # placeholder; rebound by routes_agent
                evidence=evidence_items,
                alternative_plan_id=alt_alias,
            )
        except Exception as exc:  # noqa: BLE001
            raise AIResponseError(f"Gemini Stage-2 response failed validation: {exc}") from exc

    def prioritize_candidates(
        self,
        candidates: Sequence[CandidateSummary],
        link_state: LinkState,
        mission_state: MissionState,
        anomalies: Sequence[AnomalyEvent] | None = None,
        *,
        distance_km: float | None = None,
    ) -> CandidatePrioritization:
        """Rank candidates using the Gemini API with the prioritization prompt.

        Raises:
            AIProviderError:       If the API key is missing or the API fails.
            AIPrioritizationError: If the response fails validation.
        """
        if not self._api_key:
            raise AIProviderError(
                "GCSI_GEMINI_API_KEY is not set.  Gemini API is unavailable."
            )

        user_message = _build_prioritization_message(
            candidates, link_state, mission_state, anomalies,
            distance_km=distance_km,
        )

        url = f"{_GEMINI_BASE_URL}/{self._model}:generateContent"
        params = {"key": self._api_key}
        payload = {
            "system_instruction": {
                "parts": [{"text": _PRIORITIZATION_SYSTEM_PROMPT}]
            },
            "contents": [{"role": "user", "parts": [{"text": user_message}]}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.0,
                "maxOutputTokens": 8192,
            },
        }
        try:
            with httpx.Client(timeout=self._timeout_s) as client:
                resp = client.post(url, params=params, json=payload)
        except Exception as exc:  # noqa: BLE001
            raise AIProviderError(f"Gemini prioritization API request failed: {exc}") from exc

        if resp.status_code != 200:
            raise AIProviderError(f"Gemini prioritization API returned HTTP {resp.status_code}.")

        try:
            body = resp.json()
            raw = body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise AIProviderError(f"Unexpected Gemini prioritization response shape: {exc}") from exc

        valid_ids = {cs.product_id for cs in candidates}
        try:
            return _parse_prioritization_response(raw, valid_ids, candidates)
        except Exception as exc:  # noqa: BLE001
            from .granite_agent import GraniteResponseError
            if isinstance(exc, GraniteResponseError):
                raise AIPrioritizationError(str(exc)) from exc
            raise AIPrioritizationError(f"Gemini prioritization response validation failed: {exc}") from exc
