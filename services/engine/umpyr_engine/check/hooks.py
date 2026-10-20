from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from functools import lru_cache
from typing import Any, Protocol


@dataclass
class CheckSignal:
    """Result of the ingestion hook (runs after PII masking, before extraction)."""

    status: str = "not_configured"  # not_configured | ok | review | block
    risk: float | None = None
    notes: list[str] = field(default_factory=list)
    provider: str = "none"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CheckVerdict:
    """Result of the pre-execution hook (runs inside the governance gate, before any irreversible step)."""

    allow: bool = True
    status: str = "not_configured"
    reasons: list[str] = field(default_factory=list)
    provider: str = "none"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class CheckProvider(Protocol):
    name: str

    def on_ingest(
        self, workspace_id: str, case_id: str, masked_text: str, facts: dict[str, Any] | None
    ) -> CheckSignal: ...

    def pre_execution(
        self, workspace_id: str, case_id: str, action: str, amount: Decimal | None, facts: dict[str, Any] | None
    ) -> CheckVerdict: ...


class NoopCheck:
    name = "none"

    def on_ingest(self, workspace_id: str, case_id: str, masked_text: str, facts: dict[str, Any] | None) -> CheckSignal:
        return CheckSignal()

    def pre_execution(
        self, workspace_id: str, case_id: str, action: str, amount: Decimal | None, facts: dict[str, Any] | None
    ) -> CheckVerdict:
        return CheckVerdict()


@lru_cache
def get_check() -> CheckProvider:
    from ..config import get_settings

    provider = (get_settings().check_provider or "none").lower()
    if provider == "none":
        return NoopCheck()
    raise RuntimeError(f"unknown CHECK_PROVIDER '{provider}' (only 'none' exists; Umpyr Check is deferred)")
