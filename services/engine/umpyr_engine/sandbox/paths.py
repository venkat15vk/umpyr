from __future__ import annotations

import json
from pathlib import Path

from ..config import get_settings


def client_dir(client: str) -> Path:
    d = get_settings().samples_dir / client
    d.mkdir(parents=True, exist_ok=True)
    return d


def sources_manifest(client: str) -> dict:
    return json.loads((client_dir(client) / "sources.json").read_text())


def policy_dir(client: str) -> Path:
    d = client_dir(client) / "policy"
    d.mkdir(parents=True, exist_ok=True)
    return d
