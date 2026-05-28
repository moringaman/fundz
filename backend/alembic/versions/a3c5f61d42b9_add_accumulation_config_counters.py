"""add dca_count, va_count, dip_count to accumulation_config

Revision ID: a3c5f61d42b9
Revises: e5d9f2a43b71
Create Date: 2026-05-07 11:00:00.000000

Idempotent ALTER TABLE + backfill per repo convention.
"""

from typing import Sequence, Union

from alembic import op


revision: str = 'a3c5f61d42b9'
down_revision: Union[str, None] = 'e5d9f2a43b71'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE accumulation_config ADD COLUMN IF NOT EXISTS dca_count INTEGER DEFAULT 0 NOT NULL")
    op.execute("ALTER TABLE accumulation_config ADD COLUMN IF NOT EXISTS va_count INTEGER DEFAULT 0 NOT NULL")
    op.execute("ALTER TABLE accumulation_config ADD COLUMN IF NOT EXISTS dip_count INTEGER DEFAULT 0 NOT NULL")

    # Backfill: if a strategy was enabled and had a next_at scheduled,
    # assume at least 1 historical run occurred
    op.execute("""
        UPDATE accumulation_config
        SET dca_count = 1
        WHERE dca_enabled = true AND dca_next_at IS NOT NULL AND dca_count = 0
    """)
    op.execute("""
        UPDATE accumulation_config
        SET va_count = 1
        WHERE va_enabled = true AND va_next_at IS NOT NULL AND va_count = 0
    """)
    op.execute("""
        UPDATE accumulation_config
        SET dip_count = 1
        WHERE dip_enabled = true AND dip_count = 0
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE accumulation_config DROP COLUMN IF EXISTS dip_count")
    op.execute("ALTER TABLE accumulation_config DROP COLUMN IF EXISTS va_count")
    op.execute("ALTER TABLE accumulation_config DROP COLUMN IF EXISTS dca_count")
