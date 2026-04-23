"""add agent pnl suspension fields

Revision ID: a3f9c821d4b7
Revises: cfc1988fcd46
Create Date: 2026-04-21 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f9c821d4b7'
# Merge of both branch heads: leverage fields (4b1a6f4d9c2e) and venue column (cfc1988fcd46).
# Both branched from the same initial migration (eef8f0128a8b) so we merge here.
down_revision: Union[str, tuple] = ('4b1a6f4d9c2e', 'cfc1988fcd46')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_pnl_suspended BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS pnl_suspended_reason TEXT")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS pnl_suspended_at TIMESTAMP WITH TIME ZONE")


def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS pnl_suspended_at")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS pnl_suspended_reason")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS is_pnl_suspended")
