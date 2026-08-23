"""Ollama local LLM provider — uses the Ollama HTTP REST API.

``OllamaProvider`` calls a locally-running Ollama server to generate
recommendations.  It reuses the same prompt context as ``GraniteAgent``,
the same response parsing/validation logic, and raises the same typed
exceptions so the route layer treats it identically.

Ollama API
----------
POST http://localhost:11434/api/generate
{
  "model": "<model_name>",
  "prompt": "<system + user context>",
  "stream": false
}
Response: { "response": "<generated text>", ... }

Configuration
-------------
GCSI_OLLAMA_URL      — base URL (default: http://localhost:11434)
GCSI_OLLAMA_MODEL    — model name (default: llama3.2)
GCSI_OLLAMA_TIMEOUT  — HTTP timeout in seconds (default: 60.0)

Availability check
------------------
If the Ollama server is not reachable, ``AIProviderError`` is raised so the
route can fall through to the next provider in the chain.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from ..models.candidate_plan import CandidatePlan
from ..models.evaluation_result import EvaluationResult
from ..models.evidence_item import EvidenceItem
from ..models.link_state import LinkState
from ..models.mission_state import MissionState
from ..models.recommendation import AIRecommendation
from ..models.risk_level import RiskLevel
from .base_provider import AIHallucinationError, AIProviderError, AIResponseError, BaseAIProvider
from .granite_agent import (
    _ALL_CITEABLE_FIELDS,  # reuse the same field registry
    _SYSTEM_PROMPT,  # reuse the same system prompt
)

# ---------------------------------------------------------------------------
# OllamaProvider
# ---------------------------------------------------------------------------

_DEFAULT_URL = "http://localhost:11434"
_DEFAULT_MODEL = "llama3.2"
_DEFAULT_TIMEOUT = 60.0


class OllamaProvider(BaseAIProvider):
    """Ollama-backed recommendation provider.

    Uses the Ollama HTTP REST API to call a locally-running LLM.
    Falls back gracefully: raises ``AIProviderError`` if the server is
    unreachable so the route can select the next available provider.

    Args:
        base_url:  Ollama server base URL.  Defaults to env var
                   ``GCSI_OLLAMA_URL`` or ``http://localhost:11434``.
        model:     Model name.  Defaults to env var ``GCSI_OLLAMA_MODEL``
                   or ``llama3.2``.
        timeout_s: HTTP request timeout in seconds.  Defaults to env var
                   ``GCSI_OLLAMA_TIMEOUT`` or ``60.0``.
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self._base_url = (base_url or os.getenv("GCSI_OLLAMA_URL", _DEFAULT_URL)).rstrip("/")
        self._model = model or os.getenv("GCSI_OLLAMA_MODEL", _DEFAULT_MODEL)
        self._timeout_s = float(timeout_s or os.getenv("GCSI_OLLAMA_TIMEOUT", str(_DEFAULT_TIMEOUT)))

    @property
    def provider_name(self) -> str:
        return f"Ollama ({self._model})"

    def recommend(
        self,
        link_state: LinkState,
        mission_state: MissionState,
        plans: list[CandidatePlan],
        evaluations: list[EvaluationResult],
    ) -> AIRecommendation:
        """Generate a recommendation using Ollama.

        Raises:
            AIProviderError:      If the Ollama server is unreachable or returns
                                  a non-200 response.
            AIResponseError:      If the LLM output cannot be parsed/validated.
            AIHallucinationError: If evidence cites a non-existent field.
        """
        ctx: dict[str, Any] = {
            "link_state": link_state.model_dump(mode="json"),
            "mission_state": mission_state.model_dump(mode="json"),
            "candidate_plans": [p.model_dump(mode="json") for p in plans],
            "evaluations": [e.model_dump(mode="json") for e in evaluations],
        }
        user_message = json.dumps(ctx, indent=2)
        full_prompt = f"<|system|>\n{_SYSTEM_PROMPT}\n<|user|>\n{user_message}\n<|assistant|>\n"

        raw = self._call_api(full_prompt)
        return self._parse_response(raw, plans)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _call_api(self, prompt: str) -> str:
        """POST to the Ollama generate endpoint and return the raw text.

        Raises:
            AIProviderError: on connection failure, timeout, or non-200 status.
        """
        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
        }
        try:
            with httpx.Client(timeout=self._timeout_s) as client:
                resp = client.post(f"{self._base_url}/api/generate", json=payload)
        except httpx.RequestError as exc:
            raise AIProviderError(
                f"Ollama server at '{self._base_url}' is not reachable: {exc}"
            ) from exc

        if resp.status_code != 200:
            raise AIProviderError(
                f"Ollama API returned HTTP {resp.status_code}: {resp.text[:500]}"
            )

        try:
            body = resp.json()
            return body["response"]
        except (KeyError, json.JSONDecodeError) as exc:
            raise AIProviderError(
                f"Unexpected Ollama API response shape: {exc}"
            ) from exc

    def _parse_response(self, raw: str, plans: list[CandidatePlan]) -> AIRecommendation:
        """Parse and validate the raw Ollama response into an AIRecommendation.

        Reuses the same validation logic as GraniteAgent._parse_response.

        Raises:
            AIResponseError:      If the JSON is malformed or required fields
                                  are missing / invalid.
            AIHallucinationError: If an EvidenceItem.field is not a known
                                  field name in the citeable models.
        """
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.rsplit("```", 1)[0].strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AIResponseError(
                f"Ollama response is not valid JSON: {exc}\nRaw: {raw[:200]}"
            ) from exc

        required = {
            "recommended_plan_id", "reasoning", "confidence",
            "risk_score", "risk_level", "evidence",
        }
        missing = required - data.keys()
        if missing:
            raise AIResponseError(f"Ollama response missing fields: {missing}")

        valid_plan_ids = {p.plan_id for p in plans}
        if data["recommended_plan_id"] not in valid_plan_ids:
            raise AIResponseError(
                f"Ollama recommended unknown plan_id '{data['recommended_plan_id']}'. "
                f"Valid: {valid_plan_ids}"
            )

        try:
            risk_level = RiskLevel(data["risk_level"])
        except ValueError as exc:
            raise AIResponseError(
                f"Ollama returned invalid risk_level '{data['risk_level']}'"
            ) from exc

        evidence_items: list[EvidenceItem] = []
        for i, item in enumerate(data.get("evidence", [])):
            field_name = item.get("field", "")
            if field_name not in _ALL_CITEABLE_FIELDS:
                raise AIHallucinationError(
                    f"EvidenceItem[{i}] cites unknown field '{field_name}'. "
                    f"Citeable: {sorted(_ALL_CITEABLE_FIELDS)}"
                )
            evidence_items.append(
                EvidenceItem(
                    source=item.get("source", "unknown"),
                    field=field_name,
                    value=item.get("value"),
                    interpretation=item.get("interpretation", ""),
                )
            )

        alt_plan_id: str | None = data.get("alternative_plan_id")
        if alt_plan_id is not None and alt_plan_id not in valid_plan_ids:
            raise AIResponseError(
                f"Ollama returned unknown alternative_plan_id '{alt_plan_id}'. "
                f"Valid plan IDs: {valid_plan_ids}"
            )

        recommended_plan = next(
            (p for p in plans if p.plan_id == data["recommended_plan_id"]), None
        )
        packet_actions = []
        if recommended_plan is not None:
            for rank, pkt in enumerate(recommended_plan.packets, start=1):
                packet_actions.append({
                    "packet_id": pkt.packet_id,
                    "action": "transmit",
                    "rank": rank,
                })

        try:
            return AIRecommendation(
                recommended_plan_id=data["recommended_plan_id"],
                packet_actions=packet_actions,
                reasoning=data["reasoning"],
                confidence=float(data["confidence"]),
                risk_score=float(data["risk_score"]),
                risk_level=risk_level,
                evidence=evidence_items,
                alternative_plan_id=alt_plan_id,
            )
        except Exception as exc:  # noqa: BLE001
            raise AIResponseError(
                f"Ollama response failed AIRecommendation validation: {exc}"
            ) from exc
