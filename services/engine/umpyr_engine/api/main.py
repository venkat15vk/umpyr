"""FastAPI application."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..config import get_settings
from ..llm.budget import BudgetExceeded
from .routes import admin, health, jobs, sandbox, workspaces

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
    return app


app = create_app()
