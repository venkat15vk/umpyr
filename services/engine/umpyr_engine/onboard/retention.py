"""Retention: uploaded workspaces expire after DATA_RETENTION_DAYS (default 14). The sweeper deletes the workspace
row (cascades to every tenant table) and its stored files."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import models as m
from ..storage import get_storage

log = logging.getLogger(__name__)


def sweep(session: Session, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(UTC)
    expired = list(
        session.execute(
            select(m.Workspace).where(
                m.Workspace.kind == "upload", m.Workspace.expires_at.isnot(None), m.Workspace.expires_at < now
            )
        ).scalars()
    )
    storage = get_storage()
    deleted = []
    for ws in expired:
        n = storage.delete_prefix(ws.id)
        session.delete(ws)
        deleted.append(ws.id)
        log.info("retention: deleted workspace %s (%d files)", ws.id, n)
    session.commit()
    return deleted
