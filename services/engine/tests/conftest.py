"""Engine tests run against an ephemeral embedded Postgres (pgserver) so they need no Docker.

Settings are overridden before anything imports them: DATABASE_URL points at a temp pgserver
dir, the cap is small, and no API keys are needed (LLM calls are mocked).
"""

from __future__ import annotations

import os
import tempfile
from decimal import Decimal

import pytest

_TMP = tempfile.mkdtemp(prefix="umpyr-test-pg-")
os.environ["DATABASE_URL"] = f"pgserver://{_TMP}"
os.environ["DAILY_COST_CAP_USD"] = "0.05"
os.environ["CLAUDE_MODEL"] = "claude-haiku-5-5"
os.environ["LLM_PROVIDER"] = "claude"
os.environ["ANTHROPIC_API_KEY"] = "test-key-not-real"
os.environ["ENGINE_ENV"] = "test"

from umpyr_engine.config import get_settings  # noqa: E402
from umpyr_engine.db import models  # noqa: E402,F401
from umpyr_engine.db.base import Base, get_engine, get_session_factory  # noqa: E402
from umpyr_engine.llm.budget import BudgetGuard  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    get_settings.cache_clear()
    engine = get_engine()
    from sqlalchemy import text

    with engine.connect() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        c.commit()
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def session_factory():
    return get_session_factory()


@pytest.fixture
def clean_spend(session_factory):
    from sqlalchemy import delete

    with session_factory() as s:
        s.execute(delete(models.LLMCall))
        s.execute(delete(models.DailySpend))
        s.commit()
    yield


@pytest.fixture
def budget(session_factory, clean_spend) -> BudgetGuard:
    return BudgetGuard(session_factory, Decimal("0.05"))
