"""add backtest mc_summary

Revision ID: c7d4e9b21a05
Revises: f3a2b9c84e1d
Create Date: 2026-04-25 00:00:00.000000

Adds Monte Carlo bootstrap result column to backtest_records. Idempotent
(IF NOT EXISTS) so it survives environments where main.py's runtime DDL
beat Alembic to the punch — see /memories/repo/schema-migrations.md.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7d4e9b21a05'
down_revision: Union[str, None] = 'f3a2b9c84e1d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE backtest_records ADD COLUMN IF NOT EXISTS mc_summary JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE backtest_records DROP COLUMN IF EXISTS mc_summary")
