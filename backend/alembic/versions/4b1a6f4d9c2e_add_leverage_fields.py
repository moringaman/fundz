"""add leverage fields

Revision ID: 4b1a6f4d9c2e
Revises: eef8f0128a8b
Create Date: 2026-04-17 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4b1a6f4d9c2e'
down_revision: Union[str, None] = 'eef8f0128a8b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS leverage FLOAT DEFAULT 1.0")
    op.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS margin_used FLOAT DEFAULT 0.0")
    op.execute("ALTER TABLE positions ADD COLUMN IF NOT EXISTS leverage FLOAT DEFAULT 1.0")
    op.execute("ALTER TABLE positions ADD COLUMN IF NOT EXISTS margin_used FLOAT DEFAULT 0.0")
    op.execute("ALTER TABLE positions ADD COLUMN IF NOT EXISTS liquidation_price FLOAT")


def downgrade() -> None:
    op.execute("ALTER TABLE positions DROP COLUMN IF EXISTS liquidation_price")
    op.execute("ALTER TABLE positions DROP COLUMN IF EXISTS margin_used")
    op.execute("ALTER TABLE positions DROP COLUMN IF EXISTS leverage")
    op.execute("ALTER TABLE trades DROP COLUMN IF EXISTS margin_used")
    op.execute("ALTER TABLE trades DROP COLUMN IF EXISTS leverage")