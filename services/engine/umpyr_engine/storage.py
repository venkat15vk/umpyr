"""Upload storage behind a small interface. Local disk in dev; R2 (presigned URLs) is the production target (M5)."""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import Protocol

from .config import get_settings

log = logging.getLogger(__name__)


class Storage(Protocol):
    def put(self, key: str, data: bytes) -> str: ...
    def get(self, key: str) -> bytes: ...
    def delete_prefix(self, prefix: str) -> int: ...


class LocalStorage:
    """Files under services/engine/data/uploads/<key>. Never committed (data/ is git-ignored)."""

    def __init__(self, root: Path | None = None):
        self.root = root or (get_settings().data_dir / "uploads")
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError("invalid storage key")
        return p

    def put(self, key: str, data: bytes) -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete_prefix(self, prefix: str) -> int:
        p = self._path(prefix)
        if not p.exists():
            return 0
        n = sum(1 for _ in p.rglob("*") if _.is_file())
        shutil.rmtree(p)
        return n


class R2Storage:
    """Cloudflare R2 via S3-compatible API with presigned PUT/GET URLs. Wired in M5 after the founder enables R2."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("R2 storage is configured in M5 (needs R2_ACCOUNT_ID / keys / bucket)")


def get_storage() -> Storage:
    return LocalStorage()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
