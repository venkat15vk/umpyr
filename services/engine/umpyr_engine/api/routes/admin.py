from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db.models import EmbeddingCall
from ...llm.factory import get_budget
from ..deps import db_session

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/spend")
def spend(session: Session = Depends(db_session)) -> dict:
    """Today's LLM spend (UTC day), calls by pipeline step, and embedding usage (not capped)."""
    summary = get_budget().summary()
    today = datetime.now(UTC).date()
    emb = session.execute(
        select(
            func.count(),
            func.coalesce(func.sum(EmbeddingCall.tokens), 0),
            func.coalesce(func.sum(EmbeddingCall.cost_usd), 0),
        ).where(EmbeddingCall.day == today)
    ).one()
    summary["embeddings_today"] = {"calls": int(emb[0]), "tokens": int(emb[1]), "cost_usd": float(emb[2])}
    return summary
