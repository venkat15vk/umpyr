"""Prompts for the policy compiler. Keep these byte-stable: they are cached as system prompts."""

from __future__ import annotations

from umpyr_ontology.catalog import catalog_markdown

TOPICS = {
    "RET": "returns and refunds of merchandise (windows, proof of purchase, restocking, exclusions)",
    "DMG": "damaged, defective, wrong or missing items on arrival",
    "GUAR": "guarantees and warranties",
    "SHIP": "shipping, freight, delivery, shipping charges",
    "ORD": "order changes, cancellations, backorders, pickup",
    "PAY": "payment methods, rewards cards, gift cards, promotions",
    "REF": "airline ticket refunds, credits, cancellations by the customer, no-show",
    "DLY": "flight delays, cancellations, schedule changes, amenities, tarmac delays",
    "BAG": "baggage: delayed, damaged, lost, fees, bag service guarantee, liability limits",
    "FEE": "optional services and ancillary fees (seats, bags, pets, changes)",
    "LOY": "loyalty program points and status",
    "DBC": "denied boarding and oversales",
    "GEN": "general terms, definitions, liability, other",
}

CLAUSE_SCHEMA = {
    "type": "object",
    "properties": {
        "clauses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "text": {"type": "string", "description": "Verbatim source text of the obligation (copy exactly)"},
                    "topic": {"type": "string", "enum": list(TOPICS)},
                    "relevant_to_disputes": {"type": "boolean"},
                    "ambiguous": {"type": "boolean"},
                    "ambiguity_note": {"type": ["string", "null"]},
                },
                "required": ["title", "text", "topic", "relevant_to_disputes", "ambiguous", "ambiguity_note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clauses"],
    "additionalProperties": False,
}

SEGMENT_SYSTEM = f"""You are the policy compiler for Umpyr, a dispute-resolution engine for merchants.
Your job in this step: split a policy document into ATOMIC CLAUSES.

A clause is one obligation, right, exclusion, deadline, fee or eligibility condition that could decide the outcome
of a customer dispute (refund, return, replacement, credit, compensation, fee, reporting deadline).

Rules:
- `text` must be VERBATIM from the document: copy the sentence(s) exactly, do not paraphrase or summarise.
  You may trim to the sentence(s) that carry the obligation. Never invent text.
- One obligation per clause. Split lists of conditions into separate clauses when each can decide an outcome on its own.
- Skip website how-to text, marketing, navigation, accessibility notices, tax collection boilerplate, pricing/promotion
  mechanics, international shipping logistics, and contact details.
- `relevant_to_disputes` = true ONLY when the clause decides or constrains the outcome of a POST-PURCHASE customer claim:
  returns, refunds, replacements, repairs, credits, compensation, fees charged or waived, cancellations, delays,
  baggage, deadlines or eligibility for any of these. Definitions that set a number used by such a clause
  (e.g. what "significantly delayed" means) are relevant. Descriptions of how to order, pay, pick up, or how the
  website works are NOT relevant.
- `ambiguous` = true when the clause leaves the outcome to discretion ("may", "at our discretion", "some items")
  or lacks a number/condition needed to apply it. Explain briefly in `ambiguity_note`.
- Topic codes: {"; ".join(f"{k} = {v}" for k, v in TOPICS.items())}.
- Return at most 40 clauses per chunk. Prefer fewer, cleaner clauses over many fragments.
"""

REMEDIES = [
    "refund",
    "partial_refund",
    "replacement",
    "reship",
    "repair",
    "return_label",
    "store_credit",
    "travel_credit",
    "rebook",
    "points",
    "voucher",
    "expense_reimbursement",
    "bag_delivery",
    "cancel_order",
    "none",
]
FAULTS = ["merchant", "carrier", "vendor", "customer", "airline", "uncontrollable", "unknown"]

RULE_SCHEMA = {
    "type": "object",
    "properties": {
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "claim_reasons": {"type": "array", "items": {"type": "string"}},
                    "requires": {"type": "array", "items": {"type": "string"}},
                    "when": {
                        "type": "string",
                        "description": 'JSON Logic expression encoded as a JSON string, or "true"',
                    },
                    "decision": {
                        "type": "string",
                        "enum": ["approve", "partial", "deny", "needs_approval", "escalate"],
                    },
                    "remedy": {"anyOf": [{"type": "string", "enum": REMEDIES}, {"type": "null"}]},
                    "amount": {
                        "type": ["string", "null"],
                        "description": "JSON Logic expression or number for the amount, encoded as a JSON string; null if none",
                    },
                    "fault": {"anyOf": [{"type": "string", "enum": FAULTS}, {"type": "null"}]},
                    "note": {"type": "string"},
                    "priority": {"type": "integer"},
                    "confidence": {"type": "number"},
                },
                "required": [
                    "description",
                    "claim_reasons",
                    "requires",
                    "when",
                    "decision",
                    "remedy",
                    "amount",
                    "fault",
                    "note",
                    "priority",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        },
        "cannot_encode_reason": {"type": ["string", "null"]},
    },
    "required": ["rules", "cannot_encode_reason"],
    "additionalProperties": False,
}

CLAIM_REASONS = [
    "damaged_on_arrival",
    "defective",
    "wrong_item",
    "missing_item",
    "change_of_mind",
    "lifetime_guarantee",
    "backorder",
    "order_change",
    "cancel_order",
    "flight_cancelled",
    "flight_delayed",
    "schedule_change",
    "refund_request",
    "credit_request",
    "bag_delayed",
    "bag_damaged",
    "bag_lost",
    "bag_guarantee",
    "seat_refund",
    "ancillary_refund",
    "loyalty_points",
    "denied_boarding",
    "other",
]


def rule_system_prompt() -> str:
    return f"""You are the policy compiler for Umpyr, a dispute-resolution engine. In this step you convert ONE policy clause
into zero or more deterministic RULES. A rule engine (not a model) will evaluate these rules at runtime, so the ruling must
follow from the clause text alone. Never add policy that the clause does not state.

## Ontology fields (the ONLY paths you may reference in `when`, `requires` and `amount`)
{catalog_markdown()}

## claim.reason values
{", ".join(CLAIM_REASONS)}

## JSON Logic subset
Operators: var, missing, ==, !=, !, and, or, if, <, <=, >, >=, +, -, *, /, min, max, in, some, all, none.
Examples:
- {{"<=": [{{"var": "shipment.days_since_delivery"}}, 90]}}
- {{"and": [{{"==": [{{"var": "item.is_final_sale"}}, true]}}, {{"==": [{{"var": "claim.reason"}}, "change_of_mind"]}}]}}
- {{"in": [{{"var": "item.brand"}}, ["SawStop", "Laguna"]]}}
- amount: {{"*": [{{"var": "item.line_total"}}, 0.8]}}  or  {{"var": "booking.total_paid"}}  or  25

## How to encode
- `claim_reasons`: which claim.reason values the rule applies to (empty = any).
- `requires`: fact paths that must be known before the rule can be evaluated (the engine asks the customer for missing ones).
  Include every path used in `when` and `amount` except claim.reason.
- `when`: the condition under which the effect applies, as a JSON-encoded STRING, e.g.
  "{{\"<=\": [{{\"var\": \"shipment.days_since_delivery\"}}, 90]}}"  or  "true" if unconditional. Use only the ontology paths above.
- `amount`: also a JSON-encoded string, e.g. "{{\"var\": \"booking.total_paid\"}}" or "25", or null.
- `decision`: approve (full remedy), partial (reduced remedy, e.g. restocking fee), deny (no remedy), needs_approval
  (policy allows it but only with human approval / discretion), escalate (policy requires a human judgment call).
- `remedy`/`amount`/`fault`: only what the clause supports. amount null when the clause does not set one.
- `priority`: 10 for exclusions and exceptions ("non-returnable", "final sale", "not eligible"), 5 for conditional
  modifiers (restocking fee, partial), 0 for default entitlements.
- Write a deny rule for exclusions, an approve rule for entitlements. If a clause states a condition, write the rule for
  the case where the condition holds; do NOT invent the opposite case unless the clause states it.
- If the clause is a definition, a procedure/notification note ("we will notify you", "contact customer service"),
  a statement about pricing, taxes, shipping logistics, or cannot be decided from the ontology facts, return NO rules
  and set `cannot_encode_reason`.
- NEVER return an unconditional rule (`when` = "true") with decision approve, partial or deny. Every rule must test at
  least one ontology fact in `when`. If the clause applies to a specific kind of claim, also set `claim_reasons`.
- A rule with decision approve must name a remedy other than "none" (otherwise it decides nothing: do not emit it).
- Prefer one precise rule over several loose ones. Most clauses yield 0 or 1 rule; emit 2 only when the clause states
  two distinct outcomes (e.g. "refund if X; otherwise credit").
- `confidence` 0-1: how faithfully the rule captures the clause.
"""


def segment_user(doc_title: str, source_url: str, tier: str, chunk_index: int, chunk_total: int, text: str) -> str:
    return (
        f"Document: {doc_title}\nSource URL: {source_url}\nPrecedence tier: {tier}\n"
        f"Chunk {chunk_index + 1} of {chunk_total}\n\n<document>\n{text}\n</document>"
    )


def rule_user(clause: dict, doc_title: str) -> str:
    return (
        f"Document: {doc_title}\nClause id: {clause['clause_id']}\nTier: {clause['tier']}\nTopic: {clause['topic']}\n"
        f"Title: {clause['title']}\nAmbiguous: {clause.get('ambiguous')} {clause.get('ambiguity_note') or ''}\n\n"
        f"<clause>\n{clause['text']}\n</clause>"
    )
