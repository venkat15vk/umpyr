"""`umpyr sandbox build <client>`: run the onboarding pipeline offline, step by step, with caching.

Steps (in order): fetch -> compile -> synth -> ingest -> mine -> taxonomy -> router -> procedures -> drift -> eval
Each LLM-backed step caches its output under samples/<client>/ and is skipped unless --rebuild (or the cache is absent).
The same functions back the worker jobs used by the live upload flow (M4).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from umpyr_rules import RulePack

from ..db.base import get_session_factory
from ..embeddings import get_embedder
from ..eval.scenarios import generate_eval
from ..ingest.loader import ingest_orders, ingest_tickets
from ..ingest.pii import PIIMasker
from ..learn.drift import dump_cases, load_cases, replay, score_against_truth
from ..learn.mine import mine_workspace
from ..learn.procedures import mine_procedures
from ..learn.router import train_router
from ..learn.taxonomy import build_taxonomy
from ..llm import get_llm
from .compile_job import run_compile
from .fetch import run_fetch
from .paths import client_dir, sources_manifest
from .synth.core import read_jsonl, write_jsonl
from .synth_job import run_synth
from .workspace import ensure_workspace

log = logging.getLogger(__name__)

STEPS = ["fetch", "compile", "synth", "ingest", "mine", "taxonomy", "router", "procedures", "drift", "eval"]
Emit = Callable[[str, str], None]


def _emit_log(step: str, msg: str) -> None:
    log.info("[%s] %s", step, msg)


def step_ingest(client: str, emit: Emit) -> dict[str, Any]:
    out = client_dir(client)
    tickets = read_jsonl(out / "tickets.jsonl")
    records_path = out / ("bookings.json" if client == "alaska" else "orders.json")
    records = json.loads(records_path.read_text()) if records_path.exists() else []
    eval_records_path = out / ("eval_bookings.json" if client == "alaska" else "eval_orders.json")
    if eval_records_path.exists():  # eval scenarios reference their own records (distinct id range)
        records = records + json.loads(eval_records_path.read_text())
    names_path = out / "synthetic_pii_names.json"
    masker = PIIMasker(known_names=json.loads(names_path.read_text()) if names_path.exists() else None)
    with get_session_factory()() as s:
        ensure_workspace(s, client, sources_manifest(client)["name"])
        n_orders = ingest_orders(s, client, records, kind="booking" if client == "alaska" else "order", replace=True)
        stats = ingest_tickets(s, client, tickets, masker, is_synthetic=True, replace=True)
    emit("ingest", f"{stats['tickets']} tickets ingested (masked: {stats['masked']}), {n_orders} merchant records")
    return {**stats, "records": n_orders}


def step_mine(client: str, emit: Emit, rebuild: bool) -> dict[str, Any]:
    cache = client_dir(client) / "cases.jsonl"
    with get_session_factory()() as s:
        if cache.exists() and not rebuild:
            n = load_cases(s, client, read_jsonl(cache))
            emit("mine", f"loaded {n} cached mined cases")
            return {"cases": n, "cached": True}
        res = mine_workspace(s, get_llm(), client, kind="booking" if client == "alaska" else "order")
        write_jsonl(cache, dump_cases(s, client))
    emit("mine", f"mined {res['cases']} cases ({res['failed']} failed)")
    return res


def step_taxonomy(client: str, emit: Emit, rebuild: bool) -> dict[str, Any]:
    cache = client_dir(client) / "taxonomy.json"
    with get_session_factory()() as s:
        if cache.exists() and not rebuild:
            from ..learn.taxonomy import apply_cached_taxonomy

            res = apply_cached_taxonomy(s, get_embedder(), client, json.loads(cache.read_text()))
            emit("taxonomy", f"applied cached taxonomy: {len(res['categories'])} categories")
            return {**res, "cached": True}
        res = build_taxonomy(s, get_llm(), get_embedder(), client)
        cache.write_text(
            json.dumps(
                {**res, "assignments": {c["ticket_id"]: c["category_key"] for c in dump_cases(s, client)}}, indent=1
            )
        )
    emit("taxonomy", f"{len(res['categories'])} categories via {res['method']}")
    return res


def step_router(client: str, emit: Emit) -> dict[str, Any]:
    with get_session_factory()() as s:
        m = train_router(s, get_embedder(), client)
    (client_dir(client) / "router_metrics.json").write_text(json.dumps(m, indent=1))
    emit(
        "router",
        f"accuracy {m['accuracy']} (test n={m['n_test']}), coverage@{m['threshold']} {m['coverage_at_threshold']}",
    )
    return m


def step_procedures(client: str, emit: Emit, rebuild: bool) -> list[dict[str, Any]]:
    cache = client_dir(client) / "procedures.json"
    with get_session_factory()() as s:
        if cache.exists() and not rebuild:
            from ..learn.procedures import load_cached_procedures

            n = load_cached_procedures(s, client, json.loads(cache.read_text()))
            emit("procedures", f"loaded {n} cached procedures")
            return [{"cached": True, "procedures": n}]
        res = mine_procedures(s, get_llm(), client, approve=True)
        from ..learn.procedures import dump_procedures

        cache.write_text(json.dumps(dump_procedures(s, client), indent=1, default=str))
    emit("procedures", f"{len(res)} procedures drafted and auto-approved (sandbox)")
    return res


def step_drift(client: str, emit: Emit) -> dict[str, Any]:
    with get_session_factory()() as s:
        res = replay(s, client)
        truth = read_jsonl(client_dir(client) / "tickets_truth.jsonl")
        if truth:
            res["vs_ground_truth"] = score_against_truth(s, client, truth)
        write_jsonl(client_dir(client) / "cases.jsonl", dump_cases(s, client))
    (client_dir(client) / "drift_report.json").write_text(json.dumps(res, indent=1, default=str))
    emit(
        "drift",
        f"{res['drift_findings']} drift findings, {res['unwritten_rule_candidates']} unwritten-rule candidates, {res['gold']} gold cases",
    )
    return res


def step_eval(client: str, emit: Emit, rebuild: bool, n: int = 1000) -> dict[str, Any]:
    cache = client_dir(client) / "eval_scenarios.jsonl"
    pack = RulePack.model_validate_json((client_dir(client) / "rule_pack.json").read_text())
    with get_session_factory()() as s:
        if cache.exists() and not rebuild:
            from sqlalchemy import delete

            from ..db.models import EvalScenario

            rows = read_jsonl(cache)
            s.execute(delete(EvalScenario).where(EvalScenario.workspace_id == client))
            for r in rows:
                s.add(
                    EvalScenario(
                        workspace_id=client,
                        scenario_id=r["scenario_id"],
                        category_key=r["category_key"],
                        source_rule_id=r["source_rule_id"],
                        ticket_text=r["ticket_text"],
                        facts=r["facts"],
                        expected=r["expected"],
                        split=r["split"],
                    )
                )
            s.commit()
            emit("eval", f"loaded {len(rows)} cached eval scenarios")
            return {"scenarios": len(rows), "cached": True}
        res = generate_eval(s, get_llm(), client, pack, n=n)
    write_jsonl(cache, res.pop("rows"))
    records = res.pop("records")
    (client_dir(client) / ("eval_bookings.json" if client == "alaska" else "eval_orders.json")).write_text(
        json.dumps(records, indent=1, default=str)
    )
    (client_dir(client) / "eval_report.json").write_text(json.dumps(res, indent=1, default=str))
    emit("eval", f"{res['scenarios']} scenarios; rules never fired: {len(res['rules_never_fired'])}")
    return res


def run_build(
    client: str, steps: list[str] | None = None, rebuild: bool = False, emit: Emit = _emit_log, eval_n: int = 1000
) -> dict[str, Any]:
    steps = steps or STEPS
    report: dict[str, Any] = {}
    for step in STEPS:
        if step not in steps:
            continue
        emit(step, "starting")
        if step == "fetch":
            report[step] = {"ok": sum(1 for r in run_fetch(client, rebuild=rebuild) if r.get("ok"))}
        elif step == "compile":
            with get_session_factory()() as s:
                report[step] = {
                    k: v
                    for k, v in run_compile(s, client, rebuild=rebuild).items()
                    if k in ("version", "clauses", "rules", "conflicts")
                }
        elif step == "synth":
            report[step] = run_synth(client, rebuild=rebuild)
        elif step == "ingest":
            report[step] = step_ingest(client, emit)
        elif step == "mine":
            report[step] = step_mine(client, emit, rebuild)
        elif step == "taxonomy":
            report[step] = step_taxonomy(client, emit, rebuild)
        elif step == "router":
            report[step] = step_router(client, emit)
        elif step == "procedures":
            report[step] = step_procedures(client, emit, rebuild)
        elif step == "drift":
            report[step] = step_drift(client, emit)
        elif step == "eval":
            report[step] = step_eval(client, emit, rebuild, n=eval_n)
    (client_dir(client) / "build_report.json").write_text(json.dumps(report, indent=1, default=str))
    return report
