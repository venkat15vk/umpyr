import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from umpyr_engine.db import models as m
from umpyr_engine.ingest.mapping import apply_order_mapping, apply_ticket_mapping
from umpyr_engine.ingest.parsers import parse_mail, parse_ticket_file
from umpyr_engine.onboard.retention import sweep
from umpyr_engine.storage import LocalStorage

MBOX = b"""From alice@example.com Mon Oct  5 10:00:00 2026
From: Alice <alice@example.com>
To: support@shop.example
Subject: Broken clamp order R-55
Date: Mon, 05 Oct 2026 10:00:00 +0000

My clamp arrived broken. Please help.

From support@shop.example Mon Oct  5 12:00:00 2026
From: Support <support@shop.example>
To: alice@example.com
Subject: Re: Broken clamp order R-55
Date: Mon, 05 Oct 2026 12:00:00 +0000

Sorry about that, we'll send a replacement.

From bob@example.com Tue Oct  6 09:00:00 2026
From: Bob <bob@example.com>
To: support@shop.example
Subject: Refund please
Date: Tue, 06 Oct 2026 09:00:00 +0000

I want to return my sander.
"""


def test_mbox_threads_by_subject_and_roles():
    rows = parse_mail(MBOX, ".mbox", staff_domains=["shop.example"])
    assert len(rows) == 2
    t = rows[0]
    assert t["subject"] == "Broken clamp order R-55" and [x["role"] for x in t["thread"]] == ["customer", "staff"]
    assert "replacement" in t["thread"][1]["text"]


def test_csv_parse_and_mapping_apply(tmp_path):
    p = tmp_path / "export.csv"
    p.write_text(
        "Ticket,Opened,Subject,Customer Message,Agent Reply,Order,Name\n1,2026-01-02,Broken,It broke,We will replace it,R-1,Jo Bloggs\n"
    )
    rows, cols = parse_ticket_file(str(p), p.read_bytes())
    assert cols == ["Ticket", "Opened", "Subject", "Customer Message", "Agent Reply", "Order", "Name"]
    mapping = {
        "thread_format": "split_columns",
        "mapping": {
            "ticket_id": {"source": "Ticket"},
            "created_at": {"source": "Opened"},
            "subject": {"source": "Subject"},
            "thread": {"source": "Customer Message"},
            "staff_reply": {"source": "Agent Reply"},
            "order_id": {"source": "Order"},
            "customer_name": {"source": "Name"},
        },
    }
    out = apply_ticket_mapping(rows, mapping)
    assert out[0]["ticket_id"] == "1" and out[0]["order_id"] == "R-1" and out[0]["customer_name"] == "Jo Bloggs"
    assert [x["role"] for x in out[0]["thread"]] == ["customer", "staff"]


def test_order_mapping_groups_line_items():
    rows = [
        {"Order #": "R-9", "Product": "Clamp", "SKU": "C1", "Unit Price": "10", "Qty": "2", "Grand Total": "25"},
        {"Order #": "R-9", "Product": "Bit", "SKU": "B1", "Unit Price": "5", "Qty": "1", "Grand Total": "25"},
    ]
    mapping = {
        "mapping": {
            "order_id": {"source": "Order #"},
            "item_name": {"source": "Product"},
            "item_sku": {"source": "SKU"},
            "item_price": {"source": "Unit Price"},
            "item_quantity": {"source": "Qty"},
            "total": {"source": "Grand Total"},
        }
    }
    out = apply_order_mapping(rows, mapping)
    assert (
        len(out) == 1
        and out[0]["order_id"] == "R-9"
        and len(out[0]["items"]) == 2
        and out[0]["items"][0]["line_total"] == 20.0
    )


def test_local_storage_roundtrip_and_prefix_delete(tmp_path):
    st = LocalStorage(root=tmp_path)
    st.put("ws1/tickets/a.csv", b"x")
    assert st.get("ws1/tickets/a.csv") == b"x"
    assert st.delete_prefix("ws1") == 1 and not (tmp_path / "ws1").exists()
    try:
        st.put("../escape.txt", b"x")
    except ValueError:
        pass
    else:
        raise AssertionError("path escape allowed")


def test_retention_sweep_deletes_expired_upload_workspaces(session_factory):
    with session_factory() as s:
        s.add(m.Workspace(id="old-upload", name="Old", kind="upload", expires_at=datetime.now(UTC) - timedelta(days=1)))
        s.add(
            m.Workspace(
                id="fresh-upload", name="Fresh", kind="upload", expires_at=datetime.now(UTC) + timedelta(days=5)
            )
        )
        s.add(
            m.Workspace(
                id="sandbox-keep", name="Sandbox", kind="sandbox", expires_at=datetime.now(UTC) - timedelta(days=1)
            )
        )
        s.commit()
        deleted = sweep(s)
        assert deleted == ["old-upload"]
        assert s.get(m.Workspace, "fresh-upload") is not None and s.get(m.Workspace, "sandbox-keep") is not None


def test_create_workspace_and_upload_policy_file(session_factory):
    from fastapi.testclient import TestClient
    from umpyr_engine.api.main import create_app

    c = TestClient(create_app())
    ws = c.post("/workspaces", json={"name": "Acme Tools"}).json()
    assert ws["id"].startswith("acme-tools-") and ws["retention_days"] == 14
    r = c.post(
        f"/workspaces/{ws['id']}/uploads",
        data={"kind": "policy"},
        files={"file": ("returns.md", b"# Returns\nReturn within 30 days for a refund.\n", "text/markdown")},
    )
    assert r.status_code == 200 and r.json()["status"] == "ingested"
    docs = c.get(f"/workspaces/{ws['id']}/policy-documents").json()
    assert len(docs) == 1 and docs[0]["source_type"] == "md"
    r = c.post(f"/workspaces/{ws['id']}/onboard")
    assert r.status_code == 400  # no tickets yet
    r = c.post(
        f"/workspaces/{ws['id']}/uploads",
        data={"kind": "tickets"},
        files={"file": ("x.exe", b"nope", "application/octet-stream")},
    )
    assert r.status_code == 400
    ready = c.get(f"/workspaces/{ws['id']}/readiness").json()
    assert ready["ready"] is False and "no approved procedure" in ready["reasons"]
