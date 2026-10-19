"""Procedure mining: per category, process-mine staff action sequences -> common path -> Haiku drafts a procedure
-> clauses bound to steps -> (sandbox) auto-approved.

Common path = actions present in >= 60% of the category's cases, ordered by their median relative position.
Conditional steps = actions in 20-60% of cases (branches). A close-out placeholder step is always appended.
"""

from __future__ import annotations

import logging
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from umpyr_ontology import validate_path

from ..db.models import Case, Category, Procedure, RulePack
from ..llm import LLMProvider
from ..llm.helpers import json_request, llm_json

log = logging.getLogger(__name__)

STEP_KINDS = ["gather", "verify", "check", "decide", "act", "closeout"]
TOOLS = [
    "classify",
    "find_neighbours",
    "get_procedure",
    "lookup_order",
    "extract_facts",
    "rule",
    "authorize",
    "draft_reply",
    "none",
]

PROC_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string", "enum": STEP_KINDS},
                    "instruction": {"type": "string"},
                    "required_facts": {"type": "array", "items": {"type": "string"}},
                    "clause_ids": {"type": "array", "items": {"type": "string"}},
                    "tool": {"type": "string", "enum": TOOLS},
                    "staff_action": {
                        "type": ["string", "null"],
                        "description": "mined action this step corresponds to",
                    },
                },
                "required": ["name", "kind", "instruction", "required_facts", "clause_ids", "tool", "staff_action"],
                "additionalProperties": False,
            },
        },
        "outcomes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "condition": {"type": "string"},
                    "decision": {"type": "string"},
                    "remedy": {"type": "string"},
                    "clause_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["condition", "decision", "remedy", "clause_ids"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["name", "steps", "outcomes"],
    "additionalProperties": False,
}

SYSTEM = """You draft a resolution PROCEDURE for one category of customer disputes, for Umpyr's rule-based engine.
A procedure is an ordered list of steps a case goes through. Steps are of kind gather (ask the customer for a fact),
verify (look up merchant data), check (evaluate policy), decide (the deterministic ruling), act (execute the remedy
after authorization) or closeout. Each step names the runtime tool it uses and the ontology facts it needs.

Ground the procedure in (a) the observed common path of staff actions and (b) the policy clauses provided. Bind each
check/decide/act step to the clause ids that govern it. Use ONLY the clause ids and the fact paths given. Outcomes list
the possible rulings with the clause ids that produce them. Keep it to 4-8 steps; do not include a close-out step
(the system appends one)."""


def _common_path(sequences: list[list[str]]) -> tuple[list[str], list[str], dict[str, float]]:
    n = len(sequences) or 1
    presence: Counter[str] = Counter()
    positions: dict[str, list[float]] = defaultdict(list)
    for seq in sequences:
        seen = set()
        for i, a in enumerate(seq):
            positions[a].append(i / max(1, len(seq) - 1) if len(seq) > 1 else 0.0)
            if a not in seen:
                presence[a] += 1
                seen.add(a)
    freq = {a: c / n for a, c in presence.items()}
    common = sorted([a for a, f in freq.items() if f >= 0.6], key=lambda a: statistics.median(positions[a]))
    conditional = sorted([a for a, f in freq.items() if 0.2 <= f < 0.6], key=lambda a: statistics.median(positions[a]))
    return common, conditional, freq


def mine_procedures(
    session: Session, llm: LLMProvider, workspace_id: str, *, approve: bool = True
) -> list[dict[str, Any]]:
    cats = list(
        session.execute(
            select(Category).where(Category.workspace_id == workspace_id, Category.status == "approved")
        ).scalars()
    )
    pack_row = (
        session.execute(select(RulePack).where(RulePack.workspace_id == workspace_id, RulePack.status == "active"))
        .scalars()
        .first()
    )
    pack = pack_row.pack if pack_row else {"clauses": [], "rules": []}
    clauses = {c["clause_id"]: c for c in pack["clauses"]}
    rules = pack["rules"]
    session.execute(delete(Procedure).where(Procedure.workspace_id == workspace_id))
    out = []
    for cat in cats:
        cases = list(
            session.execute(
                select(Case).where(Case.workspace_id == workspace_id, Case.category_key == cat.key)
            ).scalars()
        )
        sequences = [[a["action"] for a in (c.record or {}).get("staff_actions", [])] for c in cases]
        common, conditional, freq = _common_path(sequences)
        reasons = Counter((c.facts or {}).get("claim", {}).get("reason") for c in cases)
        top_reasons = [r for r, _ in reasons.most_common(3) if r]
        outcomes = Counter(
            f"{(c.record or {}).get('outcome', {}).get('decision')}/{(c.record or {}).get('outcome', {}).get('remedy')}"
            for c in cases
        )
        rel_rules = [
            r
            for r in rules
            if r.get("enabled", True) and (not r["claim_reasons"] or set(r["claim_reasons"]) & set(top_reasons))
        ]
        rel_clause_ids = sorted({r["clause_id"] for r in rel_rules})[:15]
        required = sorted({p for r in rel_rules for p in r.get("requires", [])})
        clause_text = "\n".join(
            f"- {cid}: {clauses[cid]['title']} - {clauses[cid]['text'][:300]}"
            for cid in rel_clause_ids
            if cid in clauses
        )
        user = (
            f"Category: {cat.name} ({cat.key}) - {cat.description}\nCases: {len(cases)}; claim reasons: {dict(reasons)}\n"
            f"Observed common path (>=60% of cases): {common}\nConditional actions (20-60%): {conditional}\n"
            f"Action frequencies: { {k: round(v, 2) for k, v in freq.items()} }\nObserved outcomes: {dict(outcomes)}\n"
            f"Facts the governing rules need: {required}\n\nGoverning clauses:\n{clause_text or '(none compiled for these reasons)'}"
        )
        res = (
            llm_json(
                llm,
                json_request(
                    step="procedures.draft",
                    system=SYSTEM,
                    user=user,
                    schema=PROC_SCHEMA,
                    max_tokens=2500,
                    workspace_id=workspace_id,
                ),
            )
            or {}
        )
        steps = []
        for i, s in enumerate(res.get("steps", []), start=1):
            steps.append(
                {
                    "step_id": f"{cat.key}.s{i}",
                    "name": s["name"],
                    "kind": s["kind"] if s["kind"] in STEP_KINDS else "check",
                    "instruction": s["instruction"],
                    "required_facts": [p for p in s.get("required_facts", []) if validate_path(p)],
                    "clause_ids": [c for c in s.get("clause_ids", []) if c in clauses],
                    "tool": s["tool"] if s["tool"] in TOOLS else "none",
                    "staff_action": s.get("staff_action"),
                }
            )
        steps.append(
            {
                "step_id": f"{cat.key}.closeout",
                "name": "Close-out (placeholder)",
                "kind": "closeout",
                "instruction": "After the customer is resolved: disposition of the item (restock / open-box / return to vendor / carrier claim) for retail; recovery within policy for airlines. Designed, not implemented, in the POC.",
                "required_facts": [],
                "clause_ids": [],
                "tool": "none",
                "staff_action": None,
            }
        )
        proc = Procedure(
            workspace_id=workspace_id,
            category_key=cat.key,
            version=1,
            name=res.get("name") or f"{cat.name} procedure",
            steps=steps,
            required_facts=sorted({p for s in steps for p in s["required_facts"]} | set(required)),
            outcomes=[
                {**o, "clause_ids": [c for c in o.get("clause_ids", []) if c in clauses]}
                for o in res.get("outcomes", [])
            ],
            mining={
                "common_path": common,
                "conditional": conditional,
                "frequencies": freq,
                "cases": len(cases),
                "outcomes": dict(outcomes),
                "claim_reasons": dict(reasons),
            },
            status="approved" if approve else "draft",
            approved_by="sandbox-auto" if approve else None,
            approved_at=datetime.now(UTC) if approve else None,
        )
        session.add(proc)
        out.append(
            {
                "category": cat.key,
                "name": proc.name,
                "steps": [s["name"] for s in steps],
                "common_path": common,
                "clauses": rel_clause_ids,
            }
        )
    session.commit()
    return out


def dump_procedures(session: Session, workspace_id: str) -> list[dict[str, Any]]:
    return [
        {
            "category_key": p.category_key,
            "version": p.version,
            "name": p.name,
            "steps": p.steps,
            "required_facts": p.required_facts,
            "outcomes": p.outcomes,
            "mining": p.mining,
            "status": p.status,
        }
        for p in session.execute(select(Procedure).where(Procedure.workspace_id == workspace_id)).scalars()
    ]


def load_cached_procedures(session: Session, workspace_id: str, rows: list[dict[str, Any]]) -> int:
    session.execute(delete(Procedure).where(Procedure.workspace_id == workspace_id))
    for r in rows:
        session.add(
            Procedure(
                workspace_id=workspace_id,
                category_key=r["category_key"],
                version=r.get("version", 1),
                name=r["name"],
                steps=r["steps"],
                required_facts=r.get("required_facts", []),
                outcomes=r.get("outcomes", []),
                mining=r.get("mining", {}),
                status=r.get("status", "approved"),
                approved_by="sandbox-auto",
                approved_at=datetime.now(UTC),
            )
        )
    session.commit()
    return len(rows)
