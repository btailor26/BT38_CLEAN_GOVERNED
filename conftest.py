"""Global pytest safety gate: tests must never run against BT38 production."""

from __future__ import annotations

import os

import pytest


PRODUCTION_DATABASE_MARKERS = (
    "ep-royal-fire-ai8c32qw",
)


def pytest_sessionstart(session):
    """Stop before test collection can import the application or write data."""

    app_env = str(os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "").upper()
    configured_urls = [
        str(os.getenv(name) or "")
        for name in ("DATABASE_URL", "DEV_DATABASE_URL", "SQLALCHEMY_DATABASE_URI")
    ]

    if app_env in {"PROD", "PRODUCTION"}:
        raise pytest.UsageError("Pytest is forbidden when APP_ENV is PROD")

    if any(
        marker in database_url
        for marker in PRODUCTION_DATABASE_MARKERS
        for database_url in configured_urls
    ):
        raise pytest.UsageError("Pytest is forbidden against the production Neon database")

    if any(configured_urls) and str(os.getenv("BT38_ALLOW_DATABASE_TESTS") or "").lower() != "true":
        raise pytest.UsageError(
            "A configured database requires BT38_ALLOW_DATABASE_TESTS=true "
            "and an isolated non-production database"
        )
