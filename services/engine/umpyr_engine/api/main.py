"""FastAPI application."""

from __future__ import annotations

import hmac
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..config import get_settings
from ..llm.budget import BudgetExceeded
from .routes import admin, cases, health, jobs, onboard, resolve, sandbox, workspaces

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("pgserver").setLevel(logging.WARNING)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Umpyr Resolve engine", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if settings.api_gateway_secret:
        expected = settings.api_gateway_secret.get_secret_value()

        @app.middleware("http")
        async def _gateway(request: Request, call_next):  # type: ignore[no-untyped-def]
            # In production the only caller is the Cloudflare Pages proxy, which adds this header after the
            # password check. Anything else (direct hits on the Railway hostname) is refused.
            if request.url.path != "/health" and not hmac.compare_digest(
                request.headers.get("x-umpyr-gateway", ""), expected
            ):
                return JSONResponse(status_code=401, content={"error": "gateway_required"})
            return await call_next(request)

    @app.exception_handler(BudgetExceeded)
    async def _budget(_: Request, exc: BudgetExceeded) -> JSONResponse:
        return JSONResponse(
            status_code=402,
            content={
                "error": "budget_exceeded",
                "message": "Demo budget reached for today",
                "spent_usd": float(exc.spent),
                "cap_usd": float(exc.cap),
            },
        )

    app.include_router(health.router)
    app.include_router(admin.router)
    app.include_router(jobs.router)
    app.include_router(workspaces.router)
    app.include_router(sandbox.router)
    app.include_router(resolve.router)
    app.include_router(cases.router)
    app.include_router(onboard.router)
    return app


app = create_app()
