"""v2 phase 3: next-question model, approval queue due time

Revision ID: b3d7e5f9a102
Revises: 9a4f1c2d8e21
Create Date: 2026-10-20 21:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3d7e5f9a102"
down_revision: str | None = "9a4f1c2d8e21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "next_questions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("category_key", sa.String(length=80), nullable=False),
        sa.Column("question", sa.String(length=40), nullable=False),
        sa.Column("probability", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("support", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "category_key", "question", name="uq_next_question"),
    )
    op.create_index("ix_next_questions_workspace_id", "next_questions", ["workspace_id"])
    op.add_column("resolve_cases", sa.Column("due_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("resolve_cases", "due_at")
    op.drop_table("next_questions")
