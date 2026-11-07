"""wholesaler owner name

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("wholesalers", sa.Column("owner_name", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("wholesalers", "owner_name")
