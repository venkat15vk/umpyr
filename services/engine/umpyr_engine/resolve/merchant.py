"""Merchant chat: questions about the merchant's own onboarding data ("what drifts most?", "top categories by pain").

Aggregates are computed in code and handed to Haiku as context; the model only phrases the answer and never sees
raw tickets.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import models as m
from ..llm import LLMProvider
from ..llm.helpers import json_request, llm_json

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}
SYSTEM = """You answer a merchant's questions about their own dispute data using ONLY the JSON context provided (categories
with volume, average hours and pain score; drift findings; unwritten-rule candidates; rule pack; router metrics).
Be specific with numbers, cite category keys and rule/clause ids, and say when the context does not contain the answer.
Plain text, 2-8 sentences, no markdown tables."""


def context(session: Session, workspace_id: str) -> dict[str, Any]:
    cats = [
        {
            "key": c.key,
            "name": c.name,
            "volume": c.volume,
            "avg_hours": float(c.avg_hours_to_resolve or 0),
            "pain_score": float(c.pain_score or 0),
        }
        for c in session.execute(
            select(m.Category).where(m.Category.workspace_id == workspace_id).order_by(m.Category.pain_score.desc())
        ).scalars()
    ]
    findings = list(
        session.execute(select(m.DriftFinding).where(m.DriftFinding.workspace_id == workspace_id)).scalars()
    )
    by_cat = Counter(f.category_key for f in findings if f.kind == "drift")
    by_rule = Counter(f.rule_id for f in findings if f.kind == "drift" and f.rule_id)
    patterns = Counter((f.pattern or "")[:90] for f in findings if f.kind == "drift")
    pack = (
        session.execute(
            select(m.RulePack).where(m.RulePack.workspace_id == workspace_id, m.RulePack.status == "active")
        )
        .scalars()
        .first()
    )
    router = (
        session.execute(
            select(m.RouterModel).where(m.RouterModel.workspace_id == workspace_id, m.RouterModel.status == "active")
        )
        .scalars()
        .first()
    )
    n_cases = session.execute(
        select(func.count()).select_from(m.Case).where(m.Case.workspace_id == workspace_id)
    ).scalar_one()
    rulings = session.execute(select(m.RulingRecord.ruling).where(m.RulingRecord.workspace_id == workspace_id)).all()
    live = Counter((r[0] or {}).get("decision") for r in rulings)
    return {
        "cases": n_cases,
        "categories": cats,
        "drift": {
            "total": sum(by_cat.values()),
            "by_category": dict(by_cat.most_common()),
            "by_rule": dict(by_rule.most_common(10)),
            "top_patterns": dict(patterns.most_common(8)),
        },
        "unwritten_rules": [f.pattern for f in findings if f.kind == "unwritten_rule"],
        "rule_pack": {"version": pack.version, "rules": pack.rule_count, "conflicts": pack.conflicts} if pack else None,
        "router": router.metrics if router else None,
        "live_rulings": dict(live),
    }


def ask(session: Session, llm: LLMProvider, workspace_id: str, question: str) -> dict[str, Any]:
    ctx = context(session, workspace_id)
    res = (
        llm_json(
            llm,
            json_request(
                step="merchant.chat",
                system=SYSTEM,
                user=f"Context:\n{json.dumps(ctx, default=str)}\n\nQuestion: {question}",
                schema=ANSWER_SCHEMA,
                max_tokens=600,
                workspace_id=workspace_id,
            ),
        )
        or {}
    )
    return {
        "answer": res.get("answer") or "I could not produce an answer from the available data.",
        "context_keys": list(ctx),
    }
