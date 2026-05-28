"""add regime attribution columns

Captures the GMM regime label active at trade entry on `trades` and
`positions`, and adds a per-regime breakdown JSON to `agent_metric_records`
mirroring the existing `venue_stats` shape. Lets the system answer
"what's this agent's win rate when regime=risk_off?" without a post-hoc
join against `regime_states`.

Revision ID: c8a1f3e7d4b2
Revises: a3c5f61d42b9
Create Date: 2026-05-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8a1f3e7d4b2"
down_revision: Union[str, None] = "a3c5f61d42b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # trades
    op.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_regime VARCHAR(20)")
    op.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_regime_confidence DOUBLE PRECISION")

    # positions
    op.execute("ALTER TABLE positions ADD COLUMN IF NOT EXISTS entry_regime VARCHAR(20)")
    op.execute("ALTER TABLE positions ADD COLUMN IF NOT EXISTS entry_regime_confidence DOUBLE PRECISION")

    # agent_metric_records.regime_stats — JSON, default empty dict
    op.execute(
        "ALTER TABLE agent_metric_records "
        "ADD COLUMN IF NOT EXISTS regime_stats JSONB NOT NULL DEFAULT '{}'::jsonb"
    )

    # Useful index for filtering closed positions by entry regime when
    # building per-strategy/regime cohorts.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_positions_agent_entry_regime "
        "ON positions (agent_id, entry_regime)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_positions_agent_entry_regime")
    op.execute("ALTER TABLE agent_metric_records DROP COLUMN IF EXISTS regime_stats")
    op.execute("ALTER TABLE positions DROP COLUMN IF EXISTS entry_regime_confidence")
    op.execute("ALTER TABLE positions DROP COLUMN IF EXISTS entry_regime")
    op.execute("ALTER TABLE trades DROP COLUMN IF EXISTS entry_regime_confidence")
    op.execute("ALTER TABLE trades DROP COLUMN IF EXISTS entry_regime")
