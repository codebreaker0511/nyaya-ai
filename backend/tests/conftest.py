"""Shared test setup.

Tests that touch the database create and delete rows, so they must NEVER run against the real
(hosted) database. They only run when TEST_DATABASE_URL is set (in the environment or backend/.env),
pointing at a separate, disposable database. Otherwise they are skipped.
"""
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
if TEST_DATABASE_URL:
    if TEST_DATABASE_URL == os.getenv("DATABASE_URL"):
        raise RuntimeError("TEST_DATABASE_URL must be a separate database, not DATABASE_URL")
    # Set before app.db.database is imported (load_dotenv never overrides existing variables).
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL


def pytest_collection_modifyitems(config, items):
    if TEST_DATABASE_URL:
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL not set (DB tests never use the real database)")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


def pytest_configure(config):
    config.addinivalue_line("markers", "db: test needs the PostgreSQL test database")


@pytest.fixture(autouse=True)
def _limits_off_by_default(request, monkeypatch):
    """Existing chat tests send many requests from the same test client, so rate limits and quotas are
    relaxed by default. Tests for the limits switch them back on explicitly. For DB tests, also start
    with empty usage/rate-limit tables so runs don't affect each other (test database only)."""
    from app import config

    monkeypatch.setattr(config, "SESSION_RATE_LIMIT_PER_MINUTE", 0)
    monkeypatch.setattr(config, "IP_RATE_LIMIT_PER_MINUTE", 0)
    monkeypatch.setattr(config, "SESSION_CREATE_LIMIT_PER_IP_PER_HOUR", 0)
    monkeypatch.setattr(config, "GLOBAL_DAILY_TOKEN_LIMIT", 10**12)
    if TEST_DATABASE_URL and "db" in request.keywords:
        from sqlalchemy import text

        from app.db.database import Base, engine
        from app.models import conversation, message, session, usage  # noqa: F401

        Base.metadata.create_all(bind=engine)
        with engine.begin() as conn:
            for table in ("token_reservations", "daily_token_usage", "rate_limit_counters"):
                conn.execute(text(f"DELETE FROM {table}"))
    yield
