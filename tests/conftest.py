"""Shared pytest configuration for GCSI test suite."""

import pytest


def pytest_configure(config):
    """Register custom marks and configure default deselection of live tests."""
    config.addinivalue_line(
        "markers",
        "granite: marks tests that require a live IBM Granite API key (select with -m granite)",
    )


def pytest_collection_modifyitems(config, items):
    """Skip 'granite' tests unless the user explicitly selected them with -m granite.

    This prevents live API calls from running in the default test suite even when
    GCSI_GRANITE_API_KEY is visible in the environment (e.g. via .env loaded at
    import time).  To run live tests: pytest -m granite
    """
    if config.option.markexpr and "granite" in config.option.markexpr:
        # User explicitly asked for granite tests — let them run.
        return
    skip_granite = pytest.mark.skip(reason="live API test — run with: pytest -m granite")
    for item in items:
        if item.get_closest_marker("granite"):
            item.add_marker(skip_granite)
