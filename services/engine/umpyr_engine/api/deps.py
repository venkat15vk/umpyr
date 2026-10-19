from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from ..db.base import get_session_factory


def db_session() -> Iterator[Session]:
    with get_session_factory()() as s:
        yield s
