"""add daily_report email_sent_at

Revision ID: b8e2d194f3a1
Revises: a3f9c821d4b7
Create Date: 2026-04-21 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8e2d194f3a1'
down_revision: Union[str, None] = 'a3f9c821d4b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Persisted so the "already sent today" guard survives container restarts.
    # NULL means no email has been sent for this report date.
    op.execute("ALTER TABLE daily_reports ADD COLUMN IF NOT EXISTS email_sent_at TIMESTAMP WITH TIME ZONE")


def downgrade() -> None:
    op.execute("ALTER TABLE daily_reports DROP COLUMN IF EXISTS email_sent_at")
