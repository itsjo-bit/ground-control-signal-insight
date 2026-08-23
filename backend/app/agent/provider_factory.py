"""AI provider factory — selects the appropriate provider based on configuration.

Provider selection logic
------------------------
1. If ``GCSI_GRANITE_API_KEY`` is set → use IBM Granite (GraniteProvider wrapper).
2. Else if ``GCSI_OLLAMA_ENABLED=true`` and Ollama is reachable → use Ollama.
3. Else → use LocalRuleBasedProvider (deterministic, zero dependencies).

The factory never raises; it always returns a valid provider.  If Ollama is
requested but unreachable, the factory falls back to LocalRuleBasedProvider
and logs a warning.

Usage::

    from backend.app.agent.provider_factory import get_provider

    provider = get_provider()
    recommendation = provider.recommend(link_state, mission_state, plans, evals)
"""

from __future__ import annotations

import logging
import os

from .base_provider import BaseAIProvider

logger = logging.getLogger(__name__)


def get_provider() -> BaseAIProvider:
    """Return the best available AI provider for the current environment.

    Selection order (first match wins):
    1. IBM Granite  — ``GCSI_GRANITE_API_KEY`` is set and non-empty.
    2. Ollama       — ``GCSI_OLLAMA_ENABLED=true`` (opt-in; default off).
    3. Local        — deterministic rule-based fallback (always available).

    Returns:
        A :class:`BaseAIProvider` instance ready to call ``recommend()``.
    """
    # ── 1. IBM Granite ────────────────────────────────────────────────────────
    if os.getenv("GCSI_GRANITE_API_KEY", "").strip():
        from .granite_provider import GraniteProvider
        logger.info("AI provider: IBM Granite (GCSI_GRANITE_API_KEY is set)")
        return GraniteProvider()

    # ── 2. Ollama (opt-in) ───────────────────────────────────────────────────
    if os.getenv("GCSI_OLLAMA_ENABLED", "false").lower() in ("true", "1", "yes"):
        from .ollama_provider import OllamaProvider
        provider = OllamaProvider()
        # Quick reachability check — if Ollama is not running, fall through.
        if _ollama_reachable(provider):
            logger.info(
                "AI provider: Ollama (%s) — server is reachable",
                os.getenv("GCSI_OLLAMA_MODEL", "llama3.2"),
            )
            return provider
        logger.warning(
            "GCSI_OLLAMA_ENABLED=true but Ollama server is not reachable; "
            "falling back to LocalRuleBasedProvider."
        )

    # ── 3. Local rule-based (always available) ───────────────────────────────
    from .local_provider import LocalRuleBasedProvider
    logger.info("AI provider: Local rule-based (no API key required)")
    return LocalRuleBasedProvider()


def _ollama_reachable(provider: "OllamaProvider") -> bool:  # type: ignore[name-defined]
    """Return True if the Ollama HTTP server responds to a HEAD request."""
    try:
        import httpx
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(f"{provider._base_url}/api/tags")  # noqa: SLF001
            return resp.status_code == 200
    except Exception:  # noqa: BLE001
        return False
