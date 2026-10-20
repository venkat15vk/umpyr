"""Customer-facing status events: pipeline stage -> templated, humane message.

Templated, never generated, so no policy text or internal reasoning can leak. Phase 1 emits these into the
ops payload only; phase 3 shows them to the customer as interim messages.
"""

from __future__ import annotations

from typing import Any

TEMPLATES: dict[str, str] = {
    "message.received": "Thanks, we have your message and are looking into it.",
    "record.lookup": "Checking your {record_word}.",
    "record.missing": "We could not match a {record_word} yet; we may ask you to confirm the reference.",
    "precedent.retrieved": "Comparing with similar cases.",
    "rule_check": "Reviewing what the policy provides for this situation.",
    "options.review": "Reviewing refund options.",
    "gate.queued": "This needs a quick authorisation on our side; we will confirm shortly.",
    "gate.allowed": "Approved; processing now.",
    "execution": "Processing your {remedy}.",
    "escalation": "A specialist is taking a look; this can take a little longer and we will follow up directly.",
    "needs_fact": "We need one or two more details from you to finish this.",
    "decision.resolved": "Done. Details are in the reply below.",
    "decision.denied": "We have reviewed this and explain the outcome in the reply below.",
}

RECORD_WORD = {"booking": "booking", "order": "order"}


def status_event(action: str, **ctx: Any) -> dict[str, Any]:
    tpl = TEMPLATES.get(action)
    if tpl is None:
        return {"action": action, "message": None}
    ctx.setdefault("record_word", RECORD_WORD.get(str(ctx.get("kind", "order")), "order"))
    ctx.setdefault("remedy", "request")
    try:
        msg = tpl.format(**ctx)
    except (KeyError, IndexError):
        msg = tpl
    return {"action": action, "message": msg}
