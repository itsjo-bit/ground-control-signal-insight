"""AI provider factory — selects the appropriate provider based on configuration.

Provider selection logic
------------------------
Explicit override (``GCSI_AI_PROVIDER``)
  If set to one of ``granite``, ``gemini``, ``ollama``, or ``local``, that
  provider is used unconditionally (subject to its own startup checks).
  An invalid value logs a warning and falls through to automatic selection.

Automatic selection order (first match wins):
1. If ``GCSI_GRANITE_API_KEY`` is set → use IBM Granite (GraniteProvider).
2. Else if ``GCSI_GEMINI_API_KEY`` is set → use Google Gemini (GeminiProvider).
3. Else if ``GCSI_OLLAMA_ENABLED=true`` and Ollama is reachable → use Ollama.
4. Else → use LocalRuleBasedProvider (deterministic, zero dependencies).

IBM Granite is checked before Gemini so that existing Granite configurations
continue to work exactly as before — Granite remains the primary IBM provider.

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

    Checks ``GCSI_AI_PROVIDER`` first for an explicit override, then falls
    through to automatic selection.

    Automatic selection order (first match wins):
    1. IBM Granite  — ``GCSI_GRANITE_API_KEY`` is set and non-empty.
    2. Google Gemini — ``GCSI_GEMINI_API_KEY`` is set and non-empty.
    3. Ollama       — ``GCSI_OLLAMA_ENABLED=true`` (opt-in; default off).
    4. Local        — deterministic rule-based fallback (always available).

    Returns:
        A :class:`BaseAIProvider` instance ready to call ``recommend()``.
    """
    # ── Explicit provider override ────────────────────────────────────────────
    explicit = os.getenv("GCSI_AI_PROVIDER", "").strip().lower()
    if explicit:
        provider = _provider_from_name(explicit)
        if provider is not None:
            return provider
        logger.warning(
            "GCSI_AI_PROVIDER='%s' is not a recognised provider name "
            "(valid: granite, gemini, ollama, local). "
            "Falling back to automatic selection.",
            explicit,
        )

    # ── 1. IBM Granite ────────────────────────────────────────────────────────
    if os.getenv("GCSI_GRANITE_API_KEY", "").strip():
        from .granite_provider import GraniteProvider
        logger.info("AI provider: IBM Granite (GCSI_GRANITE_API_KEY is set)")
        return GraniteProvider()

    # ── 2. Google Gemini ──────────────────────────────────────────────────────
    if os.getenv("GCSI_GEMINI_API_KEY", "").strip():
        from .gemini_provider import GeminiProvider
        logger.info("AI provider: Google Gemini (GCSI_GEMINI_API_KEY is set)")
        return GeminiProvider()

    # ── 3. Ollama (opt-in) ───────────────────────────────────────────────────
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

    # ── 4. Local rule-based (always available) ───────────────────────────────
    from .local_provider import LocalRuleBasedProvider
    logger.info("AI provider: Local rule-based (no API key required)")
    return LocalRuleBasedProvider()


def _provider_from_name(name: str) -> "BaseAIProvider | None":
    """Return a provider instance for *name*, or None if the name is invalid.

    Args:
        name: Lower-cased provider name: one of 'granite', 'gemini', 'ollama',
              or 'local'.

    Returns:
        A :class:`BaseAIProvider` instance, or ``None`` for unknown names.
    """
    if name == "granite":
        from .granite_provider import GraniteProvider
        logger.info("AI provider: IBM Granite (GCSI_AI_PROVIDER=granite)")
        return GraniteProvider()
    if name == "gemini":
        from .gemini_provider import GeminiProvider
        logger.info("AI provider: Google Gemini (GCSI_AI_PROVIDER=gemini)")
        return GeminiProvider()
    if name == "ollama":
        from .ollama_provider import OllamaProvider
        logger.info("AI provider: Ollama (GCSI_AI_PROVIDER=ollama)")
        return OllamaProvider()
    if name == "local":
        from .local_provider import LocalRuleBasedProvider
        logger.info("AI provider: Local rule-based (GCSI_AI_PROVIDER=local)")
        return LocalRuleBasedProvider()
    return None


def _ollama_reachable(provider: "OllamaProvider") -> bool:  # type: ignore[name-defined]
    """Return True if the Ollama HTTP server responds to a HEAD request."""
    try:
        import httpx
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(f"{provider._base_url}/api/tags")  # noqa: SLF001
            return resp.status_code == 200
    except Exception:  # noqa: BLE001
        return False
