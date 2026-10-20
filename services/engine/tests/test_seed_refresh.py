"""Boot refresh: a newer cached rule pack replaces a stale active pack (no model calls)."""

from __future__ import annotations

import json

from sqlalchemy import select
from umpyr_engine.db import models as m
from umpyr_engine.sandbox.compile_job import refresh_pack_if_stale
from umpyr_engine.sandbox.paths import client_dir


def test_stale_active_pack_is_refreshed_from_cache(session_factory):
    cached = json.loads((client_dir("rockler") / "rule_pack.json").read_text())
    with session_factory() as s:
        if s.get(m.Workspace, "rockler") is None:
            s.add(m.Workspace(id="rockler", name="Rockler", kind="sandbox", status="ready", is_synthetic=True))
            s.flush()
        s.add(
            m.RulePack(
                workspace_id="rockler",
                version="0000-00-00.0",
                pack={"workspace": "rockler", "version": "0000-00-00.0", "clauses": [], "rules": [], "conflicts": []},
                rule_count=0,
                status="active",
            )
        )
        s.commit()
        r = refresh_pack_if_stale(s, "rockler")
        assert r and r["from"] == "0000-00-00.0" and r["to"] == cached["version"]
        active = (
            s.execute(select(m.RulePack).where(m.RulePack.workspace_id == "rockler", m.RulePack.status == "active"))
            .scalars()
            .all()
        )
        assert (
            len(active) == 1 and active[0].version == cached["version"] and active[0].rule_count == len(cached["rules"])
        )
        assert refresh_pack_if_stale(s, "rockler") is None  # up to date now
