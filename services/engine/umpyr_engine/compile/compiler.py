"""Policy compiler: documents -> clauses -> typed rules -> versioned RulePack.

Steps
1. segment: each document is chunked (~10K chars at paragraph boundaries) and Haiku extracts atomic clauses
   with verbatim text, topic, relevance and ambiguity flags. Clause ids are assigned in code: <PREFIX>-<TOPIC>-<n>.
2. convert: every dispute-relevant clause is converted to zero or more rules (JSON Logic over ontology paths).
   Every rule is validated with umpyr_rules.Rule (unknown paths are rejected) and by evaluating `when` on empty
   facts (unknown operators are rejected). Invalid rules get one repair attempt with the validation error.
3. assemble: rules are merged into a RulePack with precedence tiers from the source manifest; potential conflicts
   (same claim reason, tier and priority, different decision) are listed for review.

Outputs: samples/<client>/clauses.json, rule_pack.json, compile_report.json, and DB rows.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from umpyr_rules import Clause as RClause
from umpyr_rules import Effect, JsonLogicError, Rule, RulePack, apply, collect_vars

from ..llm import LLMProvider, LLMRequest
from ..llm.helpers import json_request, llm_json, llm_json_batch, llm_json_concurrent
from .prompts import CLAUSE_SCHEMA, RULE_SCHEMA, SEGMENT_SYSTEM, TOPICS, rule_system_prompt, rule_user, segment_user

log = logging.getLogger(__name__)

CHUNK_CHARS = 10_000


def chunk_text(text: str, size: int = CHUNK_CHARS) -> list[str]:
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if len(cur) + len(p) + 2 > size and cur:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    # a single oversized paragraph (PDF text without blank lines) is split hard
    out: list[str] = []
    for c in chunks:
        while len(c) > size * 1.5:
            cut = c.rfind("\n", 0, size)
            cut = cut if cut > size // 2 else size
            out.append(c[:cut])
            c = c[cut:]
        out.append(c)
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def segment_documents(
    llm: LLMProvider, docs: list[dict], prefix: str, workspace_id: str, use_batch: bool = True
) -> list[dict]:
    """docs: [{source_id, title, url, tier, text}] -> clauses [{clause_id, ...}]"""
    reqs: list[LLMRequest] = []
    meta: list[tuple[dict, int]] = []
    for d in docs:
        chunks = chunk_text(d["text"])
        for i, ch in enumerate(chunks):
            reqs.append(
                json_request(
                    step="compile.segment",
                    system=SEGMENT_SYSTEM,
                    user=segment_user(d["title"], d["url"], d["tier"], i, len(chunks), ch),
                    schema=CLAUSE_SCHEMA,
                    max_tokens=6000,
                    workspace_id=workspace_id,
                )
            )
            meta.append((d, i))
    log.info("segmenting %d documents in %d chunks (batch=%s)", len(docs), len(reqs), use_batch)
    results = llm_json_batch(llm, reqs) if use_batch else llm_json_concurrent(llm, reqs)

    clauses: list[dict] = []
    seen: set[str] = set()
    counters: Counter[str] = Counter()
    for (d, i), res in zip(meta, results, strict=True):
        if not res:
            log.warning("no clauses returned for %s chunk %d", d["source_id"], i)
            continue
        for c in res.get("clauses", []):
            text = (c.get("text") or "").strip()
            if len(text) < 15:
                continue
            key = _norm(text)[:200]
            if key in seen:
                continue
            seen.add(key)
            topic = c.get("topic") if c.get("topic") in TOPICS else "GEN"
            counters[topic] += 1
            clauses.append(
                {
                    "clause_id": f"{prefix}-{topic}-{counters[topic]}",
                    "title": (c.get("title") or "")[:200],
                    "text": text,
                    "topic": topic,
                    "tier": d["tier"],
                    "source_id": d["source_id"],
                    "source_url": d["url"],
                    "source_title": d["title"],
                    "fetched_at": d.get("fetched_at"),
                    "relevant_to_disputes": bool(c.get("relevant_to_disputes")),
                    "ambiguous": bool(c.get("ambiguous")),
                    "ambiguity_note": c.get("ambiguity_note"),
                    "verbatim": _norm(text)[:120] in _norm(d["text"]),
                }
            )
    return clauses


def _logic(value: Any) -> Any:
    """Rule `when`/`amount` arrive as JSON-encoded strings (structured output needs concrete types)."""
    if isinstance(value, str):
        v = value.strip()
        if v == "" or v.lower() == "null":
            return None
        try:
            return json.loads(v)
        except json.JSONDecodeError as e:
            raise ValueError(f"not valid JSON logic: {v[:80]}") from e
    return value


def _build_rule(clause: dict, r: dict, idx: int) -> Rule:
    effect = Effect(
        decision=r["decision"],
        remedy=r.get("remedy"),
        amount=_logic(r.get("amount")),
        fault=r.get("fault"),
        note=(r.get("note") or "")[:300] or None,
    )
    reasons = [x for x in (r.get("claim_reasons") or []) if isinstance(x, str)]
    requires = [x for x in (r.get("requires") or []) if isinstance(x, str) and x != "claim.reason"]
    when = _logic(r.get("when"))
    if when is None:
        when = True
    rule = Rule(
        rule_id=f"{clause['clause_id']}.r{idx}",
        clause_id=clause["clause_id"],
        tier=clause["tier"],
        priority=int(r.get("priority") or 0),
        claim_reasons=reasons,
        requires=sorted(set(requires)),
        when=when,
        effect=effect,
        description=(r.get("description") or "")[:300],
    )
    apply(rule.when, {})  # raises JsonLogicError on unknown operators
    if rule.effect.amount is not None and isinstance(rule.effect.amount, dict):
        apply(rule.effect.amount, {})
    return rule


def convert_clauses(
    llm: LLMProvider, clauses: list[dict], workspace_id: str, use_batch: bool = True
) -> tuple[list[Rule], list[dict]]:
    system = rule_system_prompt()
    todo = [c for c in clauses if c["relevant_to_disputes"]]
    reqs = [
        json_request(
            step="compile.rules",
            system=system,
            user=rule_user(c, c["source_title"]),
            schema=RULE_SCHEMA,
            max_tokens=3000,
            workspace_id=workspace_id,
        )
        for c in todo
    ]
    log.info("converting %d relevant clauses to rules (batch=%s)", len(todo), use_batch)
    results = llm_json_batch(llm, reqs) if use_batch else llm_json_concurrent(llm, reqs)

    rules: list[Rule] = []
    report: list[dict] = []
    for clause, res in zip(todo, results, strict=True):
        entry = {"clause_id": clause["clause_id"], "rules": 0, "dropped": [], "cannot_encode": None, "confidence": []}
        if not res:
            entry["cannot_encode"] = "no response"
            report.append(entry)
            continue
        entry["cannot_encode"] = res.get("cannot_encode_reason")
        for i, r in enumerate(res.get("rules") or [], start=1):
            try:
                rule = _build_rule(clause, r, i)
            except (ValueError, JsonLogicError, KeyError, TypeError) as e:
                # one repair attempt with the error message
                repaired = llm_json(
                    llm,
                    LLMRequest(
                        step="compile.rules.repair",
                        system=system,
                        messages=[
                            {"role": "user", "content": rule_user(clause, clause["source_title"])},
                            {"role": "assistant", "content": json.dumps({"rules": [r], "cannot_encode_reason": None})},
                            {
                                "role": "user",
                                "content": f"Validation failed: {e}. Fix the rule using only ontology paths and the "
                                "operator subset, or return no rules with cannot_encode_reason.",
                            },
                        ],
                        output_schema=RULE_SCHEMA,
                        max_tokens=3000,
                        workspace_id=workspace_id,
                    ),
                    retries=0,
                )
                fixed = (repaired or {}).get("rules") or []
                if not fixed:
                    entry["dropped"].append(str(e)[:200])
                    continue
                try:
                    rule = _build_rule(clause, fixed[0], i)
                except (ValueError, JsonLogicError, KeyError, TypeError) as e2:
                    entry["dropped"].append(str(e2)[:200])
                    continue
            why = lint_rule(rule)
            if why:
                entry["dropped"].append(f"lint: {why}")
                continue
            rules.append(rule)
            entry["rules"] += 1
            entry["confidence"].append(r.get("confidence"))
        report.append(entry)
    return rules, report


def lint_rule(rule: Rule) -> str | None:
    """Return a reason to drop the rule, or None. Guards against rules a model tends to over-produce."""
    unconditional = not isinstance(rule.when, dict)
    if unconditional and rule.effect.decision in ("approve", "partial", "deny"):
        return "unconditional outcome (when=true)"
    if unconditional and not rule.claim_reasons:
        return "unconditional and unscoped"
    if rule.effect.decision == "approve" and (rule.effect.remedy in (None, "none")) and rule.effect.amount is None:
        return "approve with no remedy"
    if not rule.requires and not isinstance(rule.when, dict):
        return "no facts referenced"
    return None


def _fields(rule: Rule) -> set[str]:
    return {p for p in collect_vars(rule.when) if "." in p} | set(rule.requires)


def find_conflicts(rules: list[Rule]) -> list[str]:
    """Potential conflicts: same precedence level, overlapping claim reasons AND overlapping fact fields,
    different decisions. The engine still escalates real conflicts at runtime; this list is for review."""
    out: list[str] = []
    seen: set[tuple[str, str]] = set()
    for i, a in enumerate(rules):
        for b in rules[i + 1 :]:
            if (a.tier, a.priority) != (b.tier, b.priority) or a.effect.decision == b.effect.decision:
                continue
            ra, rb = set(a.claim_reasons), set(b.claim_reasons)
            if ra and rb and not (ra & rb):
                continue
            if not (_fields(a) & _fields(b)):
                continue
            key = (a.rule_id, b.rule_id)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                f"{a.rule_id} ({a.effect.decision}/{a.effect.remedy}) vs "
                f"{b.rule_id} ({b.effect.decision}/{b.effect.remedy}) "
                f"at tier={a.tier} priority={a.priority}; shared fields {sorted(_fields(a) & _fields(b))}"
            )
    return out


def apply_overrides(rules: list[Rule], overrides: dict | None) -> tuple[list[Rule], list[str]]:
    """Merchant edits (in the sandbox: curated by hand in samples/<client>/rule_overrides.json).
    {"disable": [rule_id...], "patch": {rule_id: {field: value}}, "add": [Rule dicts]}"""
    if not overrides:
        return rules, []
    log_lines: list[str] = []
    disable = set(overrides.get("disable", []))
    enable_only = overrides.get("enable_only")
    patch = overrides.get("patch", {})
    out: list[Rule] = []
    if enable_only is not None:
        kept = {r.rule_id for r in rules} & set(enable_only)
        log_lines.append(f"enable_only: kept {len(kept)} of {len(rules)} compiled rules")
    for r in rules:
        if r.rule_id in disable or (enable_only is not None and r.rule_id not in enable_only):
            if r.rule_id in disable:
                log_lines.append(f"disabled {r.rule_id}")
            continue
        if r.rule_id in patch:
            data = r.model_dump()
            for k, v in patch[r.rule_id].items():
                if k == "effect":
                    data["effect"].update(v)
                else:
                    data[k] = v
            r = Rule.model_validate(data)
            log_lines.append(f"patched {r.rule_id}: {sorted(patch[r.rule_id])}")
        out.append(r)
    for add in overrides.get("add", []):
        out.append(Rule.model_validate(add))
        log_lines.append(f"added {add['rule_id']}")
    return out, log_lines


def assemble_pack(workspace_id: str, clauses: list[dict], rules: list[Rule], version: str) -> RulePack:
    rclauses = [
        RClause(
            clause_id=c["clause_id"],
            title=c["title"],
            text=c["text"],
            source_url=c["source_url"],
            source_title=c["source_title"],
            fetched_at=(c.get("fetched_at") or "")[:10] or None,
            tier=c["tier"],
            ambiguous=c["ambiguous"],
            ambiguity_note=c.get("ambiguity_note"),
        )
        for c in clauses
    ]
    return RulePack(
        workspace=workspace_id, version=version, clauses=rclauses, rules=rules, conflicts=find_conflicts(rules)
    )


def new_version(existing: list[str]) -> str:
    today = datetime.now(UTC).date().isoformat()
    n = 1 + sum(1 for v in existing if v.startswith(today))
    return f"{today}.{n}"


def compile_report(clauses: list[dict], rules: list[Rule], conv_report: list[dict], pack: RulePack) -> dict[str, Any]:
    by_topic = Counter(c["topic"] for c in clauses)
    relevant = sum(1 for c in clauses if c["relevant_to_disputes"])
    return {
        "version": pack.version,
        "clauses": len(clauses),
        "relevant_clauses": relevant,
        "ambiguous_clauses": sum(1 for c in clauses if c["ambiguous"]),
        "non_verbatim_clauses": [c["clause_id"] for c in clauses if not c["verbatim"]],
        "rules": len(rules),
        "rules_by_tier": dict(Counter(r.tier for r in rules)),
        "rules_by_decision": dict(Counter(r.effect.decision for r in rules)),
        "clauses_by_topic": dict(by_topic),
        "cannot_encode": [e for e in conv_report if e.get("cannot_encode")],
        "dropped": [e for e in conv_report if e.get("dropped")],
        "conflicts": pack.conflicts,
    }
