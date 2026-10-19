"""Resolve chat API: sessions, messages (one turn each), settings toggles, merchant chat, audit."""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import models as m
from ...embeddings import get_embedder
from ...llm import get_llm
from ...resolve import merchant
from ...resolve.runtime import run_turn
from ..deps import db_session

router = APIRouter(tags=["resolve"])


class SessionCreate(BaseModel):
    channel: str = Field("email", pattern="^(email|agent)$")
    mode: str = Field("autonomous", pattern="^(autonomous|assist|shadow)$")
    auto_approve_limit: Decimal = Decimal("100")


class SessionPatch(BaseModel):
    channel: str | None = Field(None, pattern="^(email|agent)$")
    mode: str | None = Field(None, pattern="^(autonomous|assist|shadow)$")
    auto_approve_limit: Decimal | None = None


class MessageIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


class Question(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)


def _session_dict(s: m.ChatSession) -> dict:
    return {
        "id": str(s.id),
        "workspace_id": s.workspace_id,
        "channel": s.channel,
        "mode": s.mode,
        "auto_approve_limit": float(s.auto_approve_limit or 0),
        "status": s.status,
        "turn": (s.state or {}).get("turn", 0),
        "category_key": (s.state or {}).get("category_key"),
    }


@router.post("/workspaces/{workspace_id}/sessions")
def create_session(workspace_id: str, body: SessionCreate, session: Session = Depends(db_session)) -> dict:
    if session.get(m.Workspace, workspace_id) is None:
        raise HTTPException(404, "workspace not found")
    chat = m.ChatSession(
        workspace_id=workspace_id,
        kind="customer",
        channel=body.channel,
        mode=body.mode,
        auto_approve_limit=body.auto_approve_limit,
        state={},
    )
    session.add(chat)
    session.commit()
    return _session_dict(chat)


def _get_chat(session: Session, session_id: uuid.UUID) -> m.ChatSession:
    chat = session.get(m.ChatSession, session_id)
    if chat is None:
        raise HTTPException(404, "session not found")
    return chat


@router.get("/sessions/{session_id}")
def get_session(session_id: uuid.UUID, session: Session = Depends(db_session)) -> dict:
    chat = _get_chat(session, session_id)
    msgs = session.execute(
        select(m.ChatMessage).where(m.ChatMessage.session_id == chat.id).order_by(m.ChatMessage.created_at)
    ).scalars()
    return {
        **_session_dict(chat),
        "messages": [
            {
                "role": x.role,
                "text": x.content,
                "ops": x.payload or None,
                "at": x.created_at.isoformat() if x.created_at else None,
            }
            for x in msgs
        ],
    }


@router.patch("/sessions/{session_id}")
def patch_session(session_id: uuid.UUID, body: SessionPatch, session: Session = Depends(db_session)) -> dict:
    chat = _get_chat(session, session_id)
    if body.channel:
        chat.channel = body.channel
    if body.mode:
        chat.mode = body.mode
    if body.auto_approve_limit is not None:
        chat.auto_approve_limit = body.auto_approve_limit
    session.add(chat)
    session.commit()
    return _session_dict(chat)


@router.post("/sessions/{session_id}/messages")
def post_message(session_id: uuid.UUID, body: MessageIn, session: Session = Depends(db_session)) -> dict:
    chat = _get_chat(session, session_id)
    result = run_turn(session, get_llm(), get_embedder(), chat, body.text)
    return {**result, "session": _session_dict(chat)}


@router.post("/workspaces/{workspace_id}/merchant-chat")
def merchant_chat(workspace_id: str, body: Question, session: Session = Depends(db_session)) -> dict:
    if session.get(m.Workspace, workspace_id) is None:
        raise HTTPException(404, "workspace not found")
    return merchant.ask(session, get_llm(), workspace_id, body.question)


@router.get("/workspaces/{workspace_id}/merchant-context")
def merchant_context(workspace_id: str, session: Session = Depends(db_session)) -> dict:
    if session.get(m.Workspace, workspace_id) is None:
        raise HTTPException(404, "workspace not found")
    return merchant.context(session, workspace_id)


@router.get("/workspaces/{workspace_id}/audit")
def audit(workspace_id: str, limit: int = 20, session: Session = Depends(db_session)) -> list[dict]:
    rows = session.execute(
        select(m.RulingRecord)
        .where(m.RulingRecord.workspace_id == workspace_id)
        .order_by(m.RulingRecord.created_at.desc())
        .limit(limit)
    ).scalars()
    return [
        {
            "id": str(r.id),
            "session_id": str(r.session_id),
            "category_key": r.category_key,
            "procedure": r.procedure_version,
            "rule_pack_version": r.rule_pack_version,
            "decision": (r.ruling or {}).get("decision"),
            "remedy": (r.ruling or {}).get("remedy"),
            "amount": (r.ruling or {}).get("amount"),
            "authorization": (r.authorization or {}).get("decision") if r.authorization else None,
            "supervisor": (r.supervisor or {}).get("approved"),
            "mode": r.mode,
            "audit_hash": r.audit_hash,
            "at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/workspaces/{workspace_id}/examples")
def examples(workspace_id: str, n: int = 6, session: Session = Depends(db_session)) -> list[dict]:
    """Sample opening messages (from the eval set) so the demo can be driven by clicks."""
    rows = (
        session.execute(
            select(m.EvalScenario)
            .where(m.EvalScenario.workspace_id == workspace_id, m.EvalScenario.split == "test")
            .order_by(m.EvalScenario.scenario_id)
            .limit(60)
        )
        .scalars()
        .all()
    )
    seen: set[str] = set()
    out = []
    for r in rows:
        if r.category_key in seen:
            continue
        seen.add(r.category_key)
        text = r.ticket_text.split("\n\n", 1)[-1]
        out.append({"category_key": r.category_key, "text": text, "expected": r.expected})
        if len(out) >= n:
            break
    return out
