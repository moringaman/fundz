"""add regime_states table

Revision ID: f0a7b4d56e38
Revises: e9f6a3c45c27
Create Date: 2026-04-25 00:05:00.000000

Phase 4 quant rigour — Gaussian Mixture regime classification persistence.
Idempotent CREATE TABLE per repo convention.
"""

from typing import Sequence, Union

from alembic import op


revision: str = 'f0a7b4d56e38'
down_revision: Union[str, None] = 'e9f6a3c45c27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS regime_states (
            id VARCHAR(36) PRIMARY KEY,
            symbol VARCHAR(20) NOT NULL,
            timeframe VARCHAR(10) DEFAULT '1h',
            regime_label VARCHAR(20) NOT NULL,
            confidence FLOAT DEFAULT 0.0,
            posteriors JSONB DEFAULT '{}'::jsonb,
            label_centroids JSONB DEFAULT '{}'::jsonb,
            model_fingerprint VARCHAR(32),
            n_samples_fit INTEGER DEFAULT 0,
            feature_window INTEGER DEFAULT 24,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_regime_symbol_created ON regime_states (symbol, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_regime_created_at ON regime_states (created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS regime_states")
