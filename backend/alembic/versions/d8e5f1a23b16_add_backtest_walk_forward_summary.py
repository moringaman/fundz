"""add backtest walk_forward_summary

Revision ID: d8e5f1a23b16
Revises: c7d4e9b21a05
Create Date: 2026-04-25 00:01:00.000000

Phase 2 quant rigour — walk-forward analysis output. Idempotent per repo
convention so it survives main.py runtime DDL collisions.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd8e5f1a23b16'
down_revision: Union[str, None] = 'c7d4e9b21a05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE backtest_records ADD COLUMN IF NOT EXISTS walk_forward_summary JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE backtest_records DROP COLUMN IF EXISTS walk_forward_summary")
