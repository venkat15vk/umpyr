"""Case routes: case object, ledger events, chain verification, workspace metrics."""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from test_resolve_runtime import FakeEmbedder, FakeLLM, tiny_workspace  # noqa: F401
from umpyr_engine.api.main import create_app
from umpyr_engine.db import models as m
from umpyr_engine.resolve.runtime import run_turn


@pytest.fixture
def client():
    return TestClient(create_app())


def test_case_routes_and_metrics(client, session_factory, tiny_workspace):  # noqa: F811
    llm, emb = FakeLLM(), FakeEmbedder()
    with session_factory() as s:
        chat = m.ChatSession(
            workspace_id=tiny_workspace, channel="email", mode="autonomous", auto_approve_limit=Decimal("100"), state={}
        )
        s.add(chat)
        s.commit()
        ops = run_turn(s, llm, emb, chat, "Order R-900001 arrived broken, photos attached.")["ops"]
        session_id, case_id = str(chat.id), ops["case"]["id"]

    c = client.get(f"/sessions/{session_id}/case").json()
    assert c["id"] == case_id and c["status"] == "resolved" and c["event_count"] >= 10 and c["decision_hash"]
    assert client.get(f"/cases/{case_id}").json()["head_hash"] == c["head_hash"]
    events = client.get(f"/cases/{case_id}/events").json()
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert events[0]["action"] == "case.opened" and events[-1]["action"] == "decision"
    assert all(e["hash"] and len(e["hash"]) == 64 for e in events)
    assert events[1]["prev_hash"] == events[0]["hash"]
    v = client.get(f"/cases/{case_id}/verify").json()
    assert v["ok"] is True and v["events"] == len(events)
    assert client.get("/cases/00000000-0000-0000-0000-000000000000").status_code == 404

    mtr = client.get(f"/workspaces/{tiny_workspace}/metrics").json()
    assert mtr["cases"]["total"] >= 1 and mtr["cases"]["by_status"].get("resolved", 0) >= 1
    assert "per_resolved_case_usd" in mtr["cost"] and "by_type" in mtr["escalations"]
    assert mtr["time_to_resolution_hours"]["n"] >= 1
    assert client.get("/workspaces/nope/metrics").status_code == 404
