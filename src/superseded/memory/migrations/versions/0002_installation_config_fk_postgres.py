"""add installation_config foreign key on postgres

Reconciliation for pre-Alembic Postgres databases: the legacy hand-written
Postgres SCHEMA lacked the ``installation_config -> installations`` foreign key
that the SQLite SCHEMA had and that the models now define. Fresh databases get
this FK in 0001; this migration adds it idempotently to existing Postgres DBs.

No-op on SQLite (cannot ALTER TABLE ADD CONSTRAINT; existing SQLite DBs keep
their pre-Alembic shape, as documented in 0001).

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-07
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CONSTRAINT_NAME = "installation_config_installation_id_fkey"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    existing = bind.execute(
        "SELECT 1 FROM pg_constraint WHERE conname = :name", {"name": _CONSTRAINT_NAME}
    ).scalar()
    if existing:
        return

    op.create_foreign_key(
        _CONSTRAINT_NAME,
        source_table="installation_config",
        referent_table="installations",
        local_cols=["installation_id"],
        remote_cols=["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.drop_constraint(_CONSTRAINT_NAME, "installation_config", type_="foreignkey")
