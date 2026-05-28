"""add accumulation_execution_records table

Revision ID: e5d9f2a43b71
Revises: f0a7b4d56e38
Create Date: 2026-05-07 10:00:00.000000

Idempotent CREATE TABLE per repo convention.
"""

from typing import Sequence, Union

from alembic import op


revision: str = 'e5d9f2a43b71'
down_revision: Union[str, None] = 'f0a7b4d56e38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS accumulation_execution_records (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL REFERENCES users(id),
            asset VARCHAR(20) NOT NULL,
            strategy VARCHAR(20) NOT NULL,
            amount_usd FLOAT DEFAULT 0,
            quantity FLOAT DEFAULT 0,
            price FLOAT DEFAULT 0,
            details JSONB DEFAULT '{}'::jsonb,
            executed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_acc_exec_asset_strategy ON accumulation_execution_records (asset, strategy)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_acc_exec_executed_at ON accumulation_execution_records (executed_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS accumulation_execution_records")
