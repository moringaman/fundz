"""add venue to agents

Revision ID: cfc1988fcd46
Revises: eef8f0128a8b
Create Date: 2026-04-20 12:21:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cfc1988fcd46'
down_revision: Union[str, None] = 'eef8f0128a8b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'agents',
        sa.Column('venue', sa.String(length=20), nullable=True, server_default='phemex'),
    )


def downgrade() -> None:
    op.drop_column('agents', 'venue')
