"""Taxonomy: embed cases -> cluster (HDBSCAN, fallback k-means) -> Haiku names clusters -> merge -> categories.

Pain score = volume x average hours to resolve. In the sandbox categories are auto-approved; the setup UI (M4)
lets a merchant rename/merge/approve.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from typing import Any

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db.models import Case, Category
from ..embeddings import EmbeddingProvider
from ..llm import LLMProvider
from ..llm.helpers import json_request, llm_json

log = logging.getLogger(__name__)

NAME_SCHEMA = {
    "type": "object",
    "properties": {
        "key": {"type": "string", "description": "snake_case identifier, 2-4 words"},
        "name": {"type": "string", "description": "Short human name, 2-5 words"},
        "description": {"type": "string", "description": "One sentence: what tickets in this category are about"},
    },
    "required": ["key", "name", "description"],
    "additionalProperties": False,
}
MERGE_SCHEMA = {
    "type": "object",
    "properties": {
        "merges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from_key": {"type": "string"},
                    "into_key": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["from_key", "into_key", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["merges"],
    "additionalProperties": False,
}

NAME_SYSTEM = """You name clusters of customer-support cases for a merchant's dispute taxonomy. Given sample case summaries
from one cluster, return a snake_case key, a short name and a one-sentence description. Name the ISSUE TYPE
(what the customer's problem is), not the remedy. Be specific but general enough to cover the samples."""

MERGE_SYSTEM = """You review a draft taxonomy of customer-support categories. Propose merges ONLY for categories that are
the same issue type (duplicates or near-duplicates). Do not merge categories that differ in the customer's problem
(e.g. 'damaged on arrival' vs 'defective after use' stay separate). Return an empty list if nothing should merge."""


def case_text(c: Case, first_message: str | None = None) -> str:
    """Text embedded for clustering, the router and neighbour search. Includes the customer's opening message so
    runtime queries (raw customer messages) live in the same space as the cases."""
    claim = (c.facts or {}).get("claim", {}) or {}
    rec = c.record or {}
    msg = first_message or rec.get("first_message") or ""
    return f"{msg[:600]} {c.summary or ''} {rec.get('category_hint') or ''} reason={claim.get('reason') or ''}".strip()


def cluster(x: np.ndarray, min_cluster_size: int) -> tuple[np.ndarray, str]:
    from sklearn.cluster import HDBSCAN, KMeans
    from sklearn.metrics import silhouette_score

    n = len(x)
    labels = HDBSCAN(min_cluster_size=min_cluster_size, min_samples=3, metric="euclidean").fit_predict(x)
    k = len({v for v in labels if v >= 0})
    noise = float(np.mean(labels < 0)) if n else 1.0
    if k >= 3 and noise < 0.4:
        if noise > 0:  # attach noise points to the nearest cluster centroid
            cents = {c: x[labels == c].mean(axis=0) for c in range(k)}
            for i in np.where(labels < 0)[0]:
                labels[i] = min(cents, key=lambda c: float(np.linalg.norm(x[i] - cents[c])))
        return labels, f"hdbscan(k={k}, noise={noise:.0%} reattached)"
    best_k, best_s, best_labels = 0, -1.0, labels
    for kk in range(4, min(13, max(5, n // 15))):
        km = KMeans(n_clusters=kk, n_init=10, random_state=0).fit(x)
        s = silhouette_score(x, km.labels_) if kk < n else -1
        if s > best_s:
            best_k, best_s, best_labels = kk, s, km.labels_
    return best_labels, f"kmeans(k={best_k}, silhouette={best_s:.2f}; hdbscan gave k={k}, noise={noise:.0%})"


def _top_terms(texts: list[str], k: int = 8) -> list[str]:
    stop = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "to",
        "of",
        "for",
        "in",
        "on",
        "with",
        "is",
        "was",
        "customer",
        "requested",
        "reason",
        "none",
        "after",
        "that",
        "their",
        "they",
        "it",
        "be",
        "by",
        "from",
        "at",
        "as",
    }
    cnt: Counter[str] = Counter()
    for t in texts:
        for w in re.findall(r"[a-z][a-z_-]{2,}", t.lower()):
            if w not in stop:
                cnt[w] += 1
    return [w for w, _ in cnt.most_common(k)]


def build_taxonomy(
    session: Session,
    llm: LLMProvider,
    embedder: EmbeddingProvider,
    workspace_id: str,
    *,
    min_cluster_size: int | None = None,
) -> dict[str, Any]:
    cases = list(
        session.execute(select(Case).where(Case.workspace_id == workspace_id).order_by(Case.ticket_id)).scalars()
    )
    if len(cases) < 10:
        raise RuntimeError("need at least 10 mined cases to build a taxonomy")
    texts = [case_text(c) for c in cases]
    emb = embedder.embed(texts, step="taxonomy.embed", workspace_id=workspace_id)
    x = np.array(emb.vectors, dtype=np.float32)
    labels, method = cluster(x, min_cluster_size or max(5, len(cases) // 40))
    log.info("clustering: %s", method)

    groups: dict[int, list[int]] = defaultdict(list)
    for i, lab in enumerate(labels):
        groups[int(lab)].append(i)

    # name clusters
    named: dict[int, dict[str, Any]] = {}
    used_keys: set[str] = set()
    for lab, idxs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        sample = [texts[i][:300] for i in idxs[:12]]
        reasons = Counter((cases[i].facts or {}).get("claim", {}).get("reason") for i in idxs).most_common(3)
        user = (
            f"Cluster of {len(idxs)} cases. Most common claim reasons: {reasons}. Top terms: {_top_terms([texts[i] for i in idxs])}.\n\n"
            + "\n".join(f"- {s}" for s in sample)
        )
        res = (
            llm_json(
                llm,
                json_request(
                    step="taxonomy.name",
                    system=NAME_SYSTEM,
                    user=user,
                    schema=NAME_SCHEMA,
                    max_tokens=300,
                    workspace_id=workspace_id,
                ),
            )
            or {}
        )
        key = (
            re.sub(r"[^a-z0-9_]+", "_", (res.get("key") or f"cluster_{lab}").lower()).strip("_")[:60]
            or f"cluster_{lab}"
        )
        base = key
        n = 2
        while key in used_keys:
            key = f"{base}_{n}"
            n += 1
        used_keys.add(key)
        named[lab] = {
            "key": key,
            "name": res.get("name") or key.replace("_", " ").title(),
            "description": res.get("description") or "",
            "cluster_label": lab,
            "members": idxs,
        }

    # merge pass
    if len(named) > 2:
        listing = "\n".join(
            f"- {v['key']}: {v['name']} - {v['description']} ({len(v['members'])} cases)" for v in named.values()
        )
        res = (
            llm_json(
                llm,
                json_request(
                    step="taxonomy.merge",
                    system=MERGE_SYSTEM,
                    user=listing,
                    schema=MERGE_SCHEMA,
                    max_tokens=600,
                    workspace_id=workspace_id,
                ),
            )
            or {}
        )
        by_key = {v["key"]: lab for lab, v in named.items()}
        for mg in res.get("merges", []):
            a, b = by_key.get(mg.get("from_key")), by_key.get(mg.get("into_key"))
            if a is None or b is None or a == b or a not in named or b not in named:
                continue
            named[b]["members"] += named[a]["members"]
            log.info("merged %s into %s: %s", mg["from_key"], mg["into_key"], mg.get("why"))
            del named[a]

    # persist
    session.execute(delete(Category).where(Category.workspace_id == workspace_id))
    out = []
    for v in named.values():
        hours = [float(cases[i].hours_to_resolve) for i in v["members"] if cases[i].hours_to_resolve is not None]
        avg_h = round(sum(hours) / len(hours), 2) if hours else None
        volume = len(v["members"])
        session.add(
            Category(
                workspace_id=workspace_id,
                key=v["key"],
                name=v["name"],
                description=v["description"],
                volume=volume,
                avg_hours_to_resolve=avg_h,
                pain_score=round(volume * (avg_h or 0), 2),
                status="approved",
                approved_by="sandbox-auto",
                cluster_label=v["cluster_label"],
            )
        )
        for i in v["members"]:
            cases[i].category_key = v["key"]
            cases[i].embedding = emb.vectors[i]
            session.add(cases[i])
        out.append(
            {
                "key": v["key"],
                "name": v["name"],
                "description": v["description"],
                "volume": volume,
                "avg_hours": avg_h,
                "pain_score": round(volume * (avg_h or 0), 2),
            }
        )
    session.commit()
    out.sort(key=lambda c: -c["pain_score"])
    return {"method": method, "embedding_model": emb.model, "categories": out}


def apply_cached_taxonomy(
    session: Session, embedder: EmbeddingProvider, workspace_id: str, cached: dict[str, Any]
) -> dict[str, Any]:
    """Re-apply a cached taxonomy (categories + ticket assignments) and recompute embeddings without LLM calls."""
    cases = list(
        session.execute(select(Case).where(Case.workspace_id == workspace_id).order_by(Case.ticket_id)).scalars()
    )
    assignments: dict[str, str] = cached.get("assignments", {})
    emb = embedder.embed([case_text(c) for c in cases], step="taxonomy.embed", workspace_id=workspace_id)
    session.execute(delete(Category).where(Category.workspace_id == workspace_id))
    for cat in cached["categories"]:
        session.add(
            Category(
                workspace_id=workspace_id,
                key=cat["key"],
                name=cat["name"],
                description=cat.get("description"),
                volume=cat.get("volume", 0),
                avg_hours_to_resolve=cat.get("avg_hours"),
                pain_score=cat.get("pain_score"),
                status="approved",
                approved_by="sandbox-auto",
            )
        )
    for c, vec in zip(cases, emb.vectors, strict=True):
        c.category_key = assignments.get(c.ticket_id, c.category_key)
        c.embedding = vec
        session.add(c)
    session.commit()
    from ..resolve.planner import backfill_plan_embeddings

    backfill_plan_embeddings(session, embedder, workspace_id, only_missing=False)
    return {"method": cached.get("method"), "embedding_model": emb.model, "categories": cached["categories"]}
