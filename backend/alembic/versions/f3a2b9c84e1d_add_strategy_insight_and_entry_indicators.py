"""add strategy insight records and entry indicators

Revision ID: f3a2b9c84e1d
Revises: b8e2d194f3a1
Create Date: 2026-04-23 00:00:00.000000

Two changes in a single migration so they can be tested and rolled back together:

1. strategy_insight_records  — persists TradeRetrospective strategy-level confidence
   adjustments so they survive container restarts. Previously all learning was
   in-memory only; the scheduler started blind after every deploy.

2. positions.entry_indicators (JSON, nullable) — stores the 6-key indicator snapshot
   at position open time so retrospective analysis can correlate entry conditions
   with outcomes without post-hoc market_df approximation.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON


# revision identifiers, used by Alembic.
revision: str = 'f3a2b9c84e1d'
down_revision: Union[str, None] = 'b8e2d194f3a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. strategy_insight_records ──────────────────────────────────────────
    op.create_table(
        'strategy_insight_records',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('strategy_type', sa.String(64), nullable=False),
        sa.Column('is_paper', sa.Boolean(), nullable=False),
        sa.Column('confidence_adj', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('win_rate', sa.Float(), nullable=True),
        sa.Column('avg_win_pct', sa.Float(), nullable=True),
        sa.Column('avg_loss_pct', sa.Float(), nullable=True),
        sa.Column('trade_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('best_pattern', sa.String(100), nullable=True),
        sa.Column('worst_pattern', sa.String(100), nullable=True),
        sa.Column('strengths', JSON, nullable=True),
        sa.Column('weaknesses', JSON, nullable=True),
        sa.Column('confidence_adj_reason', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), onupdate=sa.text('now()')),
        sa.UniqueConstraint('strategy_type', 'is_paper', name='uq_strategy_insight_type_mode'),
        sa.PrimaryKeyConstraint('id'),
    )

    # ── 2. positions.entry_indicators ────────────────────────────────────────
    op.add_column(
        'positions',
        sa.Column('entry_indicators', JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_column('positions', 'entry_indicators')
    op.drop_table('strategy_insight_records')
