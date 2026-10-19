"""Small helpers over the provider interface: structured JSON calls with validation + one retry."""

from __future__ import annotations

import json
import logging
from typing import Any

from .base import LLMProvider, LLMRequest, LLMResponse

log = logging.getLogger(__name__)


def json_request(
    *,
    step: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    max_tokens: int = 4096,
    workspace_id: str | None = None,
    effort: str | None = None,
) -> LLMRequest:
    return LLMRequest(
        step=step,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_schema=schema,
        max_tokens=max_tokens,
        workspace_id=workspace_id,
        effort=effort,
    )


def parsed_or_none(resp: LLMResponse | None) -> Any:
    if resp is None or resp.refused:
        return None
    if resp.parsed is not None:
        return resp.parsed
    if resp.text:
        try:
            return json.loads(resp.text)
        except json.JSONDecodeError:
            return None
    return None


def llm_json(llm: LLMProvider, req: LLMRequest, retries: int = 1) -> Any:
    """Call and parse; retry once with a nudge if the output is not valid JSON."""
    resp = llm.complete(req)
    out = parsed_or_none(resp)
    if out is not None or retries <= 0:
        if out is None:
            log.warning("structured call failed (step=%s stop=%s)", req.step, resp.stop_reason)
        return out
    nudged = LLMRequest(
        step=req.step,
        system=req.system,
        messages=req.messages
        + [
            {"role": "assistant", "content": resp.text or "(empty)"},
            {"role": "user", "content": "That was not valid JSON matching the schema. Return only the JSON object."},
        ],
        output_schema=req.output_schema,
        max_tokens=req.max_tokens,
        workspace_id=req.workspace_id,
        effort=req.effort,
    )
    return parsed_or_none(llm.complete(nudged))


def llm_json_concurrent(llm: LLMProvider, reqs: list[LLMRequest], workers: int | None = None) -> list[Any]:
    """Synchronous requests in a small thread pool (used by live onboarding and by LLM_BATCH=false)."""
    from concurrent.futures import ThreadPoolExecutor

    from ..config import get_settings

    n = workers or get_settings().llm_sync_workers
    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(lambda r: llm_json(llm, r, retries=1), reqs))


def llm_json_batch(llm: LLMProvider, reqs: list[LLMRequest]) -> list[Any]:
    """Batch API (50% price) when LLM_BATCH=true; otherwise concurrent synchronous calls.
    Items that fail are retried once synchronously."""
    from concurrent.futures import ThreadPoolExecutor

    from ..config import get_settings

    settings = get_settings()
    if not settings.llm_batch:
        log.info("LLM_BATCH=false: %d synchronous requests with %d workers", len(reqs), settings.llm_sync_workers)
        with ThreadPoolExecutor(max_workers=settings.llm_sync_workers) as pool:
            return list(pool.map(lambda r: llm_json(llm, r, retries=1), reqs))
    responses = llm.batch(reqs)
    out: list[Any] = []
    for req, resp in zip(reqs, responses, strict=True):
        parsed = parsed_or_none(resp)
        if parsed is None:
            log.info("batch item failed; retrying synchronously (step=%s)", req.step)
            parsed = llm_json(llm, req, retries=1)
        out.append(parsed)
    return out
