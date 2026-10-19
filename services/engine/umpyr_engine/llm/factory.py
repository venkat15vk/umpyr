from __future__ import annotations

from functools import lru_cache

from ..config import get_settings
from ..db.base import get_session_factory
from .base import LLMProvider
from .budget import BudgetGuard


@lru_cache
def get_budget() -> BudgetGuard:
    settings = get_settings()
    return BudgetGuard(get_session_factory(), settings.daily_cost_cap_usd)


@lru_cache
def get_llm() -> LLMProvider:
    settings = get_settings()
    if settings.llm_provider == "claude":
        from .claude import ClaudeProvider

        return ClaudeProvider(settings, get_budget())
    if settings.llm_provider == "opensource":
        from .opensource import OpenSourceProvider

        return OpenSourceProvider()
    raise RuntimeError(f"unknown LLM_PROVIDER '{settings.llm_provider}' (expected claude|opensource)")
