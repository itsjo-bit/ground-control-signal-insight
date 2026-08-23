"""Granite AI agent — structured reasoning over pre-evaluated deterministic facts.

``GraniteAgent.recommend()`` reasons over ``LinkState``, ``MissionState``, and
``EvaluationResult`` objects already computed by the deterministic pipeline.  It
never performs RF/telecom calculations and never invents values.

Design constraints
------------------
- System prompt explicitly forbids the agent from performing calculations.
- Each ``EvidenceItem`` in the response must cite a field that exists in the
  provided ``LinkState`` or ``MissionState`` model.
- If the Granite API is unavailable a typed ``GraniteAPIError`` is raised;
  fabricated output is never returned.
- If any ``EvidenceItem`` cites a non-existent field an
  ``EvidenceHallucinationError`` is raised.
- ``risk_level`` in the AI response must be a valid ``RiskLevel`` enum value;
  invalid values raise ``GraniteResponseError``.

IAM token exchange
------------------
watsonx.ai requires an IBM Cloud IAM *access token* (not the raw API key) in the
Authorization header.  ``GraniteAgent`` automatically exchanges the API key for an
IAM access token at startup and caches it in memory.  The token is refreshed
automatically when it is within ``_IAM_REFRESH_BUFFER_S`` seconds of expiry, or
whenever an explicit 401 response is returned by watsonx.ai.

The IAM exchange endpoint defaults to ``https://iam.cloud.ibm.com/identity/token``
and can be overridden via ``GCSI_GRANITE_IAM_URL`` (rarely needed outside IBM Cloud).

Configuration
-------------
The Granite API endpoint and key are read from environment variables:

    GCSI_GRANITE_API_URL    (default: IBM watsonx.ai us-south endpoint with ?version=)
    GCSI_GRANITE_API_KEY    (required — IBM Cloud IAM API key)
    GCSI_GRANITE_PROJECT_ID (required — watsonx.ai project ID)
    GCSI_GRANITE_MODEL_ID   (default: ibm/granite-4-h-small)
    GCSI_GRANITE_IAM_URL    (default: https://iam.cloud.ibm.com/identity/token)

The watsonx.ai /ml/v1/text/generation endpoint requires an IAM access token
(in the Authorization header) and a project_id (in the JSON body).  A version
query parameter is also required; _ensure_version_param() adds it if absent.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from ..models.candidate_plan import CandidatePlan
from ..models.evaluation_result import EvaluationResult
from ..models.evidence_item import EvidenceItem
from ..models.link_state import LinkState
from ..models.mission_state import MissionState
from ..models.recommendation import AIRecommendation
from ..models.risk_level import RiskLevel

# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class GraniteAPIError(Exception):
    """Raised when the Granite API is unavailable or returns a non-200 response."""


class GraniteResponseError(Exception):
    """Raised when the Granite response is structurally malformed."""


class EvidenceHallucinationError(Exception):
    """Raised when an EvidenceItem cites a field not present in the provided state."""


# ---------------------------------------------------------------------------
# Field name registries — all valid field names the agent may cite
# ---------------------------------------------------------------------------

_LINK_STATE_FIELDS: frozenset[str] = frozenset(LinkState.model_fields.keys())
_MISSION_STATE_FIELDS: frozenset[str] = frozenset(MissionState.model_fields.keys())
_EVALUATION_RESULT_FIELDS: frozenset[str] = frozenset(EvaluationResult.model_fields.keys())

_ALL_CITEABLE_FIELDS: frozenset[str] = (
    _LINK_STATE_FIELDS | _MISSION_STATE_FIELDS | _EVALUATION_RESULT_FIELDS
)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a spacecraft ground-control decision-support agent.
Your role is to recommend a transmission plan for the current communication pass.

RULES (non-negotiable):
1. You may ONLY cite values that appear in the structured data you have been given.
2. You must NOT perform calculations. All metrics have already been computed for you.
3. Each evidence item must reference a real field name from the data.
4. Your recommendation must be one of the provided plan_id values.
5. Respond ONLY with a valid JSON object matching the AIRecommendation schema.

AIRecommendation JSON schema:
{
  "recommended_plan_id": "<string — one of the provided plan_ids>",
  "reasoning": "<string — human-readable explanation>",
  "confidence": <float in [0.0, 1.0]>,
  "risk_score": <float in [0.0, 1.0]>,
  "risk_level": "<LOW|MEDIUM|HIGH|CRITICAL>",
  "evidence": [
    {
      "source": "<link_state|mission_state|evaluation_result>",
      "field": "<exact field name from the source model>",
      "value": <any>,
      "interpretation": "<string>"
    }
  ],
  "alternative_plan_id": "<string or null>"
}"""


# ---------------------------------------------------------------------------
# GraniteAgent
# ---------------------------------------------------------------------------


def _ensure_version_param(url: str, version: str = "2023-05-29") -> str:
    """Return *url* with ``?version=<version>`` appended if not already present.

    Avoids creating duplicate ``version`` parameters when the caller already
    supplies a fully-qualified URL with a version query string.

    Args:
        url:     The endpoint URL, with or without an existing ``version`` param.
        version: The API version date string to inject if absent.

    Returns:
        URL guaranteed to contain exactly one ``version`` query parameter.
    """
    from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    if "version" not in qs:
        qs["version"] = [version]
    new_query = urlencode(qs, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


# ---------------------------------------------------------------------------
# IAM token cache entry
# ---------------------------------------------------------------------------

_IAM_REFRESH_BUFFER_S: float = 60.0  # refresh token this many seconds before expiry


class _IAMTokenCache:
    """Lightweight in-memory cache for a single IBM Cloud IAM access token.

    Tokens are not persisted to disk.  The cache is instance-scoped so each
    ``GraniteAgent`` instance maintains its own token lifecycle.
    """

    __slots__ = ("_access_token", "_expires_at")

    def __init__(self) -> None:
        self._access_token: str = ""
        self._expires_at: float = 0.0  # UNIX timestamp

    def store(self, access_token: str, expires_in: int) -> None:
        """Store a new access token with its TTL."""
        self._access_token = access_token
        # Subtract buffer so we refresh before hard expiry.
        self._expires_at = time.monotonic() + expires_in - _IAM_REFRESH_BUFFER_S

    def get(self) -> str | None:
        """Return the cached token if still valid, else None."""
        if self._access_token and time.monotonic() < self._expires_at:
            return self._access_token
        return None

    def invalidate(self) -> None:
        """Force a refresh on the next call (e.g. after a 401 from watsonx.ai)."""
        self._expires_at = 0.0


class GraniteAgent:
    """IBM Granite-backed recommendation agent.

    Args:
        api_url:    Granite inference endpoint URL.  Defaults to env var
                    ``GCSI_GRANITE_API_URL``.  A ``?version=`` query parameter
                    is injected automatically if the URL does not already contain
                    one.
        api_key:    IBM Cloud IAM API key.  Defaults to env var
                    ``GCSI_GRANITE_API_KEY``.
        project_id: watsonx.ai project ID.  Defaults to env var
                    ``GCSI_GRANITE_PROJECT_ID``.  Required by the
                    /ml/v1/text/generation endpoint.
        model_id:   Model identifier.  Defaults to env var
                    ``GCSI_GRANITE_MODEL_ID`` or ``"ibm/granite-4-h-small"``.
        iam_url:    IBM Cloud IAM token endpoint.  Defaults to env var
                    ``GCSI_GRANITE_IAM_URL`` or the standard IBM Cloud IAM URL.
                    Most users should not need to change this.
        timeout_s:  HTTP request timeout in seconds.
    """

    DEFAULT_API_URL = "https://us-south.ml.cloud.ibm.com/ml/v1/text/generation"
    DEFAULT_MODEL_ID = "ibm/granite-4-h-small"
    DEFAULT_IAM_URL = "https://iam.cloud.ibm.com/identity/token"

    def __init__(
        self,
        api_url: str | None = None,
        api_key: str | None = None,
        project_id: str | None = None,
        model_id: str | None = None,
        iam_url: str | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        raw_url = api_url or os.getenv("GCSI_GRANITE_API_URL", self.DEFAULT_API_URL)
        self._api_url = _ensure_version_param(raw_url)
        self._api_key = api_key or os.getenv("GCSI_GRANITE_API_KEY", "")
        self._project_id = project_id or os.getenv("GCSI_GRANITE_PROJECT_ID", "")
        self._model_id = model_id or os.getenv("GCSI_GRANITE_MODEL_ID", self.DEFAULT_MODEL_ID)
        self._iam_url = iam_url or os.getenv("GCSI_GRANITE_IAM_URL", self.DEFAULT_IAM_URL)
        self._timeout_s = timeout_s
        self._iam_cache = _IAMTokenCache()

    def recommend(
        self,
        link_state: LinkState,
        mission_state: MissionState,
        plans: list[CandidatePlan],
        evaluations: list[EvaluationResult],
    ) -> AIRecommendation:
        """Request a plan recommendation from IBM Granite.

        Builds a structured user message containing all pre-evaluated context,
        calls the Granite REST API, parses and validates the response into an
        ``AIRecommendation``.

        Args:
            link_state:   Current link snapshot.
            mission_state: Current mission snapshot.
            plans:        All candidate plans (baseline + alternatives).
            evaluations:  Deterministic evaluation results for each plan.

        Returns:
            A validated :class:`AIRecommendation`.

        Raises:
            GraniteAPIError:            If the API call fails.
            GraniteResponseError:       If the response is not valid JSON or
                                        violates the expected schema.
            EvidenceHallucinationError: If an EvidenceItem cites an unknown field.
        """
        user_message = self._build_user_message(link_state, mission_state, plans, evaluations)
        raw_response = self._call_api(user_message)
        return self._parse_response(raw_response, plans)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_user_message(
        self,
        link_state: LinkState,
        mission_state: MissionState,
        plans: list[CandidatePlan],
        evaluations: list[EvaluationResult],
    ) -> str:
        """Serialize all pre-computed context into a structured user message."""
        ctx: dict[str, Any] = {
            "link_state": link_state.model_dump(mode="json"),
            "mission_state": mission_state.model_dump(mode="json"),
            "candidate_plans": [p.model_dump(mode="json") for p in plans],
            "evaluations": [e.model_dump(mode="json") for e in evaluations],
        }
        return json.dumps(ctx, indent=2)

    # ------------------------------------------------------------------
    # IAM token acquisition
    # ------------------------------------------------------------------

    def _get_iam_token(self) -> str:
        """Return a valid IAM access token, fetching a fresh one if needed.

        The token is cached in memory and refreshed automatically when it is
        within ``_IAM_REFRESH_BUFFER_S`` seconds of expiry.

        Raises:
            GraniteAPIError: if the IAM token request fails.  The API key is
                             never included in the error message.
        """
        cached = self._iam_cache.get()
        if cached:
            return cached

        try:
            with httpx.Client(timeout=self._timeout_s) as client:
                resp = client.post(
                    self._iam_url,
                    data={
                        "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                        "apikey": self._api_key,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.RequestError as exc:
            raise GraniteAPIError(
                f"IAM token request failed (connection error): {type(exc).__name__}"
            ) from exc

        if resp.status_code != 200:
            raise GraniteAPIError(
                f"IAM token request returned HTTP {resp.status_code}. "
                "Check that GCSI_GRANITE_API_KEY is a valid IBM Cloud API key."
            )

        try:
            token_data = resp.json()
            access_token: str = token_data["access_token"]
            expires_in: int = int(token_data.get("expires_in", 3600))
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            raise GraniteAPIError(
                f"IAM token response was not in the expected format: {type(exc).__name__}"
            ) from exc

        self._iam_cache.store(access_token, expires_in)
        return access_token

    def _call_api(self, user_message: str) -> str:
        """POST to the Granite REST endpoint and return the raw text response.

        Raises:
            GraniteAPIError: on HTTP error, connection failure, or timeout.
        """
        if not self._api_key:
            raise GraniteAPIError(
                "GCSI_GRANITE_API_KEY is not set.  Granite API is unavailable."
            )
        if not self._project_id:
            raise GraniteAPIError(
                "GCSI_GRANITE_PROJECT_ID is not set.  "
                "The watsonx.ai /ml/v1/text/generation endpoint requires a project_id."
            )

        # Exchange API key for an IAM access token (cached; refreshed near expiry).
        access_token = self._get_iam_token()

        payload = {
            "model_id": self._model_id,
            "input": f"<|system|>\n{_SYSTEM_PROMPT}\n<|user|>\n{user_message}\n<|assistant|>\n",
            "parameters": {
                "decoding_method": "greedy",
                "max_new_tokens": 1024,
                "stop_sequences": ["<|user|>"],
            },
            "project_id": self._project_id,
        }

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            with httpx.Client(timeout=self._timeout_s) as client:
                resp = client.post(self._api_url, json=payload, headers=headers)
        except httpx.RequestError as exc:
            raise GraniteAPIError(f"Granite API request failed: {type(exc).__name__}") from exc

        if resp.status_code == 401:
            # Invalidate the cached token so the next call forces a refresh.
            self._iam_cache.invalidate()
            raise GraniteAPIError(
                "Granite API returned HTTP 401: IAM authentication failed. "
                "Verify that GCSI_GRANITE_API_KEY is valid and has watsonx.ai access."
            )

        if resp.status_code == 403:
            raise GraniteAPIError(
                "Granite API returned HTTP 403: access denied. "
                "Verify that GCSI_GRANITE_PROJECT_ID is correct and the API key "
                "has the required permissions on the watsonx.ai project."
            )

        if resp.status_code != 200:
            raise GraniteAPIError(
                f"Granite API returned HTTP {resp.status_code}. "
                "Check the watsonx.ai service status and your project configuration."
            )

        try:
            body = resp.json()
            return body["results"][0]["generated_text"]
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            raise GraniteAPIError(f"Unexpected Granite API response shape: {type(exc).__name__}") from exc

    def _parse_response(
        self, raw: str, plans: list[CandidatePlan]
    ) -> AIRecommendation:
        """Parse and validate the raw Granite response into an AIRecommendation.

        Raises:
            GraniteResponseError:       If the JSON is malformed or required fields
                                        are missing / invalid.
            EvidenceHallucinationError: If an EvidenceItem.field is not a known
                                        field name in the citeable models.
        """
        # Strip leading/trailing whitespace and common markdown fences.
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.rsplit("```", 1)[0].strip()

        try:
            # Use raw_decode so a trailing XML wrapper such as </analysis> that
            # Granite 4 sometimes appends after the closing } is silently ignored.
            # raw_decode still requires a syntactically complete JSON value at the
            # start of the string; truncated or corrupt JSON will still raise.
            data, _ = json.JSONDecoder().raw_decode(text)
        except json.JSONDecodeError as exc:
            raise GraniteResponseError(f"Granite response is not valid JSON: {exc}\nRaw: {raw[:200]}") from exc

        # Validate required top-level fields.
        required = {"recommended_plan_id", "reasoning", "confidence", "risk_score", "risk_level", "evidence"}
        missing = required - data.keys()
        if missing:
            raise GraniteResponseError(f"Granite response missing fields: {missing}")

        # Validate recommended_plan_id is one of the provided plans.
        valid_plan_ids = {p.plan_id for p in plans}
        if data["recommended_plan_id"] not in valid_plan_ids:
            raise GraniteResponseError(
                f"Granite recommended unknown plan_id '{data['recommended_plan_id']}'. "
                f"Valid: {valid_plan_ids}"
            )

        # Validate risk_level is a valid enum value.
        try:
            risk_level = RiskLevel(data["risk_level"])
        except ValueError as exc:
            raise GraniteResponseError(
                f"Granite returned invalid risk_level '{data['risk_level']}'"
            ) from exc

        # Validate and construct EvidenceItems — check for hallucinated field names.
        evidence_items: list[EvidenceItem] = []
        for i, item in enumerate(data.get("evidence", [])):
            field_name = item.get("field", "")
            if field_name not in _ALL_CITEABLE_FIELDS:
                raise EvidenceHallucinationError(
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

        # Validate alternative_plan_id — must be None or a known plan_id.
        alt_plan_id: str | None = data.get("alternative_plan_id")
        if alt_plan_id is not None and alt_plan_id not in valid_plan_ids:
            raise GraniteResponseError(
                f"Granite returned unknown alternative_plan_id '{alt_plan_id}'. "
                f"Valid plan IDs: {valid_plan_ids}"
            )

        # Build packet_actions: transmit all packets in recommended plan order.
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

        # Construct and return AIRecommendation.
        # Catch Pydantic ValidationError (e.g. out-of-range confidence/risk_score)
        # and re-raise as GraniteResponseError so callers receive a well-typed
        # error and the route maps it to HTTP 422 rather than an unhandled 500.
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
        except Exception as exc:  # noqa: BLE001  (catches pydantic.ValidationError)
            raise GraniteResponseError(
                f"Granite response failed AIRecommendation validation: {exc}"
            ) from exc
