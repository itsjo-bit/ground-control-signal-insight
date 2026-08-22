"""Shared pytest configuration for GCSI test suite."""

import pytest


def pytest_configure(config):
    """Register custom marks to avoid unknown-mark warnings."""
    config.addinivalue_line(
        "markers",
        "granite: marks tests that require a live IBM Granite API key (skip without key)",
    )
