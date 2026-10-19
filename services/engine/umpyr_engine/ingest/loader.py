"""Ticket and order ingestion with PII masking.

Accepts the canonical column layout (samples/ticket_template.csv) from CSV, XLSX, JSON or JSONL.
Arbitrary exports are mapped to this layout by the column-mapping agent (M4) before they get here.
Every text field is masked before the row is written; the raw file is never stored in the DB.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db.models import OrderRecord, Ticket
from .pii import PIIMasker

log = logging.getLogger(__name__)

CANONICAL = [
    "ticket_id",
    "created_at",
    "channel",
    "subject",
    "thread",
    "agent_actions",
    "resolution",
    "amount",
    "order_id",
]


def parse_rows(path: Path, content: bytes | None = None) -> list[dict[str, Any]]:
    data = content if content is not None else path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    if suffix == ".json":
        obj = json.loads(data.decode("utf-8"))
        if isinstance(obj, dict):
            for key in ("tickets", "data", "results", "rows"):
                if isinstance(obj.get(key), list):
                    return obj[key]
            return [obj]
        return obj
    if suffix == ".csv":
        return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    if suffix in (".xlsx", ".xlsm"):
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        header = [str(h).strip() if h is not None else f"col{i}" for i, h in enumerate(next(rows))]
        return [dict(zip(header, r, strict=False)) for r in rows if any(v is not None for v in r)]
    raise ValueError(f"unsupported ticket file type: {suffix}")


def _dt(v: Any) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y %H:%M", "%m/%d/%Y"):
        try:
            return datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _thread(v: Any) -> list[dict[str, Any]]:
    if v is None or v == "":
        return []
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("["):
            try:
                v = json.loads(s)
            except json.JSONDecodeError:
                return [{"role": "customer", "at": None, "text": s}]
        else:
            return [{"role": "customer", "at": None, "text": s}]
    out = []
    for m in v:
        if isinstance(m, str):
            out.append({"role": "customer", "at": None, "text": m})
        else:
            role = str(m.get("role") or m.get("author") or "customer").lower()
            role = "staff" if role in ("staff", "agent", "admin", "support") else "customer"
            at = m.get("at") or m.get("created_at") or m.get("timestamp")
            out.append(
                {
                    "role": role,
                    "at": at if isinstance(at, str) else (at.isoformat() if at else None),
                    "text": str(m.get("text") or m.get("body") or ""),
                }
            )
    return out


def _list(v: Any) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("["):
            try:
                return [str(x) for x in json.loads(s)]
            except json.JSONDecodeError:
                pass
        return [x.strip() for x in s.replace(";", ",").split(",") if x.strip()]
    return [str(x) for x in v]


def _amount(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).replace("$", "").replace(",", "").strip())
    except InvalidOperation:
        return None


def normalise(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticket_id": str(row.get("ticket_id") or "").strip(),
        "created_at": _dt(row.get("created_at")),
        "channel": (str(row.get("channel") or "email").strip().lower() or "email"),
        "subject": row.get("subject") or "",
        "thread": _thread(row.get("thread")),
        "agent_actions": _list(row.get("agent_actions")),
        "resolution": row.get("resolution") or None,
        "amount": _amount(row.get("amount")),
        "order_id": (str(row.get("order_id")).strip() if row.get("order_id") else None),
    }


def ingest_tickets(
    session: Session,
    workspace_id: str,
    rows: list[dict[str, Any]],
    masker: PIIMasker,
    *,
    upload_id=None,
    is_synthetic: bool = True,
    replace: bool = False,
) -> dict[str, Any]:
    if replace:
        session.execute(delete(Ticket).where(Ticket.workspace_id == workspace_id))
    existing = {
        t.ticket_id: t for t in session.execute(select(Ticket).where(Ticket.workspace_id == workspace_id)).scalars()
    }
    counts: dict[str, int] = {}
    n = 0
    for raw in rows:
        r = normalise(raw)
        if not r["ticket_id"]:
            continue
        masked_thread = []
        for m in r["thread"]:
            res = masker.mask(m["text"])
            for k, v in res.counts.items():
                counts[k] = counts.get(k, 0) + v
            masked_thread.append({"role": m["role"], "at": m["at"], "text": res.text})
        subject = masker.mask(r["subject"]).text
        resolution = masker.mask(r["resolution"]).text if r["resolution"] else None
        t = existing.get(r["ticket_id"]) or Ticket(workspace_id=workspace_id, ticket_id=r["ticket_id"])
        t.ticket_created_at = r["created_at"]
        t.channel = r["channel"]
        t.subject = subject
        t.thread = masked_thread
        t.agent_actions = r["agent_actions"]
        t.resolution = resolution
        t.amount = r["amount"]
        t.order_id = r["order_id"]
        t.pii_masked = True
        t.is_synthetic = is_synthetic
        t.upload_id = upload_id
        session.add(t)
        n += 1
    session.commit()
    return {"tickets": n, "masked": counts}


PII_KEYS = {"name", "email", "phone", "address", "first_name", "last_name"}


def _strip_pii(obj: Any, in_customer: bool = False) -> Any:
    """Drop PII fields from customer/passenger sub-records (product names etc. are kept)."""
    if isinstance(obj, dict):
        return {
            k: _strip_pii(v, in_customer or k in ("customer", "passenger", "passengers", "contact"))
            for k, v in obj.items()
            if not (in_customer and k in PII_KEYS)
        }
    if isinstance(obj, list):
        return [_strip_pii(v, in_customer) for v in obj]
    return obj


def ingest_orders(
    session: Session, workspace_id: str, records: list[dict[str, Any]], kind: str = "order", replace: bool = False
) -> int:
    if replace:
        session.execute(delete(OrderRecord).where(OrderRecord.workspace_id == workspace_id))
    existing = {
        o.order_id: o
        for o in session.execute(select(OrderRecord).where(OrderRecord.workspace_id == workspace_id)).scalars()
    }
    n = 0
    for rec in records:
        oid = str(rec.get("order_id") or rec.get("pnr") or "").strip()
        if not oid:
            continue
        row = existing.get(oid) or OrderRecord(workspace_id=workspace_id, order_id=oid)
        row.kind = kind
        row.data = json.loads(json.dumps(_strip_pii(rec), default=str))
        row.is_synthetic = True
        session.add(row)
        n += 1
    session.commit()
    return n
