from __future__ import annotations

import json
import logging

from ..compile.sources import fetch_all
from .paths import policy_dir, sources_manifest

log = logging.getLogger(__name__)


def run_fetch(client: str, rebuild: bool = False) -> list[dict]:
    manifest = sources_manifest(client)
    results = fetch_all(manifest["sources"], policy_dir(client), rebuild=rebuild)
    (policy_dir(client) / "_fetch_report.json").write_text(json.dumps(results, indent=2))
    for r in results:
        status = "ok " if r.get("ok") else "GAP"
        log.info(
            "%s %-30s %6s chars  %s %s",
            status,
            r["source_id"],
            r.get("chars", 0),
            r.get("extractor", ""),
            "; ".join(r.get("notes", [])),
        )
    return results
