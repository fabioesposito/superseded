"""PR-scoped convergence schema

Add the ``pr_findings`` table (per-PR finding state keyed by fingerprint) and
the ``review_round`` counter column on ``review_watermarks``.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-19
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pr_findings",
        sa.Column("repo", sa.String(), nullable=False),
        sa.Column("pr_number", sa.BigInteger(), nullable=False),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("finding_id", sa.String(), nullable=False),
        sa.Column("pass", sa.String(), nullable=True),
        sa.Column("severity", sa.String(), nullable=True),
        sa.Column("file", sa.String(), nullable=True),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("comment_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(), server_default="open", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("repo", "pr_number", "fingerprint"),
    )
    op.create_index("ix_pr_findings_comment_id", "pr_findings", ["comment_id"])
    op.add_column(
        "review_watermarks",
        sa.Column("review_round", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_index("ix_pr_findings_comment_id", table_name="pr_findings")
    op.drop_table("pr_findings")
    # SQLite cannot drop columns; keep downgrade safe on SQLite.
    if op.get_bind().dialect.name != "sqlite":
        op.drop_column("review_watermarks", "review_round")
