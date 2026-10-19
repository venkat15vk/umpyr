from __future__ import annotations

from fastapi import APIRouter

from ...config import get_settings
from ...db.base import db_ping

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "env": s.engine_env,
        "db": db_ping(),
        "llm_provider": s.llm_provider,
        "model": s.claude_model,
        "daily_cost_cap_usd": float(s.daily_cost_cap_usd),
    }
