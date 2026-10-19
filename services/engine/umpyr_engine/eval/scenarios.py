"""Eval scenarios: ~1,000 per client, generated from the compiled rules.

Facts are sampled by the same scenario templates as the synthetic tickets (different seed), the expected ruling is
computed by the rule engine (so gold labels are policy-consistent by construction), and Haiku writes only the
customer's opening message. The replay harness (M5) runs the pipeline on the message + order lookup and compares.
"""

from __future__ import annotations

import json
import logging
import random
from collections import Counter
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session
from umpyr_rules import RulePack

from ..db.models import EvalScenario
from ..llm import LLMProvider
from ..llm.helpers import json_request, llm_json_batch

log = logging.getLogger(__name__)

MSG_SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"}, "message": {"type": "string"}},
    "required": ["subject", "message"],
    "additionalProperties": False,
}
SYSTEM = """You write the opening customer message of a support ticket for an evaluation dataset. Write 2-6 sentences in
a natural, varied voice. The message must state every fact listed under `must_mention` in the customer's own words
(e.g. order number, what happened, when, whether photos/receipt are available, what they want). Do not mention facts
that are not given. Sign with the given name. Plain text, no markdown."""


def _must_mention(sc) -> list[str]:
    f = sc.facts
    claim = f.get("claim", {})
    items = [f"reference {sc.order_id}", sc.issue_brief, sc.customer_asks]
    for k, label in (
        ("has_photos", "photos"),
        ("has_receipt", "receipt / proof of purchase"),
        ("item_unopened", "item still unopened"),
        ("item_used", "item used"),
    ):
        if k in claim:
            items.append(f"{label}: {'yes' if claim[k] else 'no'}")
    if "reported_within_days" in claim:
        items.append(f"noticed/reported {claim['reported_within_days']} days after delivery/arrival")
    return items


def generate_eval(
    session: Session,
    llm: LLMProvider,
    workspace_id: str,
    pack: RulePack,
    *,
    n: int = 1000,
    seed: int = 123,
    write_text: bool = True,
) -> dict[str, Any]:
    if workspace_id == "rockler":
        from ..sandbox.synth import rockler as gen
    else:
        from ..sandbox.synth import alaska as gen

    _records, scenarios, _names = gen.generate(pack, n, seed=seed)
    rng = random.Random(seed)
    plans = [
        {"customer_name": sc.person.name, "merchant": workspace_id, "must_mention": _must_mention(sc)}
        for sc in scenarios
    ]
    texts: list[dict | None]
    if write_text:
        reqs = [
            json_request(
                step="eval.scenario_text",
                system=SYSTEM,
                user=json.dumps(p, indent=1),
                schema=MSG_SCHEMA,
                max_tokens=400,
                workspace_id=workspace_id,
            )
            for p in plans
        ]
        texts = llm_json_batch(llm, reqs)
    else:
        texts = [
            {"subject": sc.issue_brief[:60], "message": " ".join(p["must_mention"])}
            for sc, p in zip(scenarios, plans, strict=True)
        ]

    session.execute(delete(EvalScenario).where(EvalScenario.workspace_id == workspace_id))
    rows = []
    for i, (sc, t) in enumerate(zip(scenarios, texts, strict=True)):
        if not t:
            continue
        split = "train" if rng.random() < 0.8 else "test"
        expected = {
            "decision": sc.policy["decision"],
            "remedy": sc.policy["remedy"],
            "amount": sc.policy["amount"],
            "fault": sc.policy["fault"],
            "rule_id": sc.policy["rule_id"],
            "clause_ids": sc.policy["clause_ids"],
            "missing_facts": sc.policy["missing_facts"],
            "category_key": sc.category_key,
            "claim_reason": sc.claim_reason,
        }
        row = {
            "scenario_id": f"{workspace_id}-ev-{i:04d}",
            "category_key": sc.category_key,
            "source_rule_id": sc.policy["rule_id"],
            "ticket_text": f"Subject: {t['subject']}\n\n{t['message']}",
            "facts": json.loads(json.dumps(sc.facts, default=str)),
            "expected": expected,
            "split": split,
            "order_id": sc.order_id,
            "customer_name": sc.person.name,
        }
        rows.append(row)
        session.add(
            EvalScenario(
                workspace_id=workspace_id,
                scenario_id=row["scenario_id"],
                category_key=sc.category_key,
                source_rule_id=row["source_rule_id"],
                ticket_text=row["ticket_text"],
                facts=row["facts"],
                expected=expected,
                split=split,
            )
        )
    session.commit()
    rule_cov = Counter(r["source_rule_id"] for r in rows)
    return {
        "scenarios": len(rows),
        "by_decision": dict(Counter(r["expected"]["decision"] for r in rows)),
        "by_category": dict(Counter(r["category_key"] for r in rows)),
        "rule_coverage": dict(rule_cov),
        "rules_never_fired": sorted({r.rule_id for r in pack.rules} - set(rule_cov)),
        "rows": rows,
        "records": _records,
    }
