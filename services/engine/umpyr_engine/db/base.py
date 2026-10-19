"""SQLAlchemy engine/session plumbing.

DATABASE_URL forms:
- postgresql+psycopg://user:pass@host:5432/db   (docker compose / Railway)
- pgserver://<data dir>                           (embedded dev Postgres with pgvector, no Docker needed)
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ..config import get_settings

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


_pg_servers: dict[str, object] = {}


def resolve_database_url(url: str) -> str:
    if not url.startswith("pgserver:"):
        return url
    import pgserver  # local dev only

    # pgserver://.pgdata (relative to repo root) or pgserver:///abs/path
    raw = url[len("pgserver:") :]
    if raw.startswith("//"):
        raw = raw[2:]
    data_dir = Path(raw or ".pgdata")
    if not data_dir.is_absolute():
        data_dir = get_settings().root / data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    key = str(data_dir)
    if key not in _pg_servers:
        _pg_servers[key] = pgserver.get_server(key)
    server = _pg_servers[key]
    uri = server.get_uri()  # postgresql://postgres:@/postgres?host=/path
    return uri.replace("postgresql://", "postgresql+psycopg://", 1)


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    url = resolve_database_url(settings.database_url)
    return create_engine(url, pool_pre_ping=True, future=True)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def db_ping() -> bool:
    try:
        with get_engine().connect() as c:
            c.execute(text("select 1"))
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("db ping failed: %s", e)
        return False
