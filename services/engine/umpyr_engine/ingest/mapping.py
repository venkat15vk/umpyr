"""Column-mapping agent: maps arbitrary export columns to the canonical ticket schema (or ontology order fields).

Haiku proposes a mapping with confidence and notes from the header + a few sample rows; the merchant approves or
edits it in the onboarding UI before anything is ingested.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..llm import LLMProvider
from ..llm.helpers import json_request, llm_json

log = logging.getLogger(__name__)

TICKET_TARGETS = {
    "ticket_id": "unique ticket/conversation id",
    "created_at": "when the ticket was opened",
    "channel": "email | chat | phone | web_form",
    "subject": "subject / title",
    "thread": "the message thread: a JSON list of {role, at, text}, or the full conversation text, or the first customer message",
    "agent_actions": "list of actions staff took (tags, macros, resolution codes)",
    "resolution": "final resolution / outcome text or code",
    "amount": "money refunded/credited, if any",
    "order_id": "order number / booking reference the ticket is about",
    "customer_name": "customer name (masked on ingest)",
    "customer_email": "customer email (masked on ingest)",
    "staff_reply": "a separate column holding the staff reply text, if the export splits messages into columns",
}
ORDER_TARGETS = {
    "order_id": "order number or booking confirmation code (required)",
    "placed_at": "order/purchase date",
    "status": "order status",
    "total": "grand total paid",
    "subtotal": "merchandise subtotal",
    "shipping_total": "shipping charged",
    "payment_method": "card | paypal | gift_card | rewards_card | points",
    "customer_id": "customer identifier",
    "item_name": "product name (one row per line item exports)",
    "item_sku": "product sku",
    "item_category": "product category",
    "item_price": "unit price",
    "item_quantity": "quantity",
    "shipped_at": "ship date",
    "delivered_at": "delivery date",
    "carrier": "carrier",
}

SCHEMA = {
    "type": "object",
    "properties": {
        "mapping": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "source": {"type": "string", "description": "source column name, or empty when no column fits"},
                    "confidence": {"type": "number"},
                    "note": {"type": "string"},
                },
                "required": ["target", "source", "confidence", "note"],
                "additionalProperties": False,
            },
        },
        "thread_format": {"type": "string", "enum": ["json_list", "plain_text", "split_columns", "unknown"]},
        "notes": {"type": "string"},
    },
    "required": ["mapping", "thread_format", "notes"],
    "additionalProperties": False,
}
SYSTEM = """You map the columns of a merchant's exported spreadsheet onto a canonical schema for Umpyr. You see the column
names and a few sample rows. For every canonical target, pick the single best source column or leave `source` empty.
Never map the same source column to two targets unless it is genuinely both (e.g. a conversation text that is also the
subject). Confidence 0-1. In `thread_format`, say how the conversation is stored: json_list (a JSON array of messages),
plain_text (one text blob), split_columns (customer message and staff reply in separate columns), or unknown."""


def propose_mapping(
    llm: LLMProvider, workspace_id: str, columns: list[str], sample_rows: list[dict[str, Any]], kind: str = "tickets"
) -> dict[str, Any]:
    targets = TICKET_TARGETS if kind == "tickets" else ORDER_TARGETS
    samples = [{c: (str(r.get(c))[:160] if r.get(c) is not None else None) for c in columns} for r in sample_rows[:5]]
    user = (
        "Canonical targets:\n"
        + "\n".join(f"- {k}: {v}" for k, v in targets.items())
        + f"\n\nSource columns: {columns}\n\nSample rows:\n{json.dumps(samples, indent=1, default=str)}"
    )
    res = (
        llm_json(
            llm,
            json_request(
                step="mapping.propose",
                system=SYSTEM,
                user=user,
                schema=SCHEMA,
                max_tokens=1500,
                workspace_id=workspace_id,
            ),
        )
        or {}
    )
    valid = set(columns)
    mapping = {}
    for item in res.get("mapping", []):
        if item.get("target") in targets:
            src = item.get("source") or ""
            mapping[item["target"]] = {
                "source": src if src in valid else None,
                "confidence": float(item.get("confidence") or 0),
                "note": item.get("note") or "",
            }
    for t in targets:
        mapping.setdefault(t, {"source": None, "confidence": 0.0, "note": "no match"})
    return {
        "kind": kind,
        "columns": columns,
        "mapping": mapping,
        "thread_format": res.get("thread_format", "unknown"),
        "notes": res.get("notes", ""),
        "status": "proposed",
    }


def apply_ticket_mapping(rows: list[dict[str, Any]], mapping: dict[str, Any]) -> list[dict[str, Any]]:
    """Rename source columns to canonical names; build a thread when the export splits messages into columns."""
    m = {t: v.get("source") for t, v in mapping.get("mapping", {}).items()}
    fmt = mapping.get("thread_format", "unknown")
    out = []
    for i, r in enumerate(rows, start=1):

        def get(t, r=r):
            return r.get(m[t]) if m.get(t) else None

        thread = get("thread")
        if fmt == "split_columns" or (
            thread is not None and not str(thread).strip().startswith("[") and m.get("staff_reply")
        ):
            thread = [{"role": "customer", "at": get("created_at"), "text": str(thread or "")}]
            if get("staff_reply"):
                thread.append({"role": "staff", "at": None, "text": str(get("staff_reply"))})
        out.append(
            {
                "ticket_id": get("ticket_id") or f"T-{i:05d}",
                "created_at": get("created_at"),
                "channel": get("channel") or "email",
                "subject": get("subject") or "",
                "thread": thread if thread is not None else "",
                "agent_actions": get("agent_actions") or [],
                "resolution": get("resolution"),
                "amount": get("amount"),
                "order_id": get("order_id"),
                "customer_name": get("customer_name"),
                "customer_email": get("customer_email"),
            }
        )
    return out


def apply_order_mapping(rows: list[dict[str, Any]], mapping: dict[str, Any]) -> list[dict[str, Any]]:
    """Group line-item rows by order_id into ontology-shaped order records."""
    m = {t: v.get("source") for t, v in mapping.get("mapping", {}).items()}
    orders: dict[str, dict[str, Any]] = {}
    for r in rows:

        def get(t, r=r):
            return r.get(m[t]) if m.get(t) else None

        oid = str(get("order_id") or "").strip()
        if not oid:
            continue
        o = orders.setdefault(
            oid,
            {
                "order_id": oid,
                "placed_at": get("placed_at"),
                "status": get("status"),
                "total": get("total"),
                "subtotal": get("subtotal"),
                "shipping_total": get("shipping_total"),
                "payment_method": get("payment_method"),
                "items": [],
                "shipments": [],
                "customer": {"customer_id": get("customer_id")},
                "has_shipped": bool(get("shipped_at")),
            },
        )
        if get("item_name") or get("item_sku"):
            qty = get("item_quantity") or 1
            price = get("item_price")
            try:
                line_total = round(float(price) * float(qty), 2) if price not in (None, "") else None
            except (TypeError, ValueError):
                line_total = None
            o["items"].append(
                {
                    "item_id": f"{oid}-{len(o['items']) + 1}",
                    "sku": get("item_sku"),
                    "name": get("item_name"),
                    "category": get("item_category"),
                    "price": price,
                    "quantity": qty,
                    "line_total": line_total,
                }
            )
        if get("shipped_at") and not o["shipments"]:
            o["shipments"].append(
                {
                    "shipment_id": f"{oid}-S1",
                    "carrier": get("carrier"),
                    "shipped_at": get("shipped_at"),
                    "delivered_at": get("delivered_at"),
                    "status": "delivered" if get("delivered_at") else "in_transit",
                }
            )
    return list(orders.values())
