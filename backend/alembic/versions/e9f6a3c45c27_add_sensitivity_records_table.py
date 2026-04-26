"""add sensitivity_records table

Revision ID: e9f6a3c45c27
Revises: d8e5f1a23b16
Create Date: 2026-04-25 00:02:00.000000

Phase 3 quant rigour — strategy sensitivity sweep persistence. Idempotent
CREATE TABLE per repo convention; the live app already calls Base.metadata
.create_all() at startup so this migration mainly exists for fresh
provisioning and downgrade support.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e9f6a3c45c27'
down_revision: Union[str, None] = 'd8e5f1a23b16'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS sensitivity_records (
            id VARCHAR(36) PRIMARY KEY,
            agent_id VARCHAR(36) REFERENCES agents(id) ON DELETE SET NULL,
            symbol VARCHAR(20) NOT NULL,
            strategy VARCHAR(50) NOT NULL,
            interval VARCHAR(10) DEFAULT '1h',
            axis_x VARCHAR(30) NOT NULL,
            axis_y VARCHAR(30) NOT NULL,
            chosen_x_value FLOAT NOT NULL,
            chosen_y_value FLOAT NOT NULL,
            chosen_sharpe FLOAT DEFAULT 0.0,
            chosen_net_pnl FLOAT DEFAULT 0.0,
            chosen_max_dd FLOAT DEFAULT 0.0,
            stability_score FLOAT,
            stability_tier VARCHAR(20) DEFAULT 'unknown',
            n_cells_total INTEGER DEFAULT 0,
            n_cells_valid INTEGER DEFAULT 0,
            surface JSONB DEFAULT '{}'::jsonb,
            source VARCHAR(30) DEFAULT 'manual',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_sensitivity_agent_id ON sensitivity_records (agent_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_sensitivity_strategy ON sensitivity_records (strategy)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_sensitivity_created_at ON sensitivity_records (created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sensitivity_records")
