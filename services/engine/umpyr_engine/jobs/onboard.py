"""Worker job handlers for onboarding steps (used by the live upload flow in M4; the sandbox CLI calls the same
functions directly). Each job runs one pipeline step for a workspace and streams progress via job events."""

from __future__ import annotations

from typing import Any

from ..sandbox.build import step_drift, step_eval, step_mine, step_procedures, step_router, step_taxonomy
from .queue import JobContext
from .registry import register


def _emit(ctx: JobContext):
    def emit(step: str, message: str) -> None:
        ctx.emit(step, message)

    return emit


@register("onboard.mine")
def job_mine(ctx: JobContext) -> dict[str, Any]:
    return step_mine(ctx.workspace_id, _emit(ctx), rebuild=bool(ctx.params.get("rebuild")))


@register("onboard.taxonomy")
def job_taxonomy(ctx: JobContext) -> dict[str, Any]:
    res = step_taxonomy(ctx.workspace_id, _emit(ctx), rebuild=bool(ctx.params.get("rebuild")))
    return {"categories": res.get("categories"), "method": res.get("method")}


@register("onboard.router")
def job_router(ctx: JobContext) -> dict[str, Any]:
    return step_router(ctx.workspace_id, _emit(ctx))


@register("onboard.procedures")
def job_procedures(ctx: JobContext) -> dict[str, Any]:
    return {"procedures": step_procedures(ctx.workspace_id, _emit(ctx), rebuild=bool(ctx.params.get("rebuild")))}


@register("onboard.drift")
def job_drift(ctx: JobContext) -> dict[str, Any]:
    return step_drift(ctx.workspace_id, _emit(ctx))


@register("onboard.eval")
def job_eval(ctx: JobContext) -> dict[str, Any]:
    res = step_eval(
        ctx.workspace_id, _emit(ctx), rebuild=bool(ctx.params.get("rebuild")), n=int(ctx.params.get("n", 1000))
    )
    return {k: v for k, v in res.items() if k != "rule_coverage"}


@register("onboard.pipeline")
def job_pipeline(ctx: JobContext) -> dict[str, Any]:
    from ..onboard.pipeline import run_onboarding

    def emit(step: str, message: str, level: str = "info", progress: int | None = None) -> None:
        ctx.emit(step, message, level=level, progress=progress)

    report = run_onboarding(ctx.session, ctx.workspace_id, emit)
    return {k: (v if not isinstance(v, list) else len(v)) for k, v in report.items() if k != "taxonomy"} | {
        "categories": len((report.get("taxonomy") or {}).get("categories", []))
    }


@register("retention.sweep")
def job_retention(ctx: JobContext) -> dict[str, Any]:
    from ..onboard.retention import sweep

    deleted = sweep(ctx.session)
    ctx.emit("retention", f"deleted {len(deleted)} expired workspaces")
    return {"deleted": deleted}
