"""Customer experience (design v2 §5, §12, §14 and the queued-customer item of §11).

Everything here is deterministic over history, the rule pack and the ruling; no model call:

- next-question prediction: follow-up customer messages in historical threads are classified by pattern into a
  small question vocabulary; per category this gives P(question) which the runtime uses to prefetch and answer
  proactively (refund timeline first).
- refund timeline: the public clause that states the refund window (e.g. "seven business days") is located in
  the active rule pack and cited; nothing is generated.
- refund options: when more than one approving rule fired with different money remedies, the customer may choose.
  Where the winning rule is regulation and pays cash, cash is listed first and marked guaranteed; alternatives are
  offered, never defaulted. A choice re-binds the ruling to the chosen rule's effect.
- queued customer: a pending message always; when the merchant configured `approval_sla_hours`, the customer is
  told the window and an overdue queue escalates with an honest notice.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from umpyr_rules import RulePack, Ruling
from umpyr_rules.jsonlogic import apply as jl_apply

from ..compile.provenance import citation as _citation
from ..db import models as m

# ---------------------------------------------------------------- next questions
QUESTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "timeline": re.compile(
        r"\b(when|how long|how many days|how soon|eta|arrive|land|show up|hit my|be back on|processed by)\b", re.I
    ),
    "status": re.compile(
        r"\b(status|any update|any news|still waiting|has it been|haven't (heard|received)|not received)\b", re.I
    ),
    "how_to_return": re.compile(
        r"\b(label|ship (it )?back|send (it )?back|drop (it )?off|return (it|the item)|packag)\w*", re.I
    ),
    "confirmation": re.compile(r"\b(confirmation|receipt|email me|in writing|reference number)\b", re.I),
    "amount": re.compile(r"\b(how much|full amount|the rest|remaining|partial|why only)\b", re.I),
    "escalate": re.compile(r"\b(supervisor|manager|complaint|escalat|unacceptable|lawyer|dispute)\w*", re.I),
}
QUESTION_LABEL = {
    "timeline": "when the money or item will arrive",
    "status": "whether anything is still pending",
    "how_to_return": "how to send the item back",
    "confirmation": "written confirmation",
    "amount": "why the amount is what it is",
    "escalate": "speaking to a person",
}


def classify_question(text: str) -> list[str]:
    return [q for q, pat in QUESTION_PATTERNS.items() if pat.search(text or "")]


def mine_next_questions(session: Session, workspace_id: str) -> list[dict[str, Any]]:
    """P(next question | category) from the customer follow-ups in historical threads. Replaces the stored rows."""
    cats = {
        c.ticket_id: c.category_key
        for c in session.execute(select(m.Case).where(m.Case.workspace_id == workspace_id)).scalars()
    }
    counts: dict[str, dict[str, int]] = {}
    tickets_per_cat: dict[str, int] = {}
    for t in session.execute(select(m.Ticket).where(m.Ticket.workspace_id == workspace_id)).scalars():
        cat = cats.get(t.ticket_id)
        if not cat:
            continue
        tickets_per_cat[cat] = tickets_per_cat.get(cat, 0) + 1
        msgs = [x for x in (t.thread or []) if isinstance(x, dict) and x.get("role") == "customer"]
        seen: set[str] = set()
        for x in msgs[1:]:  # follow-ups only
            for q in classify_question(x.get("text") or ""):
                seen.add(q)
        for q in seen:
            counts.setdefault(cat, {})[q] = counts.setdefault(cat, {}).get(q, 0) + 1
    session.query(m.NextQuestion).filter(m.NextQuestion.workspace_id == workspace_id).delete()
    rows = []
    for cat, qs in counts.items():
        n = tickets_per_cat.get(cat, 0) or 1
        for q, c in qs.items():
            rows.append(
                m.NextQuestion(
                    workspace_id=workspace_id, category_key=cat, question=q, probability=round(c / n, 4), support=c
                )
            )
    session.add_all(rows)
    session.commit()
    return [
        {
            "category_key": r.category_key,
            "question": r.question,
            "probability": float(r.probability),
            "support": r.support,
        }
        for r in rows
    ]


def predict_next_questions(
    session: Session, workspace_id: str, category_key: str | None, k: int = 3
) -> list[dict[str, Any]]:
    if not category_key:
        return []
    rows = session.execute(
        select(m.NextQuestion)
        .where(m.NextQuestion.workspace_id == workspace_id, m.NextQuestion.category_key == category_key)
        .order_by(m.NextQuestion.probability.desc())
        .limit(k)
    ).scalars()
    return [
        {
            "question": r.question,
            "label": QUESTION_LABEL.get(r.question, r.question),
            "probability": float(r.probability),
            "support": r.support,
        }
        for r in rows
    ]


# ---------------------------------------------------------------- refund timeline (from the pack, cited)
_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "fourteen": 14, "fifteen": 15, "twenty": 20, "thirty": 30, "forty-five": 45, "sixty": 60, "ninety": 90,
}  # fmt: skip
_DAYS_RE = re.compile(
    r"\b(\d{1,3}|" + "|".join(_NUM_WORDS) + r")\s*(?:\(\d+\)\s*)?(business|calendar|working)?[\s-]*days?\b", re.I
)
_REFUND_RE = re.compile(r"\brefund", re.I)
_CARD_RE = re.compile(r"credit card|debit card|card|original (form|method) of payment", re.I)
_CASH_RE = re.compile(r"cash|check|cheque|other (forms?|methods?) of payment", re.I)


def _days(match: re.Match[str]) -> int:
    n = match.group(1).lower()
    return int(n) if n.isdigit() else _NUM_WORDS[n]


def timeline_answer(pack: RulePack | None, payment_method: str | None) -> dict[str, Any] | None:
    """Locate the public clause that states the refund window and turn it into a templated, cited sentence."""
    if pack is None:
        return None
    is_card = (payment_method or "card").lower() in (
        "card",
        "credit_card",
        "debit_card",
        "credit",
        "debit",
        "visa",
        "mastercard",
        "amex",
    )
    best: tuple[int, int, Any, int, str] | None = None  # (tier_rank, days, clause, days, kind)
    tier_rank = {"law": 0, "contract": 1, "commitment": 2, "goodwill": 3}
    for cl in pack.clauses:
        prov = cl.provenance
        if prov is not None and not prov.public:
            continue
        text = cl.text or ""
        if not _REFUND_RE.search(text):
            continue
        for mt in _DAYS_RE.finditer(text):
            # context = the sentence/clause segment holding the match, so "card ... 7 days; cash ... 20 days" splits
            seg_start = max(text.rfind(ch, 0, mt.start()) for ch in ".;:\n") + 1
            seg_end_candidates = [i for i in (text.find(ch, mt.end()) for ch in ".;:\n") if i != -1]
            seg_end = min(seg_end_candidates) if seg_end_candidates else len(text)
            window = text[seg_start:seg_end]
            card_ctx = bool(_CARD_RE.search(window))
            cash_ctx = bool(_CASH_RE.search(window))
            if card_ctx and not cash_ctx and not is_card:
                continue  # a card-only window does not apply to a cash/check payment
            if cash_ctx and not card_ctx and is_card:
                continue  # a cash-only window does not apply to a card payment
            kind = (mt.group(2) or "calendar").lower()
            d = _days(mt)
            if not 1 <= d <= 90:
                continue
            key = (tier_rank.get(str(cl.tier), 9), d)
            if best is None or key < best[:2]:
                best = (key[0], key[1], cl, d, kind)
    if best is None:
        return None
    _, _, cl, d, kind = best
    cit = _citation(cl)
    where = cit["document"] or "the policy"
    method = "your original card" if is_card else "your original form of payment"
    answer = f"Refunds to {method} are issued within {d} {kind} days of the request under {where}."
    return {"question": "timeline", "answer": answer, "days": d, "day_kind": kind, "citation": cit}


# ---------------------------------------------------------------- refund options
MONEY_REMEDIES = ("refund", "partial_refund", "store_credit", "travel_credit", "points", "voucher")
REMEDY_LABEL = {
    "refund": "refund to the original form of payment",
    "partial_refund": "partial refund to the original form of payment",
    "store_credit": "store credit",
    "travel_credit": "travel credit",
    "points": "loyalty points",
    "voucher": "voucher",
}
CASH = ("refund", "partial_refund")


def refund_options(ruling: Ruling | None, pack: RulePack | None) -> list[dict[str, Any]]:
    """Distinct money remedies offered by fired approving rules; cash first when regulation guarantees it.
    Empty unless the customer has a real choice (two or more remedies)."""
    if ruling is None or str(ruling.decision) not in ("approve", "partial") or ruling.remedy not in MONEY_REMEDIES:
        return []
    opts: dict[str, dict[str, Any]] = {}
    for c in ruling.checks:
        if not c.fired or c.effect is None or str(c.effect.decision) not in ("approve", "partial"):
            continue
        rem = str(c.effect.remedy) if c.effect.remedy else None
        if rem not in MONEY_REMEDIES or rem in opts:
            continue
        cl = pack.clause(c.clause_id) if pack else None
        opts[rem] = {
            "remedy": rem,
            "label": REMEDY_LABEL[rem],
            "amount": str(c.amount)
            if c.amount is not None
            else (str(ruling.amount) if rem == ruling.remedy and ruling.amount is not None else None),
            "rule_id": c.rule_id,
            "clause_id": c.clause_id,
            "tier": str(c.tier),
            "guaranteed_by_regulation": str(c.tier) == "law" and rem in CASH,
            "is_cash": rem in CASH,
            "citation": _citation(cl) if cl else None,
        }
    if len(opts) < 2:
        return []
    ordered = sorted(
        opts.values(), key=lambda o: (not o["guaranteed_by_regulation"], not o["is_cash"], o["remedy"] != ruling.remedy)
    )
    return ordered


def apply_choice(ruling: Ruling, options: list[dict[str, Any]], chosen_remedy: str, facts: dict[str, Any]) -> Ruling:
    """Re-bind the ruling to the chosen option's rule (remedy, amount, winning ids). The decision is unchanged."""
    opt = next((o for o in options if o["remedy"] == chosen_remedy), None)
    if opt is None or chosen_remedy == ruling.remedy:
        return ruling
    chk = next((c for c in ruling.checks if c.rule_id == opt["rule_id"]), None)
    amount = chk.amount if chk and chk.amount is not None else None
    if amount is None and chk and chk.effect and chk.effect.amount is not None:
        try:
            v = jl_apply(chk.effect.amount, facts)
            amount = Decimal(str(v)) if v is not None else None
        except Exception:
            amount = None
    return ruling.model_copy(
        update={
            "remedy": chosen_remedy,
            "amount": amount if amount is not None else ruling.amount,
            "winning_rule_id": opt["rule_id"],
            "winning_clause_id": opt["clause_id"],
            "tier": opt["tier"],
            "explanation": f"{ruling.explanation} The customer chose {REMEDY_LABEL[chosen_remedy]} from the offered options.".strip(),
        }
    )


def detect_choice(options: list[dict[str, Any]], extracted: dict[str, Any], text: str) -> str | None:
    """Which offered option the customer picked in this message, if any (extraction first, then plain words)."""
    if not options:
        return None
    offered = {o["remedy"] for o in options}
    claim = extracted.get("claim") or {}
    rem = claim.get("requested_remedy")
    if rem in offered:
        return rem
    if claim.get("wants_credit_instead_of_refund") is True:
        for r in ("travel_credit", "store_credit", "voucher", "points"):
            if r in offered:
                return r
    if claim.get("wants_credit_instead_of_refund") is False and "refund" in offered:
        return "refund"
    t = (text or "").lower()
    words = {
        "travel_credit": ("travel credit", "flight credit"),
        "store_credit": ("store credit", "gift card"),
        "points": ("points", "miles"),
        "voucher": ("voucher",),
        "refund": ("refund", "original payment", "my card", "cash"),
    }
    for r in options:  # offered order = preference order, cash first
        if any(w in t for w in words.get(r["remedy"], ())):
            return r["remedy"]
    return None


# ---------------------------------------------------------------- queued customer
def queue_state(prev: dict[str, Any] | None, sla_hours: float | None, now: datetime) -> dict[str, Any]:
    """Open (or keep) the approval queue entry for a case that is pending approval."""
    if prev and prev.get("queued_at"):
        q = dict(prev)
    else:
        q = {"queued_at": now.isoformat(), "sla_hours": sla_hours, "due_at": None}
        if sla_hours:
            q["due_at"] = (now + timedelta(hours=float(sla_hours))).isoformat()
    q["overdue"] = bool(q.get("due_at") and now > datetime.fromisoformat(q["due_at"]))
    return q


def is_overdue(prev: dict[str, Any] | None, now: datetime) -> bool:
    return bool(prev and prev.get("due_at") and now > datetime.fromisoformat(prev["due_at"]))


def sla_sentence(sla_hours: float | None) -> str | None:
    if not sla_hours:
        return None
    h = float(sla_hours)
    if h < 1:
        return f"within {int(round(h * 60))} minutes"
    if h == int(h):
        return f"within {int(h)} hour{'s' if h != 1 else ''}"
    return f"within {h:g} hours"


def now_utc() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- status strip
STATUS_PAST = {
    "message.received": "Received your message",
    "record.lookup": "Checked your {record_word}",
    "record.missing": "Could not match a {record_word} yet",
    "precedent.retrieved": "Compared with similar cases",
    "rule_check": "Reviewed what the policy provides",
    "options.review": "Reviewed refund options",
    "gate.queued": "Sent for authorisation",
    "gate.allowed": "Approved",
    "escalation": "Handed to a specialist",
    "needs_fact": "Need a couple more details",
    "queue.overdue": "Authorisation took longer than promised",
    "decision.resolved": "Done",
    "decision.denied": "Reviewed and explained below",
}


def status_lines(events: list[dict[str, Any]], kind: str) -> list[str]:
    rw = "booking" if kind == "booking" else "order"
    out: list[str] = []
    for e in events:
        tpl = STATUS_PAST.get(e.get("action", ""))
        if tpl:
            line = tpl.format(record_word=rw)
            if line not in out:
                out.append(line)
    return out


def public_citations(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Customer-safe subset: public documents only, no internal ids beyond the document and its location."""
    out = []
    for c in citations:
        if not c.get("public"):
            continue
        out.append(
            {
                "document": c.get("document"),
                "section": c.get("section"),
                "page": c.get("page"),
                "url": c.get("url"),
                "document_version": c.get("document_version"),
                "quote": (c.get("quote") or "")[:200],
            }
        )
    return out
