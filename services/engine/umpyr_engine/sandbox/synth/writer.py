"""Thread writer: Haiku turns a fully specified scenario into realistic ticket text.

The structure (who speaks when, what each turn is for, the staff actions, the outcome) is fixed by
code; the model only writes the words. Customer messages include fake PII (name, email, phone,
sometimes address) so masking is demonstrable. Runs on the Batch API.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ...llm import LLMProvider
from ...llm.helpers import json_request, llm_json_batch

log = logging.getLogger(__name__)

THREAD_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "messages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"role": {"type": "string", "enum": ["customer", "staff"]}, "text": {"type": "string"}},
                "required": ["role", "text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["subject", "messages"],
    "additionalProperties": False,
}

SYSTEM = """You write realistic customer-support email threads for a synthetic dataset. The dataset is used to train and
test a dispute-resolution system, so the threads must be faithful to the turn plan you are given: same number of
messages, same roles in the same order, each message doing exactly what its `purpose` says, and the staff outcome
exactly as specified (same remedy, same amount, same reasons). Do not add promises, remedies or policy details that
are not in the plan.

Style:
- Customers write like real people: varied tone (polite, frustrated, terse, chatty), occasional typos, specifics
  (order number, product, dates). They sign with their name and include at least one contact detail (email or phone),
  and sometimes their mailing address, exactly as given in `customer`.
- Staff write as the merchant's support team: warm, concise, professional, first-name signature from `agent_name`.
  They reference the order number and, when the plan says so, the policy reason in plain language.
- Never mention that this is synthetic. Never include real brand phone numbers or URLs.
- Each message 2-8 sentences. Plain text, no markdown.
"""


def thread_request(plan: dict[str, Any], workspace_id: str):
    return json_request(
        step="synth.thread",
        system=SYSTEM,
        user="Write the thread for this plan. Return JSON only.\n\n" + json.dumps(plan, default=str, indent=1),
        schema=THREAD_SCHEMA,
        max_tokens=2500,
        workspace_id=workspace_id,
    )


def write_threads(llm: LLMProvider, plans: list[dict[str, Any]], workspace_id: str) -> list[dict | None]:
    reqs = [thread_request(p, workspace_id) for p in plans]
    log.info("writing %d threads via batch", len(reqs))
    results = llm_json_batch(llm, reqs)
    out: list[dict | None] = []
    for plan, res in zip(plans, results, strict=True):
        if not res or not res.get("messages"):
            out.append(None)
            continue
        msgs = res["messages"]
        want = [t["role"] for t in plan["turns"]]
        got = [m["role"] for m in msgs]
        if got != want:
            # keep what we can: truncate/pad roles to the plan; text stays model-written
            fixed = []
            for i, role in enumerate(want):
                text = msgs[i]["text"] if i < len(msgs) else plan["turns"][i]["purpose"]
                fixed.append({"role": role, "text": text})
            msgs = fixed
        out.append({"subject": res.get("subject") or plan["turns"][0]["purpose"][:80], "messages": msgs})
    return out
