"""Rule format.

A RulePack is the compiled, versioned output of the policy compiler for one
workspace. Rules reference only ontology field paths; every rule cites a clause
with its source text and URL.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from umpyr_ontology import Decision, Fault, Remedy, Tier, validate_path

from .jsonlogic import collect_vars

Json = Any


class Clause(BaseModel):
    """An atomic policy statement with provenance."""

    model_config = ConfigDict(extra="forbid")

    clause_id: str = Field(..., description="e.g. RK-RET-1")
    title: str
    text: str = Field(..., description="Verbatim source text of the clause")
    source_url: str
    source_title: str | None = None
    fetched_at: str | None = Field(None, description="ISO date the source was fetched")
    tier: Tier
    ambiguous: bool = Field(False, description="Compiler flagged this clause as ambiguous")
    ambiguity_note: str | None = None


class Effect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision
    remedy: Remedy | None = None
    amount: Json | None = Field(
        None,
        description="JSON Logic expression (or literal) evaluated over facts to produce the amount, "
        "e.g. {'*': [{'var': 'item.line_total'}, 0.8]}",
    )
    fault: Fault | None = None
    note: str | None = Field(None, description="Short explanation shown to ops")


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    clause_id: str
    tier: Tier
    priority: int = Field(0, description="Within a tier, higher priority wins (exceptions > defaults)")
    categories: list[str] = Field(
        default_factory=list, description="Taxonomy categories this rule applies to; empty = all"
    )
    claim_reasons: list[str] = Field(
        default_factory=list, description="claim.reason values this rule applies to; empty = all"
    )
    requires: list[str] = Field(default_factory=list, description="Fact paths that must be present to evaluate")
    when: Json = Field(..., description="JSON Logic condition over facts")
    effect: Effect
    description: str = ""
    enabled: bool = True

    @model_validator(mode="after")
    def _check_paths(self) -> Rule:
        problems = validate_rule(self)
        if problems:
            raise ValueError("; ".join(problems))
        return self


def validate_rule(rule: Rule) -> list[str]:
    """Return a list of problems (empty = valid). Checks that every referenced path is in the ontology."""
    problems: list[str] = []
    for p in collect_vars(rule.when) | set(rule.requires):
        if p in ("current", "accumulator") or p.startswith("current.") or p.startswith("accumulator."):
            continue
        if p == "":
            continue
        # inside some/all/none the var is relative to the element; accept short relative names
        if "." not in p and p not in (
            "order",
            "item",
            "shipment",
            "booking",
            "flight",
            "bag",
            "claim",
            "customer",
            "case",
        ):
            continue
        if not validate_path(p):
            problems.append(f"{rule.rule_id}: unknown ontology path '{p}'")
    if rule.effect.amount is not None and isinstance(rule.effect.amount, dict):
        for p in collect_vars(rule.effect.amount):
            if "." in p and not validate_path(p):
                problems.append(f"{rule.rule_id}: unknown ontology path in amount '{p}'")
    return problems


class RulePack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace: str
    version: str = Field(..., description="e.g. 2026-10-19.1")
    clauses: list[Clause] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list, description="Compiler-detected conflicts between rules")

    def clause(self, clause_id: str) -> Clause | None:
        for c in self.clauses:
            if c.clause_id == clause_id:
                return c
        return None

    def rule(self, rule_id: str) -> Rule | None:
        for r in self.rules:
            if r.rule_id == rule_id:
                return r
        return None

    @model_validator(mode="after")
    def _check_refs(self) -> RulePack:
        ids = {c.clause_id for c in self.clauses}
        missing = sorted({r.clause_id for r in self.rules if r.clause_id not in ids})
        if missing:
            raise ValueError(f"rules reference unknown clauses: {missing}")
        dup = [r for r in {x.rule_id for x in self.rules} if sum(1 for x in self.rules if x.rule_id == r) > 1]
        if dup:
            raise ValueError(f"duplicate rule ids: {sorted(dup)}")
        return self


class Check(BaseModel):
    """One evaluated rule, shown in the ops per-step table."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    clause_id: str
    tier: Tier
    priority: int
    applicable: bool = Field(..., description="Category/claim scope matched")
    missing: list[str] = Field(default_factory=list, description="Required facts missing")
    fired: bool = Field(False, description="Condition evaluated true")
    fields: dict[str, Any] = Field(default_factory=dict, description="Referenced fields with their values")
    effect: Effect | None = None
    amount: Decimal | None = None
    error: str | None = None


class Ruling(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision
    remedy: Remedy | None = None
    amount: Decimal | None = None
    fault: Fault | None = None
    winning_rule_id: str | None = None
    winning_clause_id: str | None = None
    tier: Tier | None = None
    fired_rule_ids: list[str] = Field(default_factory=list)
    clause_ids: list[str] = Field(default_factory=list, description="All clauses that fired, highest tier first")
    missing_facts: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    checks: list[Check] = Field(default_factory=list)
    rule_pack_version: str
    explanation: str = ""
