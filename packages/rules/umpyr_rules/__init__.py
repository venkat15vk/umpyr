"""Umpyr rules: deterministic rule engine over compiled policy.

- jsonlogic: a small, deterministic JSON Logic evaluator (Decimal arithmetic).
- model: Clause, Rule, RulePack, Ruling, Check.
- engine: evaluate(rule_pack, facts) -> Ruling with precedence and conflict flags.
- pdp: authorize(actor, action, resource, facts) -> allow | deny | needs_approval.
- audit: canonical JSON + SHA-256 for audit records.
"""

from .audit import audit_hash, canonical_json
from .engine import evaluate
from .jsonlogic import JsonLogicError, apply, collect_vars
from .model import Check, Clause, Effect, Rule, RulePack, Ruling, validate_rule
from .pdp import Action, Actor, Authorization, PDPPolicy, Resource, authorize

__all__ = [
    "audit_hash",
    "canonical_json",
    "evaluate",
    "JsonLogicError",
    "apply",
    "collect_vars",
    "Check",
    "Clause",
    "Effect",
    "Rule",
    "RulePack",
    "Ruling",
    "validate_rule",
    "Action",
    "Actor",
    "Authorization",
    "PDPPolicy",
    "Resource",
    "authorize",
]
