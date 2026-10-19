"""`umpyr sandbox compile <client>`: archived sources -> clauses -> rules -> rule pack (cached unless --rebuild)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from umpyr_rules import Rule

from ..compile.compiler import (
    apply_overrides,
    assemble_pack,
    compile_report,
    convert_clauses,
    new_version,
    segment_documents,
)
from ..compile.sources import load_archived
from ..db import models as m
from ..llm import get_llm
from .paths import client_dir, policy_dir, sources_manifest
from .workspace import ensure_workspace

log = logging.getLogger(__name__)

PREFIX = {"rockler": "RK", "alaska": "AK"}


def keep_sections(text: str, pattern: str, keep: list[str]) -> str:
    """Keep only the sections whose heading starts with one of `keep` (headings matched by `pattern`)."""
    import re

    lines = text.split("\n")
    out: list[str] = []
    keeping = True
    heading = re.compile(pattern)
    for line in lines:
        if heading.match(line.strip()):
            keeping = any(line.strip().startswith(k) for k in keep)
        if keeping:
            out.append(line)
    return "\n".join(out)


def load_documents(client: str) -> list[dict]:
    manifest = sources_manifest(client)
    docs = []
    seen_sha: set[str] = set()
    for s in manifest["sources"]:
        archived = load_archived(s["id"], policy_dir(client))
        if not archived:
            log.warning("source %s not archived; run `umpyr sandbox fetch %s`", s["id"], client)
            continue
        meta, text = archived
        if not meta.get("ok"):
            continue
        if s.get("keep_sections"):
            text = keep_sections(text, s["section_pattern"], s["keep_sections"])
        if meta["sha256"] in seen_sha:  # e.g. return-policy redirects to the FAQ page
            log.info("skipping %s: identical content to an earlier source", s["id"])
            continue
        seen_sha.add(meta["sha256"])
        docs.append(
            {
                "source_id": s["id"],
                "title": s.get("title") or meta.get("title") or s["id"],
                "url": s["url"],
                "final_url": meta.get("final_url"),
                "tier": s["tier"],
                "fetched_at": meta.get("fetched_at"),
                "text": text,
                "sha256": meta["sha256"],
                "content_type": meta.get("content_type"),
                "raw_path": meta.get("raw_path"),
            }
        )
    return docs


def persist(session: Session, client: str, docs: list[dict], clauses: list[dict], pack, report: dict) -> None:
    ensure_workspace(session, client, sources_manifest(client)["name"])
    # documents
    session.execute(delete(m.PolicyDocument).where(m.PolicyDocument.workspace_id == client))
    doc_ids: dict[str, object] = {}
    for d in docs:
        row = m.PolicyDocument(
            workspace_id=client,
            title=d["title"],
            source_url=d["url"],
            source_type="pdf" if d.get("content_type") == "application/pdf" else "url",
            tier=d["tier"],
            fetched_at=datetime.fromisoformat(d["fetched_at"]) if d.get("fetched_at") else None,
            raw_path=f"samples/{client}/policy/{d.get('raw_path')}",
            text=d["text"],
            sha256=d["sha256"],
            status="compiled",
        )
        session.add(row)
        session.flush()
        doc_ids[d["source_id"]] = row.id
    # clauses
    session.execute(delete(m.Clause).where(m.Clause.workspace_id == client))
    for c in clauses:
        session.add(
            m.Clause(
                workspace_id=client,
                clause_id=c["clause_id"],
                document_id=doc_ids.get(c["source_id"]),
                title=c["title"],
                text=c["text"],
                tier=c["tier"],
                source_url=c["source_url"],
                ambiguous=c["ambiguous"],
                ambiguity_note=c.get("ambiguity_note"),
            )
        )
    # rule pack (retire previous active)
    for old in session.execute(
        select(m.RulePack).where(m.RulePack.workspace_id == client, m.RulePack.status == "active")
    ).scalars():
        old.status = "retired"
    rp = m.RulePack(
        workspace_id=client,
        version=pack.version,
        pack=json.loads(pack.model_dump_json()),
        rule_count=len(pack.rules),
        conflicts=pack.conflicts,
        status="active",
    )
    session.add(rp)
    session.flush()
    for r in pack.rules:
        session.add(
            m.Rule(
                workspace_id=client,
                rule_pack_id=rp.id,
                rule_id=r.rule_id,
                clause_id=r.clause_id,
                tier=r.tier,
                rule=json.loads(r.model_dump_json()),
                enabled=r.enabled,
            )
        )
    session.commit()


def run_compile(
    session: Session, client: str, rebuild: bool = False, use_batch: bool = True, reassemble: bool = False
) -> dict:
    """rebuild: re-run segmentation and rule conversion (LLM). reassemble: re-apply rule_overrides.json to the
    cached raw rules (no LLM). Neither: load the cached pack and persist it."""
    out = client_dir(client)
    pack_path = out / "rule_pack.json"
    clauses_path = out / "clauses.json"
    raw_rules_path = out / "rules_raw.json"
    report_path = out / "compile_report.json"
    docs = load_documents(client)
    if not docs:
        raise RuntimeError(f"no archived sources for {client}")

    if pack_path.exists() and clauses_path.exists() and not rebuild and not reassemble:
        log.info("using cached rule pack %s", pack_path)
        from umpyr_rules import RulePack

        pack = RulePack.model_validate_json(pack_path.read_text())
        clauses = json.loads(clauses_path.read_text())
        report = json.loads(report_path.read_text()) if report_path.exists() else {}
        persist(session, client, docs, clauses, pack, report)
        return report

    prefix = PREFIX.get(client, client[:2].upper())
    if clauses_path.exists() and not rebuild:
        log.info("using cached clauses %s", clauses_path)
        clauses = json.loads(clauses_path.read_text())
    else:
        clauses = segment_documents(get_llm(), docs, prefix, client, use_batch=use_batch)
        clauses_path.write_text(json.dumps(clauses, indent=2))
    log.info("%d clauses (%d dispute-relevant)", len(clauses), sum(c["relevant_to_disputes"] for c in clauses))

    if raw_rules_path.exists() and not rebuild:
        log.info("using cached raw rules %s", raw_rules_path)
        raw = json.loads(raw_rules_path.read_text())
        rules = [Rule.model_validate(r) for r in raw["rules"]]
        conv_report = raw.get("report", [])
    else:
        rules, conv_report = convert_clauses(get_llm(), clauses, client, use_batch=use_batch)
        raw_rules_path.write_text(
            json.dumps({"rules": [json.loads(r.model_dump_json()) for r in rules], "report": conv_report}, indent=2)
        )

    overrides_path = out / "rule_overrides.json"
    overrides = json.loads(overrides_path.read_text()) if overrides_path.exists() else None
    rules, edits = apply_overrides(rules, overrides)
    existing = [
        v for (v,) in session.execute(select(m.RulePack.version).where(m.RulePack.workspace_id == client)).all()
    ]
    pack = assemble_pack(client, clauses, rules, new_version(existing))
    pack_path.write_text(pack.model_dump_json(indent=2))
    report = compile_report(clauses, rules, conv_report, pack)
    report["merchant_edits"] = edits
    report["compiled_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    report_path.write_text(json.dumps(report, indent=2, default=str))
    persist(session, client, docs, clauses, pack, report)
    return report
