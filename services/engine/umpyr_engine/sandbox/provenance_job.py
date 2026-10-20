"""`umpyr sandbox provenance <client>`: attach provenance to the cached clauses and rule pack of a sandbox
and push it into the database. No model calls; idempotent; safe at boot.

Reads samples/<client>/clauses.json + policy/*.meta.json (+ the archived PDF for page numbers), writes the
`provenance` block back into clauses.json and rule_pack.json, then updates `clauses` rows and the active
`rule_packs.pack` for that workspace.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session
from umpyr_rules import Provenance, RulePack

from ..compile.provenance import attach_provenance, clause_columns
from ..db import models as m
from .compile_job import load_documents
from .paths import client_dir, policy_dir

log = logging.getLogger(__name__)


def refresh_provenance(session: Session, client: str, write_samples: bool = True) -> dict:
    out = client_dir(client)
    clauses_path = out / "clauses.json"
    pack_path = out / "rule_pack.json"
    if not clauses_path.exists() or not pack_path.exists():
        raise RuntimeError(f"no cached clauses/rule pack for {client}; run `umpyr sandbox compile {client}` first")
    docs = load_documents(client)
    clauses = json.loads(clauses_path.read_text())
    report = attach_provenance(clauses, docs, policy_dir(client))
    by_id = {c["clause_id"]: c for c in clauses}
    pack = RulePack.model_validate_json(pack_path.read_text())
    for cl in pack.clauses:
        src = by_id.get(cl.clause_id)
        if src and src.get("provenance"):
            cl.provenance = Provenance.model_validate(src["provenance"])
    if write_samples:
        clauses_path.write_text(json.dumps(clauses, indent=2))
        pack_path.write_text(pack.model_dump_json(indent=2))
    # database
    rows = session.execute(select(m.Clause).where(m.Clause.workspace_id == client)).scalars().all()
    updated = 0
    for row in rows:
        src = by_id.get(row.clause_id)
        if not src:
            continue
        for k, v in clause_columns(src).items():
            setattr(row, k, v)
        updated += 1
    active = session.execute(
        select(m.RulePack).where(m.RulePack.workspace_id == client, m.RulePack.status == "active")
    ).scalar_one_or_none()
    if active is not None and active.version == pack.version:
        active.pack = json.loads(pack.model_dump_json())
        session.add(active)
    session.commit()
    report["db_clauses_updated"] = updated
    report["db_pack_updated"] = bool(active is not None and active.version == pack.version)
    log.info("provenance %s: %s", client, {k: v for k, v in report.items() if k != "unlocated"})
    return report


def needs_provenance(session: Session, client: str) -> bool:
    from sqlalchemy import func

    n = session.execute(
        select(func.count()).select_from(m.Clause).where(m.Clause.workspace_id == client, m.Clause.text_hash.is_(None))
    ).scalar()
    return bool(n)
