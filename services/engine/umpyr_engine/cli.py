"""`umpyr` command line.

umpyr db local            start the embedded dev Postgres (pgserver) and keep it running
umpyr db upgrade          run Alembic migrations to head
umpyr db revision -m msg  autogenerate a migration
umpyr api                 run the FastAPI app (uvicorn, reload in dev)
umpyr worker              run the background worker
umpyr spend               print today's LLM spend by step
umpyr llm smoke           one tiny Haiku call to verify key, model and accounting
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path


def _alembic_cfg():
    from alembic.config import Config

    ini = Path(__file__).resolve().parent.parent / "alembic.ini"
    return Config(str(ini))


def cmd_db(args: argparse.Namespace) -> int:
    from .config import get_settings

    if args.db_cmd == "local":
        from .db.base import resolve_database_url

        url = resolve_database_url(f"pgserver://{get_settings().local_pg_dir}")
        print(f"embedded Postgres running. DATABASE_URL={url}")
        print("Press Ctrl-C to stop.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0
    if args.db_cmd == "upgrade":
        from alembic import command

        command.upgrade(_alembic_cfg(), "head")
        print("migrations applied")
        return 0
    if args.db_cmd == "revision":
        from alembic import command

        command.revision(_alembic_cfg(), message=args.message, autogenerate=True)
        return 0
    return 1


def cmd_api(args: argparse.Namespace) -> int:
    import uvicorn

    from .config import get_settings

    s = get_settings()
    uvicorn.run("umpyr_engine.api.main:app", host=args.host, port=args.port, reload=(s.engine_env == "dev"))
    return 0


def cmd_worker(_: argparse.Namespace) -> int:
    from .jobs.worker import main_loop

    main_loop()
    return 0


def cmd_spend(_: argparse.Namespace) -> int:
    from .llm.factory import get_budget

    print(json.dumps(get_budget().summary(), indent=2))
    return 0


def cmd_retention(_: argparse.Namespace) -> int:
    from .db.base import get_session_factory
    from .onboard.retention import sweep

    with get_session_factory()() as session:
        print(json.dumps({"deleted": sweep(session)}))
    return 0


def cmd_llm(args: argparse.Namespace) -> int:
    from .llm import LLMRequest, get_llm

    if args.llm_cmd == "smoke":
        llm = get_llm()
        resp = llm.complete(
            LLMRequest(
                step="smoke",
                system="You are a terse assistant.",
                messages=[{"role": "user", "content": "Reply with the single word: ok"}],
                max_tokens=16,
            )
        )
        print(
            json.dumps(
                {"text": resp.text, "model": resp.model, "usage": resp.usage.__dict__, "cost_usd": resp.cost_usd},
                indent=2,
            )
        )
        return 0
    return 1


def _refresh_provenance_if_needed(client: str, report: dict) -> None:
    """Backfill clause provenance (design v2 §6) for a sandbox loaded before phase 1. No model calls."""
    from .db.base import get_session_factory
    from .sandbox.provenance_job import needs_provenance, refresh_provenance

    with get_session_factory()() as s:
        if needs_provenance(s, client):
            r = refresh_provenance(s, client, write_samples=False)
            report[client] = {**report.get(client, {}), "provenance": {k: v for k, v in r.items() if k != "unlocated"}}
    from .sandbox.plan_job import needs_plan_embeddings, refresh_plan_embeddings

    with get_session_factory()() as s:
        if needs_plan_embeddings(s, client):
            report[client] = {**report.get(client, {}), "plans": refresh_plan_embeddings(s, client)}


def cmd_seed(args: argparse.Namespace) -> int:
    """Load both sandbox merchants into the database from the cached samples/ outputs. Never calls the LLM:
    the daily cap is forced to 0 for this process, so any uncached step fails loudly instead of spending."""
    import os

    from sqlalchemy import func, select

    os.environ["DAILY_COST_CAP_USD"] = "0"
    from .config import get_settings

    get_settings.cache_clear()
    from .db import models as m
    from .db.base import get_session_factory
    from .sandbox.build import run_build

    report = {}
    for client in args.clients:
        with get_session_factory()() as s:
            n = s.execute(select(func.count()).select_from(m.Ticket).where(m.Ticket.workspace_id == client)).scalar()
        if n and not args.force:
            report[client] = {"skipped": f"{n} tickets already loaded (use --force to reload)"}
            _refresh_provenance_if_needed(client, report)
            continue
        r = run_build(client, rebuild=False, eval_n=args.eval_n)
        report[client] = {
            k: (
                v
                if not isinstance(v, dict)
                else {
                    kk: vv
                    for kk, vv in v.items()
                    if kk
                    in (
                        "tickets",
                        "records",
                        "cases",
                        "version",
                        "rules",
                        "categories_n",
                        "precision",
                        "recall",
                        "agreement",
                    )
                }
            )
            for k, v in r.items()
        }
    print(json.dumps(report, indent=2, default=str))
    return 0


def cmd_sandbox(args: argparse.Namespace) -> int:
    from .sandbox.fetch import run_fetch

    if args.sb_cmd == "fetch":
        results = run_fetch(args.client, rebuild=args.rebuild)
        gaps = [r["source_id"] for r in results if not r.get("ok")]
        print(json.dumps({"sources": len(results), "ok": len(results) - len(gaps), "gaps": gaps}, indent=2))
        return 0
    if args.sb_cmd == "seed":
        return cmd_seed(args)
    if args.sb_cmd == "build":
        from .sandbox.build import run_build

        report = run_build(args.client, steps=args.steps, rebuild=args.rebuild, eval_n=args.eval_n)
        print(
            json.dumps(
                {
                    k: (
                        v
                        if not isinstance(v, dict)
                        else {
                            kk: vv for kk, vv in v.items() if kk not in ("rule_coverage", "categories", "by_category")
                        }
                    )
                    for k, v in report.items()
                },
                indent=2,
                default=str,
            )[:6000]
        )
        return 0
    if args.sb_cmd == "synth":
        from .sandbox.synth_job import run_synth

        print(json.dumps(run_synth(args.client, n=args.n, rebuild=args.rebuild, limit=args.limit), indent=2))
        return 0
    if args.sb_cmd == "plans":
        from .db.base import get_session_factory
        from .sandbox.plan_job import refresh_plan_embeddings

        out = {}
        for client in args.clients:
            with get_session_factory()() as s:
                out[client] = refresh_plan_embeddings(s, client, rebuild=args.rebuild)
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.sb_cmd == "provenance":
        from .db.base import get_session_factory
        from .sandbox.provenance_job import refresh_provenance

        out = {}
        for client in args.clients:
            with get_session_factory()() as s:
                out[client] = refresh_provenance(s, client, write_samples=not args.no_write)
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.sb_cmd == "compile":
        from .db.base import get_session_factory
        from .sandbox.compile_job import run_compile

        with get_session_factory()() as session:
            report = run_compile(
                session, args.client, rebuild=args.rebuild, use_batch=not args.no_batch, reassemble=args.reassemble
            )
        print(
            json.dumps(
                {k: v for k, v in report.items() if k not in ("cannot_encode", "dropped")}, indent=2, default=str
            )
        )
        return 0
    return 1


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("pgserver").setLevel(logging.WARNING)
    p = argparse.ArgumentParser(prog="umpyr", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    db = sub.add_parser("db")
    dbs = db.add_subparsers(dest="db_cmd", required=True)
    dbs.add_parser("local")
    dbs.add_parser("upgrade")
    rev = dbs.add_parser("revision")
    rev.add_argument("-m", "--message", required=True)
    db.set_defaults(fn=cmd_db)

    api = sub.add_parser("api")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    api.set_defaults(fn=cmd_api)

    sub.add_parser("worker").set_defaults(fn=cmd_worker)
    sub.add_parser("spend").set_defaults(fn=cmd_spend)

    sub.add_parser("retention").set_defaults(fn=cmd_retention)

    llm = sub.add_parser("llm")
    llms = llm.add_subparsers(dest="llm_cmd", required=True)
    llms.add_parser("smoke")
    llm.set_defaults(fn=cmd_llm)

    sb = sub.add_parser("sandbox")
    sbs = sb.add_subparsers(dest="sb_cmd", required=True)
    f = sbs.add_parser("fetch")
    f.add_argument("client")
    f.add_argument("--rebuild", action="store_true")
    b = sbs.add_parser("build")
    b.add_argument("client")
    b.add_argument(
        "--steps",
        nargs="*",
        default=None,
        help="subset of: fetch compile synth ingest mine taxonomy router procedures drift eval",
    )
    b.add_argument("--rebuild", action="store_true")
    b.add_argument("--eval-n", type=int, default=1000)
    sd = sbs.add_parser("seed", help="load rockler + alaska from cached samples/ (no LLM calls; idempotent)")
    sd.add_argument("clients", nargs="*", default=["rockler", "alaska"])
    sd.add_argument("--force", action="store_true", help="reload even if the workspace already has tickets")
    sd.add_argument("--eval-n", type=int, default=1000)
    sy = sbs.add_parser("synth")
    sy.add_argument("client")
    sy.add_argument("--n", type=int, default=300)
    sy.add_argument("--limit", type=int, default=None, help="only write threads for the first N scenarios (dev)")
    sy.add_argument("--rebuild", action="store_true")
    pl = sbs.add_parser("plans", help="embed historical cases for the historical planner (local embedder; no LLM)")
    pl.add_argument("clients", nargs="*", default=["rockler", "alaska"])
    pl.add_argument("--rebuild", action="store_true")
    pv = sbs.add_parser(
        "provenance", help="locate clauses in their source documents and store provenance (no LLM calls)"
    )
    pv.add_argument("clients", nargs="*", default=["rockler", "alaska"])
    pv.add_argument("--no-write", action="store_true", help="update the database only, leave samples/ untouched")
    c = sbs.add_parser("compile")
    c.add_argument("client")
    c.add_argument("--rebuild", action="store_true")
    c.add_argument("--no-batch", action="store_true", help="synchronous calls instead of the Batch API")
    c.add_argument("--reassemble", action="store_true", help="re-apply rule_overrides.json to cached rules (no LLM)")
    sb.set_defaults(fn=cmd_sandbox)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
