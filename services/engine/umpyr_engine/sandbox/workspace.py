from __future__ import annotations

from sqlalchemy.orm import Session

from ..db.models import Workspace


def ensure_workspace(session: Session, workspace_id: str, name: str, kind: str = "sandbox") -> Workspace:
    ws = session.get(Workspace, workspace_id)
    if ws is None:
        ws = Workspace(
            id=workspace_id,
            name=name,
            kind=kind,
            status="onboarding",
            settings={
                "mode": "autonomous",
                "auto_approve_limit": 100,
                "reply_channel": "email",
                "as_of": "2026-10-01T12:00:00",  # synthetic data is dated relative to this day
                "record_kind": "booking" if workspace_id == "alaska" else "order",
            },
            is_synthetic=True,
        )
        session.add(ws)
        session.commit()
    return ws
