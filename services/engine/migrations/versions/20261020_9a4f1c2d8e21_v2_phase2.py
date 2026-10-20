"""v2 phase 2: structured-case plan embeddings on historical cases

Revision ID: 9a4f1c2d8e21
Revises: 7c1e2a9b4d10
Create Date: 2026-10-20 20:00:00
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

revision: str = "9a4f1c2d8e21"
down_revision: str | None = "7c1e2a9b4d10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("plan_embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True))


def downgrade() -> None:
    op.drop_column("cases", "plan_embedding")
