"""Live onboarding for uploaded workspaces: a single worker job that runs every step and streams progress.

Steps: ingest (approved mapping, PII mask) -> compile (uploaded policies) -> mine -> taxonomy -> router -> procedures
-> drift -> eval (small). Nothing is cached to samples/; the workspace's DB rows are the output. Categories and
procedures are created as *pending/draft* for the merchant to approve on the setup page.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from umpyr_rules import RulePack as RP

from ..compile.compiler import apply_overrides, assemble_pack, compile_report, convert_clauses, segment_documents
from ..db import models as m
from ..embeddings import get_embedder
from ..ingest.loader import ingest_orders, ingest_tickets
from ..ingest.mapping import apply_order_mapping, apply_ticket_mapping
from ..ingest.parsers import parse_record_file, parse_ticket_file
from ..ingest.pii import PIIMasker
from ..learn.drift import replay
from ..learn.mine import mine_workspace
from ..learn.procedures import mine_procedures
from ..learn.router import train_router
from ..learn.taxonomy import build_taxonomy
from ..llm import get_llm
from ..storage import get_storage

log = logging.getLogger(__name__)
Emit = Callable[..., None]


def ingest_uploads(session: Session, workspace_id: str, emit: Emit) -> dict[str, Any]:
    storage = get_storage()
    uploads = list(
        session.execute(
            select(m.Upload).where(m.Upload.workspace_id == workspace_id, m.Upload.status.in_(("stored", "mapped")))
        ).scalars()
    )
    names: list[str] = []
    stats: dict[str, Any] = {"tickets": 0, "records": 0, "masked": {}}
    ticket_rows: list[dict] = []
    for up in uploads:
        if up.kind != "tickets":
            continue
        rows, _cols = parse_ticket_file(up.filename, storage.get(up.storage_key))
        mapping = up.mapping or {}
        if mapping.get("status") != "approved" and up.filename.lower().split(".")[-1] not in ("eml", "mbox"):
            raise RuntimeError(f"column mapping for {up.filename} is not approved")
        if mapping.get("mapping"):
            rows = apply_ticket_mapping(rows, mapping)
        for r in rows:
            if r.get("customer_name"):
                names.append(str(r["customer_name"]))
        ticket_rows.extend(rows)
        up.row_count = len(rows)
        up.status = "ingested"
        session.add(up)
    masker = PIIMasker(known_names=names)
    if ticket_rows:
        res = ingest_tickets(session, workspace_id, ticket_rows, masker, is_synthetic=False, replace=True)
        stats["tickets"] = res["tickets"]
        stats["masked"] = res["masked"]
        emit("ingest", f"{res['tickets']} tickets ingested; PII masked: {res['masked']}")
    records: list[dict] = []
    for up in uploads:
        if up.kind != "orders":
            continue
        rows, _cols = parse_record_file(up.filename, storage.get(up.storage_key))
        mapping = up.mapping or {}
        if mapping.get("mapping"):
            rows = apply_order_mapping(rows, mapping)
        records.extend(rows)
        up.row_count = len(rows)
        up.status = "ingested"
        session.add(up)
    if records:
        stats["records"] = ingest_orders(session, workspace_id, records, kind="order", replace=True)
        emit("ingest", f"{stats['records']} order records loaded")
    session.commit()
    return stats


def compile_uploaded_policies(session: Session, workspace_id: str, emit: Emit, prefix: str) -> dict[str, Any] | None:
    docs_rows = list(
        session.execute(select(m.PolicyDocument).where(m.PolicyDocument.workspace_id == workspace_id)).scalars()
    )
    if not docs_rows:
        emit("compile", "no policy documents uploaded; skipping compile (rulings will escalate)")
        return None
    docs = [
        {
            "source_id": str(d.id),
            "title": d.title,
            "url": d.source_url or f"upload://{d.title}",
            "tier": d.tier,
            "fetched_at": d.fetched_at.isoformat() if d.fetched_at else None,
            "text": d.text,
        }
        for d in docs_rows
    ]
    llm = get_llm()
    emit("compile", f"segmenting {len(docs)} documents into clauses")
    clauses = segment_documents(llm, docs, prefix, workspace_id, use_batch=False)
    emit(
        "compile",
        f"{len(clauses)} clauses ({sum(c['relevant_to_disputes'] for c in clauses)} dispute-relevant); converting to rules",
    )
    rules, conv_report = convert_clauses(llm, clauses, workspace_id, use_batch=False)
    rules, _ = apply_overrides(rules, None)
    version = datetime.now(UTC).strftime("%Y-%m-%d") + ".1"
    pack = assemble_pack(workspace_id, clauses, rules, version)
    session.execute(delete(m.Clause).where(m.Clause.workspace_id == workspace_id))
    doc_by_sid = {str(d.id): d.id for d in docs_rows}
    for c in clauses:
        session.add(
            m.Clause(
                workspace_id=workspace_id,
                clause_id=c["clause_id"],
                document_id=doc_by_sid.get(c["source_id"]),
                title=c["title"],
                text=c["text"],
                tier=c["tier"],
                source_url=c["source_url"],
                ambiguous=c["ambiguous"],
                ambiguity_note=c.get("ambiguity_note"),
            )
        )
    for old in session.execute(
        select(m.RulePack).where(m.RulePack.workspace_id == workspace_id, m.RulePack.status == "active")
    ).scalars():
        old.status = "retired"
    rp = m.RulePack(
        workspace_id=workspace_id,
        version=version,
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
                workspace_id=workspace_id,
                rule_pack_id=rp.id,
                rule_id=r.rule_id,
                clause_id=r.clause_id,
                tier=r.tier,
                rule=json.loads(r.model_dump_json()),
                enabled=True,
            )
        )
    for d in docs_rows:
        d.status = "compiled"
        session.add(d)
    session.commit()
    report = compile_report(clauses, rules, conv_report, pack)
    emit(
        "compile",
        f"rule pack {version}: {len(pack.rules)} rules, {len(pack.conflicts)} potential conflicts; review on the setup page",
    )
    return report


def run_onboarding(session: Session, workspace_id: str, emit: Emit, *, eval_n: int = 0) -> dict[str, Any]:
    ws = session.get(m.Workspace, workspace_id)
    if ws is None:
        raise RuntimeError("workspace not found")
    ws.status = "onboarding"
    session.add(ws)
    session.commit()
    report: dict[str, Any] = {}
    prefix = "".join(ch for ch in ws.name.upper() if ch.isalnum())[:3] or "WS"

    emit("ingest", "starting", progress=5)
    report["ingest"] = ingest_uploads(session, workspace_id, emit)
    if report["ingest"]["tickets"] == 0:
        raise RuntimeError("no tickets ingested; upload a ticket export first")

    emit("compile", "starting", progress=15)
    report["compile"] = compile_uploaded_policies(session, workspace_id, emit, prefix)

    emit("mine", "starting: extracting a structured case from every ticket", progress=30)
    res = mine_workspace(
        session, get_llm(), workspace_id, kind=(ws.settings or {}).get("record_kind", "order"), use_batch=False
    )
    report["mine"] = res
    emit("mine", f"{res['cases']} cases mined ({res['failed']} failed)", progress=55)

    emit("taxonomy", "clustering cases and naming categories", progress=60)
    tx = build_taxonomy(session, get_llm(), get_embedder(), workspace_id)
    for cat in session.execute(select(m.Category).where(m.Category.workspace_id == workspace_id)).scalars():
        cat.status = "pending"
        cat.approved_by = None
        session.add(cat)
    session.commit()
    report["taxonomy"] = {"method": tx["method"], "categories": tx["categories"]}
    emit(
        "taxonomy",
        f"{len(tx['categories'])} categories proposed ({tx['method']}); confirm or merge on the setup page",
        progress=70,
    )

    emit("router", "training the classifier", progress=72)
    try:
        report["router"] = train_router(session, get_embedder(), workspace_id)
        emit(
            "router",
            f"accuracy {report['router']['accuracy']} on held-out {report['router']['n_test']} cases",
            progress=78,
        )
    except RuntimeError as e:
        report["router"] = {"skipped": str(e)}
        emit("router", f"skipped: {e}", level="warn", progress=78)

    emit("procedures", "mining staff action sequences and drafting procedures", progress=80)
    # procedures need approved categories to iterate; draft for pending ones too
    for cat in session.execute(select(m.Category).where(m.Category.workspace_id == workspace_id)).scalars():
        cat.status = "approved"
        session.add(cat)
    session.commit()
    procs = mine_procedures(session, get_llm(), workspace_id, approve=False)
    for cat in session.execute(select(m.Category).where(m.Category.workspace_id == workspace_id)).scalars():
        cat.status = "pending"
        session.add(cat)
    session.commit()
    report["procedures"] = procs
    emit("procedures", f"{len(procs)} procedure drafts ready for approval", progress=88)

    emit("drift", "replaying the rule pack over history", progress=90)
    if report["compile"]:
        report["drift"] = replay(session, workspace_id)
        emit(
            "drift",
            f"{report['drift']['drift_findings']} drift findings, {report['drift']['unwritten_rule_candidates']} unwritten-rule candidates, {report['drift']['gold']} gold cases",
            progress=96,
        )
    else:
        emit("drift", "skipped (no rule pack)", progress=96)

    if eval_n:
        emit(
            "eval",
            f"generating {eval_n} eval scenarios is not available for uploaded workspaces in the POC",
            level="warn",
            progress=98,
        )

    ws.status = "review"
    ws.settings = {**(ws.settings or {}), "onboarded_at": datetime.now(UTC).isoformat()}
    session.add(ws)
    session.commit()
    emit("done", "onboarding complete; approve categories, procedures and rules on the setup page", progress=100)
    return report


def workspace_ready(session: Session, workspace_id: str) -> tuple[bool, list[str]]:
    reasons = []
    if (
        session.execute(
            select(m.Procedure).where(m.Procedure.workspace_id == workspace_id, m.Procedure.status == "approved")
        )
        .scalars()
        .first()
        is None
    ):
        reasons.append("no approved procedure")
    if (
        session.execute(
            select(m.Category).where(m.Category.workspace_id == workspace_id, m.Category.status == "approved")
        )
        .scalars()
        .first()
        is None
    ):
        reasons.append("no approved category")
    if (
        session.execute(
            select(m.RulePack).where(m.RulePack.workspace_id == workspace_id, m.RulePack.status == "active")
        )
        .scalars()
        .first()
        is None
    ):
        reasons.append("no active rule pack (rulings will escalate)")
    return (len([r for r in reasons if not r.startswith("no active")]) == 0), reasons


def rebuild_active_pack(session: Session, workspace_id: str) -> None:
    """Reassemble the active pack from the rules table (enabled flags) so setup-page toggles take effect."""
    rp = (
        session.execute(
            select(m.RulePack).where(m.RulePack.workspace_id == workspace_id, m.RulePack.status == "active")
        )
        .scalars()
        .first()
    )
    if rp is None:
        return
    rows = list(session.execute(select(m.Rule).where(m.Rule.rule_pack_id == rp.id)).scalars())
    pack = RP.model_validate(rp.pack)
    enabled = {r.rule_id: r.enabled for r in rows}
    for rule in pack.rules:
        rule.enabled = enabled.get(rule.rule_id, rule.enabled)
    rp.pack = json.loads(pack.model_dump_json())
    rp.rule_count = sum(1 for r in pack.rules if r.enabled)
    session.add(rp)
    session.commit()


def adopt_unwritten_rule(session: Session, workspace_id: str, finding: m.DriftFinding) -> str | None:
    """Merchant adopts a consistent staff practice as a rule. The rule lives at the goodwill tier (below every
    written clause) scoped to the finding's category and claim reason, and cites a synthetic PRACTICE clause whose
    text is the observed pattern. Returns the new rule id."""
    import re

    from umpyr_rules import Clause, Effect, Rule

    rp = (
        session.execute(
            select(m.RulePack).where(m.RulePack.workspace_id == workspace_id, m.RulePack.status == "active")
        )
        .scalars()
        .first()
    )
    if rp is None or finding.kind != "unwritten_rule":
        return None
    pack = RP.model_validate(rp.pack)
    mobj = re.search(r"claim\.reason=(\w+)", finding.pattern or "")
    reason = mobj.group(1) if mobj else None
    n = 1 + sum(1 for c in pack.clauses if c.clause_id.startswith("PRACTICE-"))
    clause_id = f"PRACTICE-{n}"
    rule_id = f"{clause_id}.m1"
    if any(r.rule_id == rule_id for r in pack.rules):
        return rule_id
    staff = finding.staff_outcome or {}
    decision = staff.get("decision") if staff.get("decision") in ("approve", "partial", "deny") else "needs_approval"
    pack.clauses.append(
        Clause(
            clause_id=clause_id,
            title=f"Adopted practice: {finding.category_key}",
            text=finding.pattern or "",
            source_url=f"practice://{workspace_id}/{finding.id}",
            source_title="Merchant-adopted practice (from drift analysis)",
            tier="goodwill",
        )
    )
    pack.rules.append(
        Rule(
            rule_id=rule_id,
            clause_id=clause_id,
            tier="goodwill",
            priority=0,
            categories=[finding.category_key] if finding.category_key else [],
            claim_reasons=[reason] if reason else [],
            requires=[],
            when=True,
            effect=Effect(
                decision=decision,
                remedy=staff.get("remedy") or None,
                note=f"Adopted from practice ({finding.support} consistent cases); no written clause governs this.",
            ),
            description=f"Merchant-adopted practice for {finding.category_key} / {reason}: {decision}/{staff.get('remedy')}",
        )
    )
    rp.pack = json.loads(pack.model_dump_json())
    rp.rule_count = len(pack.rules)
    session.add(rp)
    session.add(
        m.Clause(
            workspace_id=workspace_id,
            clause_id=clause_id,
            title=f"Adopted practice: {finding.category_key}",
            text=finding.pattern or "",
            tier="goodwill",
            source_url=f"practice://{workspace_id}/{finding.id}",
        )
    )
    session.add(
        m.Rule(
            workspace_id=workspace_id,
            rule_pack_id=rp.id,
            rule_id=rule_id,
            clause_id=clause_id,
            tier="goodwill",
            rule=json.loads(pack.rules[-1].model_dump_json()),
            enabled=True,
        )
    )
    session.commit()
    return rule_id
