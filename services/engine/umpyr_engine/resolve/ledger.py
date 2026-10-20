"""Case object + append-only hash-chained ledger (design v2 §3, §15).

    hash = sha256(canonical({case_id, seq, prev_hash, turn, actor, action, basis, result, cost_usd, at}))
    decision_hash = sha256(head_hash || ruling_audit_hash)

`verify()` recomputes the chain from the stored rows; any edit breaks it at the first altered event.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from umpyr_rules import audit_hash

from ..db import models as m

ACTORS = ("customer", "umpyr", "merchant_reviewer", "merchant_system", "check")


def _jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=str))


def event_hash(
    *,
    case_id: str,
    seq: int,
    prev_hash: str | None,
    turn: int,
    actor: str,
    action: str,
    basis: dict[str, Any],
    result: dict[str, Any],
    cost_usd: str,
    at: str,
) -> str:
    return audit_hash(
        {
            "case_id": case_id,
            "seq": seq,
            "prev_hash": prev_hash,
            "turn": turn,
            "actor": actor,
            "action": action,
            "basis": basis,
            "result": result,
            "cost_usd": cost_usd,
            "at": at,
        }
    )


class CaseLedger:
    def __init__(self, session: Session, case: m.ResolveCase):
        self.session = session
        self.case = case
        self.turn = int(case.turns or 0)

    # ---- lifecycle ---------------------------------------------------------
    @classmethod
    def open(cls, session: Session, chat: m.ChatSession) -> CaseLedger:
        """Get or create the case behind a chat session. Creating one appends `case.opened`."""
        case = session.execute(select(m.ResolveCase).where(m.ResolveCase.session_id == chat.id)).scalar_one_or_none()
        created = case is None
        if created:
            case = m.ResolveCase(workspace_id=chat.workspace_id, session_id=chat.id, state={"executed": []})
            session.add(case)
            session.flush()
        ledger = cls(session, case)
        if created:
            ledger.append(
                "umpyr",
                "case.opened",
                basis={"channel": chat.channel, "mode": chat.mode, "auto_approve_limit": str(chat.auto_approve_limit)},
                result={"session_id": str(chat.id)},
            )
        return ledger

    def begin_turn(self) -> int:
        self.turn = int(self.case.turns or 0) + 1
        self.case.turns = self.turn
        return self.turn

    # ---- append ------------------------------------------------------------
    def append(
        self,
        actor: str,
        action: str,
        *,
        basis: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        cost_usd: Decimal | float | int = 0,
        llm_calls: int = 0,
    ) -> m.CaseEvent:
        if actor not in ACTORS:
            raise ValueError(f"unknown actor {actor!r}")
        seq = int(self.case.event_count or 0) + 1
        at = datetime.now(UTC)
        basis_j = _jsonable(basis or {})
        result_j = _jsonable(result or {})
        cost = Decimal(str(cost_usd)).quantize(Decimal("0.000001"))
        h = event_hash(
            case_id=str(self.case.id),
            seq=seq,
            prev_hash=self.case.head_hash,
            turn=self.turn,
            actor=actor,
            action=action,
            basis=basis_j,
            result=result_j,
            cost_usd=format(cost, "f"),
            at=at.astimezone(UTC).isoformat(),
        )
        ev = m.CaseEvent(
            case_id=self.case.id,
            workspace_id=self.case.workspace_id,
            seq=seq,
            turn=self.turn,
            actor=actor,
            action=action,
            basis=basis_j,
            result=result_j,
            cost_usd=cost,
            at=at,
            prev_hash=self.case.head_hash,
            hash=h,
        )
        self.session.add(ev)
        self.case.event_count = seq
        self.case.head_hash = h
        self.case.cost_usd = (Decimal(self.case.cost_usd or 0) + cost).quantize(Decimal("0.000001"))
        self.case.llm_calls = int(self.case.llm_calls or 0) + int(llm_calls)
        self.session.add(self.case)
        self.session.flush()
        return ev

    def ensure_once(self, step_key: str, idempotency_key: str | None = None, result_ref: str | None = None) -> bool:
        """Record an executed step; False if the same idempotency key was already executed (never fire twice)."""
        key = idempotency_key or f"{self.case.id}:{step_key}"
        state = dict(self.case.state or {})
        executed = list(state.get("executed") or [])
        if any(e.get("idempotency_key") == key for e in executed):
            return False
        executed.append(
            {
                "step_key": step_key,
                "idempotency_key": key,
                "turn": self.turn,
                "at": datetime.now(UTC).isoformat(),
                "result_ref": result_ref,
            }
        )
        state["executed"] = executed
        self.case.state = state
        self.session.add(self.case)
        return True

    def commit_decision(self, ruling_hash: str) -> str:
        """Decision hash commitment on top of the chain: sha256(head_hash || ruling_hash)."""
        d = hashlib.sha256(f"{self.case.head_hash or ''}{ruling_hash}".encode()).hexdigest()
        self.case.decision_hash = d
        self.session.add(self.case)
        return d

    # ---- verify ------------------------------------------------------------
    @staticmethod
    def verify(session: Session, case_id: uuid.UUID) -> dict[str, Any]:
        case = session.get(m.ResolveCase, case_id)
        if case is None:
            return {"ok": False, "error": "case not found"}
        rows = (
            session.execute(select(m.CaseEvent).where(m.CaseEvent.case_id == case_id).order_by(m.CaseEvent.seq))
            .scalars()
            .all()
        )
        prev: str | None = None
        broken_at: int | None = None
        for ev in rows:
            expected = event_hash(
                case_id=str(case_id),
                seq=ev.seq,
                prev_hash=prev,
                turn=ev.turn,
                actor=ev.actor,
                action=ev.action,
                basis=ev.basis or {},
                result=ev.result or {},
                cost_usd=format(Decimal(ev.cost_usd or 0).quantize(Decimal("0.000001")), "f"),
                at=ev.at.astimezone(UTC).isoformat(),
            )
            if ev.prev_hash != prev or ev.hash != expected:
                broken_at = ev.seq
                break
            prev = ev.hash
        head_ok = broken_at is None and prev == case.head_hash and len(rows) == int(case.event_count or 0)
        return {
            "ok": head_ok,
            "events": len(rows),
            "head_hash": case.head_hash,
            "decision_hash": case.decision_hash,
            "broken_at": broken_at,
            "recomputed_head": prev,
        }
