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

    llm = sub.add_parser("llm")
    llms = llm.add_subparsers(dest="llm_cmd", required=True)
    llms.add_parser("smoke")
    llm.set_defaults(fn=cmd_llm)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
