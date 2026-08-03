"""verification columns

Add verification / verification_reason columns to findings table and
source column to feedback table.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-03
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('findings', sa.Column('verification', sa.String(), nullable=True))
    op.add_column('findings', sa.Column('verification_reason', sa.String(), nullable=True))
    op.add_column('feedback', sa.Column('source', sa.String(), server_default='human', nullable=False))


def downgrade() -> None:
    # SQLite cannot drop columns; no-op to keep downgrade safe.
    pass
