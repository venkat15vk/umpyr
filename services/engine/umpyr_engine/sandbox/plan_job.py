"""`umpyr sandbox plans <client>`: embed the structured text of every historical case for the historical planner
(design v2 §4). Uses the configured embedder (local hash embedder in the sandboxes: free). Idempotent."""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import models as m
from ..embeddings import get_embedder
from ..resolve.planner import backfill_plan_embeddings

log = logging.getLogger(__name__)


def refresh_plan_embeddings(session: Session, client: str, rebuild: bool = False) -> dict:
    n = backfill_plan_embeddings(session, get_embedder(), client, only_missing=not rebuild)
    total = session.execute(
        select(func.count()).select_from(m.Case).where(m.Case.workspace_id == client, m.Case.plan_embedding.isnot(None))
    ).scalar()
    log.info("plan embeddings %s: embedded %d, %d cases now have one", client, n, total)
    return {"embedded": n, "with_plan_embedding": total}


def needs_plan_embeddings(session: Session, client: str) -> bool:
    n = session.execute(
        select(func.count()).select_from(m.Case).where(m.Case.workspace_id == client, m.Case.plan_embedding.is_(None))
    ).scalar()
    return bool(n)
