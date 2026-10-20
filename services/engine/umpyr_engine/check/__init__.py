"""Umpyr Check hooks (ownership, claim verification, abuse scoring) — deferred.

Two hook points exist today with a no-op provider so the case ledger already carries `check.ingest` and
`check.pre_execution` events in their final positions. `CHECK_PROVIDER=none` is the only implementation.
"""

from .hooks import CheckProvider, CheckSignal, CheckVerdict, NoopCheck, get_check

__all__ = ["CheckProvider", "CheckSignal", "CheckVerdict", "NoopCheck", "get_check"]
