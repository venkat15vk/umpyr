"""Upload flow API: create workspace -> upload files / URLs -> approve column mapping -> run onboarding job -> setup."""

from __future__ import annotations

import json
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...db import models as m
from ...ingest.mapping import propose_mapping
from ...ingest.parsers import (
    POLICY_EXTS,
    RECORD_EXTS,
    TICKET_EXTS,
    crawl_policy_urls,
    parse_policy_file,
    parse_record_file,
    parse_ticket_file,
)
from ...jobs.queue import enqueue
from ...llm import get_llm
from ...onboard.pipeline import rebuild_active_pack, workspace_ready
from ...storage import get_storage, sha256
from ..deps import db_session

router = APIRouter(tags=["onboard"])


class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "ws"
    return f"{base}-{secrets.token_hex(3)}"


@router.post("/workspaces")
def create_workspace(body: WorkspaceCreate, session: Session = Depends(db_session)) -> dict:
    s = get_settings()
    ws = m.Workspace(
        id=_slug(body.name),
        name=body.name,
        kind="upload",
        status="new",
        settings={"mode": "autonomous", "auto_approve_limit": 100, "reply_channel": "email", "record_kind": "order"},
        expires_at=datetime.now(UTC) + timedelta(days=s.data_retention_days),
        is_synthetic=False,
    )
    session.add(ws)
    session.commit()
    return {
        "id": ws.id,
        "name": ws.name,
        "status": ws.status,
        "expires_at": ws.expires_at.isoformat(),
        "retention_days": s.data_retention_days,
    }


def _ws(session: Session, workspace_id: str) -> m.Workspace:
    ws = session.get(m.Workspace, workspace_id)
    if ws is None:
        raise HTTPException(404, "workspace not found")
    return ws


def _upload_dict(u: m.Upload) -> dict:
    return {
        "id": str(u.id),
        "kind": u.kind,
        "filename": u.filename,
        "size_bytes": u.size_bytes,
        "status": u.status,
        "row_count": u.row_count,
        "mapping": u.mapping,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


@router.get("/workspaces/{workspace_id}/uploads")
def list_uploads(workspace_id: str, session: Session = Depends(db_session)) -> list[dict]:
    _ws(session, workspace_id)
    return [
        _upload_dict(u)
        for u in session.execute(
            select(m.Upload).where(m.Upload.workspace_id == workspace_id).order_by(m.Upload.created_at)
        ).scalars()
    ]


def _store_upload(
    session: Session, ws: m.Workspace, kind: str, filename: str, data: bytes, content_type: str | None
) -> m.Upload:
    s = get_settings()
    used = sum(u.size_bytes for u in session.execute(select(m.Upload).where(m.Upload.workspace_id == ws.id)).scalars())
    if used + len(data) > s.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"workspace upload limit is {s.max_upload_mb} MB")
    key = f"{ws.id}/{kind}/{uuid.uuid4().hex[:8]}-{re.sub(r'[^A-Za-z0-9._-]', '_', filename)}"
    get_storage().put(key, data)
    up = m.Upload(
        workspace_id=ws.id,
        kind=kind,
        filename=filename,
        content_type=content_type,
        size_bytes=len(data),
        storage_key=key,
        status="stored",
    )
    session.add(up)
    session.commit()
    return up


@router.post("/workspaces/{workspace_id}/uploads")
async def upload_file(
    workspace_id: str, kind: str = Form(...), file: UploadFile = File(...), session: Session = Depends(db_session)
) -> dict:
    ws = _ws(session, workspace_id)
    if kind not in ("tickets", "policy", "orders"):
        raise HTTPException(400, "kind must be tickets | policy | orders")
    data = await file.read()
    name = file.filename or "upload"
    ext = "." + name.lower().rsplit(".", 1)[-1] if "." in name else ""
    allowed = {"tickets": TICKET_EXTS, "policy": POLICY_EXTS, "orders": RECORD_EXTS}[kind]
    if ext not in allowed:
        raise HTTPException(400, f"{kind} accepts {sorted(allowed)}")
    s = get_settings()
    up = _store_upload(session, ws, kind, name, data, file.content_type)
    if kind == "tickets":
        rows, cols = parse_ticket_file(name, data)
        if len(rows) > s.max_upload_tickets:
            raise HTTPException(413, f"max {s.max_upload_tickets} tickets per workspace (file has {len(rows)})")
        up.row_count = len(rows)
        if ext in (".eml", ".mbox"):
            up.mapping = {
                "kind": "tickets",
                "columns": cols,
                "mapping": {},
                "thread_format": "json_list",
                "status": "approved",
                "notes": "mail parsed into threads",
            }
            up.status = "mapped"
        else:
            up.mapping = propose_mapping(get_llm(), ws.id, cols, rows, kind="tickets")
            up.mapping["samples"] = [
                {c: (str(r.get(c))[:120] if r.get(c) is not None else None) for c in cols} for r in rows[:3]
            ]
    elif kind == "orders":
        rows, cols = parse_record_file(name, data)
        up.row_count = len(rows)
        up.mapping = propose_mapping(get_llm(), ws.id, cols, rows, kind="orders")
        up.mapping["samples"] = [
            {c: (str(r.get(c))[:120] if r.get(c) is not None else None) for c in cols} for r in rows[:3]
        ]
    else:
        pt = parse_policy_file(name, data)
        session.add(
            m.PolicyDocument(
                workspace_id=ws.id,
                title=pt.title,
                source_url=None,
                source_type=pt.source_type,
                tier="contract",
                fetched_at=datetime.now(UTC),
                raw_path=up.storage_key,
                text=pt.text,
                sha256=sha256(data),
                status="fetched",
            )
        )
        up.row_count = len(pt.text)
        up.status = "ingested"
    session.add(up)
    ws.status = "uploading"
    session.add(ws)
    session.commit()
    return _upload_dict(up)


class UrlBody(BaseModel):
    url: str = Field(..., min_length=8)
    tier: str = Field("contract", pattern="^(law|contract|commitment|goodwill)$")


@router.post("/workspaces/{workspace_id}/policy-urls")
def add_policy_url(workspace_id: str, body: UrlBody, session: Session = Depends(db_session)) -> dict:
    ws = _ws(session, workspace_id)
    docs = crawl_policy_urls(body.url)
    if not docs:
        raise HTTPException(422, "no readable policy text found at that URL (JavaScript-only page?)")
    for d in docs:
        session.add(
            m.PolicyDocument(
                workspace_id=ws.id,
                title=d.title[:300],
                source_url=d.source_url,
                source_type="url",
                tier=body.tier,
                fetched_at=datetime.now(UTC),
                text=d.text,
                sha256=sha256(d.text.encode()),
                status="fetched",
            )
        )
    ws.status = "uploading"
    session.add(ws)
    session.commit()
    return {
        "documents": [{"title": d.title, "url": d.source_url, "chars": len(d.text), "notes": d.notes} for d in docs]
    }


class MappingPatch(BaseModel):
    mapping: dict[str, str | None] = Field(default_factory=dict, description="target -> source column (or null)")
    thread_format: str | None = None
    approve: bool = True


@router.patch("/workspaces/{workspace_id}/uploads/{upload_id}/mapping")
def patch_mapping(
    workspace_id: str, upload_id: uuid.UUID, body: MappingPatch, session: Session = Depends(db_session)
) -> dict:
    _ws(session, workspace_id)
    up = session.get(m.Upload, upload_id)
    if up is None or up.workspace_id != workspace_id:
        raise HTTPException(404, "upload not found")
    mp = dict(up.mapping or {"mapping": {}})
    for target, source in body.mapping.items():
        mp.setdefault("mapping", {})[target] = {
            **mp.get("mapping", {}).get(target, {}),
            "source": source,
            "confidence": 1.0,
            "note": "set by merchant",
        }
    if body.thread_format:
        mp["thread_format"] = body.thread_format
    mp["status"] = "approved" if body.approve else "proposed"
    up.mapping = mp
    up.status = "mapped" if body.approve else "stored"
    session.add(up)
    session.commit()
    return _upload_dict(up)


@router.post("/workspaces/{workspace_id}/uploads/sample")
def load_sample(workspace_id: str, client: str = "rockler", session: Session = Depends(db_session)) -> dict:
    """Demo helper: load the sandbox's synthetic export files (tickets.csv, orders.csv, policy text) as uploads."""
    ws = _ws(session, workspace_id)
    base = get_settings().samples_dir / client
    if not (base / "tickets.csv").exists():
        raise HTTPException(404, "sample files not built; run `umpyr sandbox build <client>`")
    out = []
    data = (base / "tickets.csv").read_bytes()
    up = _store_upload(session, ws, "tickets", f"{client}_tickets.csv", data, "text/csv")
    rows, cols = parse_ticket_file(up.filename, data)
    up.row_count = len(rows)
    up.mapping = propose_mapping(get_llm(), ws.id, cols, rows, kind="tickets")
    up.mapping["samples"] = [
        {c: (str(r.get(c))[:120] if r.get(c) is not None else None) for c in cols} for r in rows[:3]
    ]
    session.add(up)
    out.append(up)
    orders_csv = base / "orders.csv"
    if orders_csv.exists():
        data = orders_csv.read_bytes()
        up = _store_upload(session, ws, "orders", f"{client}_orders.csv", data, "text/csv")
        rows, cols = parse_record_file(up.filename, data)
        up.row_count = len(rows)
        up.mapping = propose_mapping(get_llm(), ws.id, cols, rows, kind="orders")
        up.mapping["samples"] = [
            {c: (str(r.get(c))[:120] if r.get(c) is not None else None) for c in cols} for r in rows[:3]
        ]
        session.add(up)
        out.append(up)
    manifest = json.loads((base / "sources.json").read_text())
    for src in manifest["sources"][:3]:
        txt = base / "policy" / f"{src['id']}.txt"
        if txt.exists() and len(txt.read_text()) > 200:
            text = txt.read_text()
            session.add(
                m.PolicyDocument(
                    workspace_id=ws.id,
                    title=src.get("title") or src["id"],
                    source_url=src["url"],
                    source_type="url",
                    tier=src["tier"],
                    fetched_at=datetime.now(UTC),
                    text=text,
                    sha256=sha256(text.encode()),
                    status="fetched",
                )
            )
    ws.status = "uploading"
    ws.settings = {**(ws.settings or {}), "record_kind": "booking" if client == "alaska" else "order"}
    session.add(ws)
    session.commit()
    return {"uploads": [_upload_dict(u) for u in out], "policy_documents": 3}


@router.post("/workspaces/{workspace_id}/onboard")
def start_onboarding(workspace_id: str, session: Session = Depends(db_session)) -> dict:
    ws = _ws(session, workspace_id)
    tickets = [
        u
        for u in session.execute(
            select(m.Upload).where(m.Upload.workspace_id == ws.id, m.Upload.kind == "tickets")
        ).scalars()
    ]
    if not tickets:
        raise HTTPException(400, "upload a ticket export first")
    unapproved = [u.filename for u in tickets if (u.mapping or {}).get("status") != "approved"]
    if unapproved:
        raise HTTPException(400, f"approve the column mapping first: {unapproved}")
    job = enqueue(session, "onboard.pipeline", {}, ws.id)
    ws.status = "onboarding"
    session.add(ws)
    session.commit()
    return {"job_id": str(job.id)}


# ---------------------------------------------------------------- setup / approvals
class CategoryPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = Field(None, pattern="^(pending|approved|merged)$")
    merge_into: str | None = None


@router.patch("/workspaces/{workspace_id}/categories/{key}")
def patch_category(workspace_id: str, key: str, body: CategoryPatch, session: Session = Depends(db_session)) -> dict:
    _ws(session, workspace_id)
    cat = (
        session.execute(select(m.Category).where(m.Category.workspace_id == workspace_id, m.Category.key == key))
        .scalars()
        .first()
    )
    if cat is None:
        raise HTTPException(404, "category not found")
    if body.merge_into:
        target = (
            session.execute(
                select(m.Category).where(m.Category.workspace_id == workspace_id, m.Category.key == body.merge_into)
            )
            .scalars()
            .first()
        )
        if target is None:
            raise HTTPException(404, "merge target not found")
        for c in session.execute(
            select(m.Case).where(m.Case.workspace_id == workspace_id, m.Case.category_key == key)
        ).scalars():
            c.category_key = target.key
            session.add(c)
        target.volume = (target.volume or 0) + (cat.volume or 0)
        cat.status = "merged"
        cat.merged_into = target.key
        session.add(target)
    if body.name:
        cat.name = body.name
    if body.description is not None:
        cat.description = body.description
    if body.status:
        cat.status = body.status
        cat.approved_by = "merchant" if body.status == "approved" else None
    session.add(cat)
    session.commit()
    return {"key": cat.key, "name": cat.name, "status": cat.status, "merged_into": cat.merged_into}


class ProcedurePatch(BaseModel):
    status: str = Field(..., pattern="^(draft|approved)$")
    steps: list[dict] | None = None


@router.patch("/workspaces/{workspace_id}/procedures/{category_key}")
def patch_procedure(
    workspace_id: str, category_key: str, body: ProcedurePatch, session: Session = Depends(db_session)
) -> dict:
    _ws(session, workspace_id)
    proc = (
        session.execute(
            select(m.Procedure)
            .where(m.Procedure.workspace_id == workspace_id, m.Procedure.category_key == category_key)
            .order_by(m.Procedure.version.desc())
        )
        .scalars()
        .first()
    )
    if proc is None:
        raise HTTPException(404, "procedure not found")
    if body.steps is not None:
        proc.steps = body.steps
    proc.status = body.status
    proc.approved_by = "merchant" if body.status == "approved" else None
    proc.approved_at = datetime.now(UTC) if body.status == "approved" else None
    session.add(proc)
    session.commit()
    return {"category_key": proc.category_key, "version": proc.version, "status": proc.status}


class RulePatch(BaseModel):
    enabled: bool


@router.patch("/workspaces/{workspace_id}/rules/{rule_id}")
def patch_rule(workspace_id: str, rule_id: str, body: RulePatch, session: Session = Depends(db_session)) -> dict:
    _ws(session, workspace_id)
    rp = (
        session.execute(
            select(m.RulePack).where(m.RulePack.workspace_id == workspace_id, m.RulePack.status == "active")
        )
        .scalars()
        .first()
    )
    if rp is None:
        raise HTTPException(404, "no active rule pack")
    row = (
        session.execute(select(m.Rule).where(m.Rule.rule_pack_id == rp.id, m.Rule.rule_id == rule_id)).scalars().first()
    )
    if row is None:
        raise HTTPException(404, "rule not found")
    row.enabled = body.enabled
    session.add(row)
    session.commit()
    rebuild_active_pack(session, workspace_id)
    return {"rule_id": rule_id, "enabled": body.enabled}


@router.get("/workspaces/{workspace_id}/readiness")
def readiness(workspace_id: str, session: Session = Depends(db_session)) -> dict:
    ws = _ws(session, workspace_id)
    ok, reasons = workspace_ready(session, workspace_id)
    if ok and ws.status in ("review", "onboarding"):
        ws.status = "ready"
        session.add(ws)
        session.commit()
    return {
        "ready": ok,
        "reasons": reasons,
        "status": ws.status,
        "expires_at": ws.expires_at.isoformat() if ws.expires_at else None,
    }
