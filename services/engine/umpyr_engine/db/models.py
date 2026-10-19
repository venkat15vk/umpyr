"""Postgres schema. Every tenant-scoped table carries workspace_id; queries must filter by it."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

EMBEDDING_DIM = 1024


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # slug, e.g. rockler
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="sandbox")  # sandbox | upload
    status: Mapped[str] = mapped_column(String(20), default="new")  # new | onboarding | ready
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # mode, auto_approve_limit, reply_channel
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)


class Upload(Base, TimestampMixin):
    __tablename__ = "uploads"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # tickets | policy | orders
    filename: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    storage_key: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | stored | mapped | ingested | failed
    mapping: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # column mapping proposal/approval
    row_count: Mapped[int | None] = mapped_column(Integer)


class Ticket(Base, TimestampMixin):
    __tablename__ = "tickets"
    __table_args__ = (UniqueConstraint("workspace_id", "ticket_id", name="uq_ticket_ws_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    ticket_id: Mapped[str] = mapped_column(String(100))
    ticket_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    channel: Mapped[str | None] = mapped_column(String(20))
    subject: Mapped[str | None] = mapped_column(Text)
    thread: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)  # masked
    agent_actions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    resolution: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    order_id: Mapped[str | None] = mapped_column(String(100), index=True)
    pii_masked: Mapped[bool] = mapped_column(Boolean, default=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    upload_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("uploads.id", ondelete="SET NULL"))


class OrderRecord(Base, TimestampMixin):
    """Merchant order/booking data mapped into the ontology (stand-in for live lookups)."""

    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("workspace_id", "order_id", name="uq_order_ws_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20), default="order")  # order | booking
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # ontology Order or Booking (+ customer)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)


class Category(Base, TimestampMixin):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("workspace_id", "key", name="uq_category_ws_key"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    volume: Mapped[int] = mapped_column(Integer, default=0)
    avg_hours_to_resolve: Mapped[float | None] = mapped_column(Numeric(10, 2))
    pain_score: Mapped[float | None] = mapped_column(Numeric(14, 2))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | merged
    merged_into: Mapped[str | None] = mapped_column(String(80))
    approved_by: Mapped[str | None] = mapped_column(String(120))
    cluster_label: Mapped[int | None] = mapped_column(Integer)


class Case(Base, TimestampMixin):
    __tablename__ = "cases"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    ticket_id: Mapped[str] = mapped_column(String(100), index=True)
    category_key: Mapped[str | None] = mapped_column(String(80), index=True)
    record: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # ontology Case
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # ontology Facts dict
    summary: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    hours_to_resolve: Mapped[float | None] = mapped_column(Numeric(10, 2))
    is_drift: Mapped[bool] = mapped_column(Boolean, default=False)
    drift_reason: Mapped[str | None] = mapped_column(Text)
    is_gold: Mapped[bool] = mapped_column(Boolean, default=False)
    split: Mapped[str | None] = mapped_column(String(10))  # train | test | held
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)


Index("ix_cases_ws_category", Case.workspace_id, Case.category_key)


class Procedure(Base, TimestampMixin):
    __tablename__ = "procedures"
    __table_args__ = (UniqueConstraint("workspace_id", "category_key", "version", name="uq_proc_ws_cat_ver"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    category_key: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(200))
    steps: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list
    )  # [{step_id, name, kind, clause_ids, required_facts, tool}]
    required_facts: Mapped[list[str]] = mapped_column(JSONB, default=list)
    outcomes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    mining: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # common path stats
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | approved
    approved_by: Mapped[str | None] = mapped_column(String(120))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PolicyDocument(Base, TimestampMixin):
    __tablename__ = "policy_documents"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    source_url: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(20))  # url | pdf | docx | md | html
    tier: Mapped[str] = mapped_column(String(20), default="contract")
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_path: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, default="")
    sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="fetched")  # fetched | compiled | gap


class Clause(Base, TimestampMixin):
    __tablename__ = "clauses"
    __table_args__ = (UniqueConstraint("workspace_id", "clause_id", name="uq_clause_ws_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    clause_id: Mapped[str] = mapped_column(String(40))
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("policy_documents.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text)
    tier: Mapped[str] = mapped_column(String(20))
    source_url: Mapped[str | None] = mapped_column(Text)
    ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)
    ambiguity_note: Mapped[str | None] = mapped_column(Text)


class RulePack(Base, TimestampMixin):
    __tablename__ = "rule_packs"
    __table_args__ = (UniqueConstraint("workspace_id", "version", name="uq_rulepack_ws_ver"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(40))
    pack: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # umpyr_rules.RulePack
    rule_count: Mapped[int] = mapped_column(Integer, default=0)
    conflicts: Mapped[list[str]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | active | retired


class Rule(Base, TimestampMixin):
    __tablename__ = "rules"
    __table_args__ = (UniqueConstraint("workspace_id", "rule_pack_id", "rule_id", name="uq_rule_ws_pack_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    rule_pack_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rule_packs.id", ondelete="CASCADE"), index=True)
    rule_id: Mapped[str] = mapped_column(String(60))
    clause_id: Mapped[str] = mapped_column(String(40))
    tier: Mapped[str] = mapped_column(String(20))
    rule: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class DriftFinding(Base, TimestampMixin):
    __tablename__ = "drift_findings"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))  # drift | unwritten_rule
    category_key: Mapped[str | None] = mapped_column(String(80))
    rule_id: Mapped[str | None] = mapped_column(String(60))
    clause_id: Mapped[str | None] = mapped_column(String(40))
    staff_outcome: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    policy_outcome: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    pattern: Mapped[str | None] = mapped_column(Text)  # for unwritten-rule candidates
    support: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(
        String(20), default="open"
    )  # open | policy_is_right | update_policy | adopt_as_rule | ignored
    note: Mapped[str | None] = mapped_column(Text)


class EvalScenario(Base, TimestampMixin):
    __tablename__ = "eval_scenarios"
    __table_args__ = (UniqueConstraint("workspace_id", "scenario_id", name="uq_scenario_ws_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    scenario_id: Mapped[str] = mapped_column(String(60))
    category_key: Mapped[str | None] = mapped_column(String(80))
    source_rule_id: Mapped[str | None] = mapped_column(String(60))
    ticket_text: Mapped[str] = mapped_column(Text)
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    expected: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # decision, remedy, amount, fault, clause_ids
    split: Mapped[str] = mapped_column(String(10), default="test")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)


class EvalRun(Base, TimestampMixin):
    __tablename__ = "eval_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    rule_pack_version: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="running")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)


class RouterModel(Base, TimestampMixin):
    __tablename__ = "router_models"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    blob: Mapped[bytes] = mapped_column(LargeBinary)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    labels: Mapped[list[str]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(20), default="active")


class ChatSession(Base, TimestampMixin):
    __tablename__ = "chat_sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="customer")  # customer | merchant
    channel: Mapped[str] = mapped_column(String(20), default="email")  # email | agent
    mode: Mapped[str] = mapped_column(String(20), default="autonomous")
    auto_approve_limit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("100"))
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # facts, category, procedure, step...
    status: Mapped[str] = mapped_column(String(20), default="open")


class ChatMessage(Base, TimestampMixin):
    __tablename__ = "chat_messages"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # customer | assistant | system | ops
    content: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # ops view snapshot / agent JSON


class RulingRecord(Base, TimestampMixin):
    """Audit record. `audit_hash` is the SHA-256 of the canonical JSON of the hashed fields."""

    __tablename__ = "rulings"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chat_sessions.id", ondelete="SET NULL"))
    ticket_id: Mapped[str | None] = mapped_column(String(100), index=True)
    category_key: Mapped[str | None] = mapped_column(String(80))
    procedure_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("procedures.id", ondelete="SET NULL"))
    procedure_version: Mapped[str | None] = mapped_column(String(80))  # "<category>@<version>"
    rule_pack_version: Mapped[str | None] = mapped_column(String(40))
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    ruling: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    authorization: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    supervisor: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    mode: Mapped[str | None] = mapped_column(String(20))
    audit_hash: Mapped[str] = mapped_column(String(64), index=True)


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(60), index=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)  # queued | running | done | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_by: Mapped[str | None] = mapped_column(String(120))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[int] = mapped_column(Integer, default=0)


class JobEvent(Base):
    __tablename__ = "job_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(64))
    seq: Mapped[int] = mapped_column(Integer)
    step: Mapped[str] = mapped_column(String(60))
    level: Mapped[str] = mapped_column(String(10), default="info")
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    progress: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LLMCall(Base):
    __tablename__ = "llm_calls"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    step: Mapped[str] = mapped_column(String(60), index=True)
    model: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(20), default="claude")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    is_batch: Mapped[bool] = mapped_column(Boolean, default=False)
    request_id: Mapped[str | None] = mapped_column(String(120))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    day: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DailySpend(Base):
    __tablename__ = "daily_spend"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    calls: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmbeddingCall(Base):
    __tablename__ = "embedding_calls"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    step: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(80))
    texts: Mapped[int] = mapped_column(Integer, default=0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    day: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
