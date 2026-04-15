"""initial_schema

Revision ID: 001
Revises:
Create Date: 2026-04-15 12:48:41.254325

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "issues",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("status", sa.Text, server_default="new", nullable=False),
        sa.Column("stage", sa.Text, server_default="spec", nullable=False),
        sa.Column("assignee", sa.Text, server_default=""),
        sa.Column("labels", sa.Text, server_default="[]"),
        sa.Column("filepath", sa.Text, server_default=""),
        sa.Column("created", sa.Text, server_default=""),
        sa.Column("pause_reason", sa.Text, server_default=""),
        sa.Column("updated_at", sa.TIMESTAMP, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    op.create_table(
        "stage_results",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("issue_id", sa.Text, sa.ForeignKey("issues.id"), nullable=False),
        sa.Column("repo", sa.Text, server_default="primary"),
        sa.Column("stage", sa.Text, nullable=False),
        sa.Column("passed", sa.Integer, nullable=False),
        sa.Column("output", sa.Text, server_default=""),
        sa.Column("error", sa.Text, server_default=""),
        sa.Column("artifacts", sa.Text, server_default="[]"),
        sa.Column("started_at", sa.Text, nullable=True),
        sa.Column("finished_at", sa.Text, nullable=True),
    )

    op.create_table(
        "harness_iterations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("issue_id", sa.Text, sa.ForeignKey("issues.id"), nullable=False),
        sa.Column("repo", sa.Text, server_default="primary"),
        sa.Column("attempt", sa.Integer, nullable=False),
        sa.Column("stage", sa.Text, nullable=False),
        sa.Column("exit_code", sa.Integer, nullable=False),
        sa.Column("output", sa.Text, server_default=""),
        sa.Column("error", sa.Text, server_default=""),
        sa.Column("previous_errors", sa.Text, server_default="[]"),
        sa.Column("created_at", sa.TIMESTAMP, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    op.create_table(
        "session_turns",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("issue_id", sa.Text, sa.ForeignKey("issues.id"), nullable=False),
        sa.Column("stage", sa.Text, nullable=False),
        sa.Column("attempt", sa.Integer, nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("metadata", sa.Text, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    op.create_table(
        "agent_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("issue_id", sa.Text, sa.ForeignKey("issues.id"), nullable=False),
        sa.Column("stage", sa.Text, nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("content", sa.Text, server_default=""),
        sa.Column("metadata", sa.Text, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP, server_default=sa.text("CURRENT_TIMESTAMP")),
    )


def downgrade() -> None:
    op.drop_table("agent_events")
    op.drop_table("session_turns")
    op.drop_table("harness_iterations")
    op.drop_table("stage_results")
    op.drop_table("issues")
