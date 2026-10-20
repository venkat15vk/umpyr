"""v2 phase 1: clause provenance, cost per case, resolve_cases + append-only case_events

Revision ID: 7c1e2a9b4d10
Revises: 3d7ad63f49d8
Create Date: 2026-10-20 18:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7c1e2a9b4d10"
down_revision: str | None = "3d7ad63f49d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY = """
CREATE OR REPLACE FUNCTION case_events_append_only() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' AND NOT EXISTS (SELECT 1 FROM resolve_cases WHERE id = OLD.case_id) THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION USING MESSAGE = 'case_events is append-only (refused ' || TG_OP || ')';
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS case_events_no_update ON case_events;
CREATE TRIGGER case_events_no_update BEFORE UPDATE OR DELETE ON case_events
  FOR EACH ROW EXECUTE FUNCTION case_events_append_only();
"""


def upgrade() -> None:
    # clause provenance
    with op.batch_alter_table("clauses") as b:
        b.add_column(sa.Column("document_version", sa.String(length=64), nullable=True))
        b.add_column(sa.Column("section", sa.Text(), nullable=True))
        b.add_column(sa.Column("page", sa.Integer(), nullable=True))
        b.add_column(sa.Column("span_start", sa.Integer(), nullable=True))
        b.add_column(sa.Column("span_end", sa.Integer(), nullable=True))
        b.add_column(sa.Column("text_hash", sa.String(length=64), nullable=True))
        b.add_column(sa.Column("public", sa.Boolean(), nullable=False, server_default=sa.true()))
    # cost per case
    op.add_column("llm_calls", sa.Column("case_id", sa.UUID(), nullable=True))
    op.add_column("llm_calls", sa.Column("session_id", sa.UUID(), nullable=True))
    op.create_index("ix_llm_calls_case_id", "llm_calls", ["case_id"])
    # cases
    op.create_table(
        "resolve_cases",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("category_key", sa.String(length=80), nullable=True),
        sa.Column("reference", sa.String(length=60), nullable=True),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("escalation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("turns", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("llm_calls", sa.Integer(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("head_hash", sa.String(length=64), nullable=True),
        sa.Column("decision_hash", sa.String(length=64), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_resolve_cases_workspace_id", "resolve_cases", ["workspace_id"])
    op.create_index("ix_resolve_cases_session_id", "resolve_cases", ["session_id"], unique=True)
    # ledger
    op.create_table(
        "case_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("turn", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(length=30), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("basis", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=True),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["resolve_cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "seq", name="uq_case_event_seq"),
    )
    op.create_index("ix_case_events_case_id", "case_events", ["case_id"])
    op.create_index("ix_case_events_workspace_id", "case_events", ["workspace_id"])
    op.create_index("ix_case_events_action", "case_events", ["action"])
    op.create_index("ix_case_events_hash", "case_events", ["hash"])
    op.execute(APPEND_ONLY)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS case_events_no_update ON case_events")
    op.execute("DROP FUNCTION IF EXISTS case_events_append_only()")
    op.drop_table("case_events")
    op.drop_table("resolve_cases")
    op.drop_index("ix_llm_calls_case_id", table_name="llm_calls")
    op.drop_column("llm_calls", "session_id")
    op.drop_column("llm_calls", "case_id")
    with op.batch_alter_table("clauses") as b:
        for col in ("public", "text_hash", "span_end", "span_start", "page", "section", "document_version"):
            b.drop_column(col)
