"""`umpyr sandbox synth <client>`: generate orders/bookings + scenario ground truth, then have Haiku write the threads.

Outputs in samples/<client>/:
  orders.json | bookings.json      merchant records (stand-in for live lookups)
  tickets.jsonl                    canonical tickets (raw, synthetic PII, NOT masked - masking happens at ingest)
  tickets.csv                      same tickets in the samples/ticket_template.csv column layout
  tickets_truth.jsonl              hidden ground truth per ticket: category, facts, policy ruling, staff outcome, drift
  synthetic_pii_names.json         the fake names used, so masking can be verified
Cached: skipped when tickets.jsonl exists unless --rebuild.
"""

from __future__ import annotations

import csv
import json
import logging
import random
from datetime import timedelta
from pathlib import Path

from umpyr_rules import RulePack

from ..llm import get_llm
from .paths import client_dir
from .synth import rockler
from .synth.core import iso, thread_timestamps, write_jsonl
from .synth.writer import write_threads

log = logging.getLogger(__name__)

CSV_COLUMNS = [
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


def load_pack(client: str) -> RulePack:
    return RulePack.model_validate_json((client_dir(client) / "rule_pack.json").read_text())


def run_synth(client: str, n: int = 300, rebuild: bool = False, limit: int | None = None) -> dict:
    out: Path = client_dir(client)
    tickets_path = out / "tickets.jsonl"
    if tickets_path.exists() and not rebuild:
        rows = [json.loads(line) for line in tickets_path.read_text().splitlines() if line.strip()]
        log.info("using cached synthetic tickets (%d)", len(rows))
        return {"cached": True, "tickets": len(rows)}

    pack = load_pack(client)
    if client == "rockler":
        orders, scenarios, names = rockler.generate(pack, n)
        records_name = "orders.json"
        plan_fn = rockler.turn_plan
    elif client == "alaska":
        from .synth import alaska

        orders, scenarios, names = alaska.generate(pack, n)
        records_name = "bookings.json"
        plan_fn = alaska.turn_plan
    else:
        raise ValueError(client)
    if limit:
        orders, scenarios = orders[:limit], scenarios[:limit]

    (out / records_name).write_text(json.dumps(orders, indent=1, default=str))
    (out / "synthetic_pii_names.json").write_text(json.dumps(sorted(set(names)), indent=1))
    write_jsonl(out / "tickets_truth.jsonl", [s.to_truth() for s in scenarios])

    rng = random.Random(99)
    plans = []
    by_id = {o.get("order_id") or o.get("pnr"): o for o in orders}
    for sc in scenarios:
        rec = by_id[sc.order_id]
        item = sc.facts.get("item") or {}
        plans.append(plan_fn(rng, sc, rec, item))
    threads = write_threads(get_llm(), plans, client)

    tickets = []
    failed = 0
    for sc, th in zip(scenarios, threads, strict=True):
        if th is None:
            failed += 1
            continue
        stamps = thread_timestamps(rng, sc.created_at, len(th["messages"]), sc.hours_to_resolve)
        thread = [
            {"role": m["role"], "at": iso(t), "text": m["text"]} for m, t in zip(th["messages"], stamps, strict=True)
        ]
        resolved_at = sc.created_at + timedelta(hours=sc.hours_to_resolve)
        tickets.append(
            {
                "ticket_id": sc.ticket_id,
                "created_at": iso(sc.created_at),
                "resolved_at": iso(resolved_at),
                "channel": sc.channel,
                "subject": th["subject"],
                "thread": thread,
                "agent_actions": [s.action for s in sc.staff_steps],
                "resolution": f"{sc.staff_outcome['decision']}:{sc.staff_outcome['remedy']}",
                "amount": sc.staff_outcome.get("amount"),
                "order_id": sc.order_id,
                "customer": {"name": sc.person.name, "email": sc.person.email},
                "is_synthetic": True,
            }
        )
    write_jsonl(tickets_path, tickets)
    with (out / "tickets.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for t in tickets:
            w.writerow(
                {
                    "ticket_id": t["ticket_id"],
                    "created_at": t["created_at"],
                    "channel": t["channel"],
                    "subject": t["subject"],
                    "thread": json.dumps(t["thread"]),
                    "agent_actions": json.dumps(t["agent_actions"]),
                    "resolution": t["resolution"],
                    "amount": t["amount"] or "",
                    "order_id": t["order_id"],
                }
            )
    log.info("wrote %d tickets (%d failed) to %s", len(tickets), failed, tickets_path)
    return {"cached": False, "tickets": len(tickets), "failed": failed, "drift": sum(s.is_drift for s in scenarios)}
