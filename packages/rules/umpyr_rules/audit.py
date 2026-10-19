"""Audit record hashing. Canonical JSON (sorted keys, no whitespace, Decimals as strings) -> SHA-256."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any


def _default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return format(obj, "f")
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="python")
    if isinstance(obj, set):
        return sorted(obj)
    raise TypeError(f"not serialisable: {type(obj)}")


def canonical_json(record: Any) -> str:
    return json.dumps(record, default=_default, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def audit_hash(record: Any) -> str:
    return hashlib.sha256(canonical_json(record).encode("utf-8")).hexdigest()
