import json

import pytest
from fastapi.testclient import TestClient
from umpyr_engine.api.main import create_app
from umpyr_engine.db.models import Workspace
from umpyr_engine.jobs.worker import run_one


@pytest.fixture
def client():
    return TestClient(create_app())


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["db"] is True and body["model"].startswith("claude-haiku")


def test_admin_spend_shape(client, clean_spend):
    r = client.get("/admin/spend")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"day", "cap_usd", "spent_usd", "remaining_usd", "blocked", "by_step", "embeddings_today"}


def test_workspaces(client, session_factory):
    with session_factory() as s:
        if s.get(Workspace, "testws") is None:
            s.add(Workspace(id="testws", name="Test", kind="sandbox"))
            s.commit()
    assert any(w["id"] == "testws" for w in client.get("/workspaces").json())
    assert client.get("/workspaces/testws").json()["name"] == "Test"
    assert client.get("/workspaces/nope").status_code == 404


def test_job_lifecycle_and_sse(client):
    r = client.post("/jobs/demo", json={"delay": 0})
    assert r.status_code == 200
    job = r.json()
    assert job["status"] == "queued"
    assert client.post("/jobs/unknown").status_code == 404

    assert run_one("test-worker") is True  # worker claims and runs it
    assert run_one("test-worker") is False  # queue empty

    done = client.get(f"/jobs/{job['id']}").json()
    assert (
        done["status"] == "done"
        and done["progress"] == 100
        and done["result"] == {"steps": ["ingest", "mine", "taxonomy"]}
    )

    with client.stream("GET", f"/jobs/{job['id']}/events") as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())
    events = [line for line in text.splitlines() if line.startswith("event:")]
    assert events.count("event: step") == 5  # start + 3 demo steps + done
    assert events[-1] == "event: end"
    datas = [json.loads(line[len("data: ") :]) for line in text.splitlines() if line.startswith("data: ")]
    assert [d["step"] for d in datas[:-1]] == ["start", "ingest", "mine", "taxonomy", "done"]


def test_workspace_summary_and_results_endpoints(client, session_factory):
    with session_factory() as s:
        if s.get(Workspace, "testws") is None:
            s.add(Workspace(id="testws", name="Test", kind="sandbox"))
            s.commit()
    body = client.get("/workspaces/testws/summary").json()
    assert body["workspace"]["id"] == "testws" and body["tickets"] == 0 and body["rule_pack"] is None
    assert client.get("/workspaces/testws/categories").json() == []
    assert client.get("/workspaces/testws/rules").json()["rules"] == []
    assert client.get("/workspaces/nope/summary").status_code == 404
