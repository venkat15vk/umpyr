"""Router: logistic regression on case embeddings with calibrated probabilities.

Below the confidence threshold (0.70) the runtime falls back to Haiku classification (M3).
"""

from __future__ import annotations

import logging
import pickle
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Case, RouterModel
from ..embeddings import EmbeddingProvider

log = logging.getLogger(__name__)

THRESHOLD = 0.70


class Router:
    def __init__(self, model, labels: list[str], embedder_name: str):
        self.model = model
        self.labels = labels
        self.embedder_name = embedder_name

    def predict(self, vectors: list[list[float]]) -> list[tuple[str, float, dict[str, float]]]:
        probs = self.model.predict_proba(np.array(vectors, dtype=np.float32))
        out = []
        for p in probs:
            i = int(np.argmax(p))
            out.append((self.labels[i], float(p[i]), {lab: float(v) for lab, v in zip(self.labels, p, strict=True)}))
        return out


def train_router(session: Session, embedder: EmbeddingProvider, workspace_id: str, *, seed: int = 0) -> dict[str, Any]:
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, f1_score
    from sklearn.model_selection import train_test_split

    cases = [
        c
        for c in session.execute(select(Case).where(Case.workspace_id == workspace_id)).scalars()
        if c.category_key and c.embedding is not None
    ]
    if len(cases) < 20:
        raise RuntimeError("need at least 20 categorised cases to train the router")
    x = np.array([list(c.embedding) for c in cases], dtype=np.float32)
    y = np.array([c.category_key for c in cases])
    labels = sorted(set(y))
    counts = {lab: int((y == lab).sum()) for lab in labels}
    stratify = y if min(counts.values()) >= 2 else None
    xtr, xte, ytr, yte = train_test_split(x, y, test_size=0.2, random_state=seed, stratify=stratify)
    base = LogisticRegression(max_iter=3000, C=2.0)
    min_train = min(int((ytr == lab).sum()) for lab in labels)
    if min_train >= 6:
        model = CalibratedClassifierCV(base, cv=3, method="sigmoid").fit(xtr, ytr)
        kind = "logreg+sigmoid-calibration"
    else:
        model = base.fit(xtr, ytr)
        kind = "logreg"
    classes = list(model.classes_)
    probs = model.predict_proba(xte)
    pred = np.array([classes[int(i)] for i in probs.argmax(axis=1)])
    conf = probs.max(axis=1)
    covered = conf >= THRESHOLD
    metrics = {
        "model": kind,
        "n_train": int(len(ytr)),
        "n_test": int(len(yte)),
        "accuracy": round(float(accuracy_score(yte, pred)), 3),
        "macro_f1": round(float(f1_score(yte, pred, average="macro")), 3),
        "threshold": THRESHOLD,
        "coverage_at_threshold": round(float(covered.mean()), 3),
        "accuracy_at_threshold": round(float(accuracy_score(yte[covered], pred[covered])), 3)
        if covered.any()
        else None,
        "class_counts": counts,
    }
    # refit on everything for deployment
    if min(counts.values()) >= 6:
        final = CalibratedClassifierCV(LogisticRegression(max_iter=3000, C=2.0), cv=3, method="sigmoid").fit(x, y)
    else:
        final = LogisticRegression(max_iter=3000, C=2.0).fit(x, y)
    router = Router(final, list(final.classes_), embedder.name)
    for old in session.execute(
        select(RouterModel).where(RouterModel.workspace_id == workspace_id, RouterModel.status == "active")
    ).scalars():
        old.status = "retired"
    version = 1 + len(
        list(session.execute(select(RouterModel.id).where(RouterModel.workspace_id == workspace_id)).all())
    )
    session.add(
        RouterModel(
            workspace_id=workspace_id,
            version=version,
            blob=pickle.dumps(router),
            metrics=metrics,
            labels=router.labels,
            status="active",
        )
    )
    session.commit()
    log.info("router v%d: %s", version, metrics)
    return metrics


def load_router(session: Session, workspace_id: str) -> Router | None:
    row = (
        session.execute(
            select(RouterModel)
            .where(RouterModel.workspace_id == workspace_id, RouterModel.status == "active")
            .order_by(RouterModel.version.desc())
        )
        .scalars()
        .first()
    )
    return pickle.loads(row.blob) if row else None
