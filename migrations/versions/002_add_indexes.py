"""add indexes for common query patterns

Revision ID: 002_add_indexes
Revises: 001_initial_schema
Create Date: 2026-04-21
"""

from alembic import op

revision = "002_add_indexes"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS idx_stage_results_issue_id ON stage_results(issue_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_events_issue_id ON agent_events(issue_id, id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_turns_issue_id ON session_turns(issue_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_harness_iterations_issue_id ON harness_iterations(issue_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_stage_results_issue_id")
    op.execute("DROP INDEX IF EXISTS idx_agent_events_issue_id")
    op.execute("DROP INDEX IF EXISTS idx_session_turns_issue_id")
    op.execute("DROP INDEX IF EXISTS idx_harness_iterations_issue_id")
