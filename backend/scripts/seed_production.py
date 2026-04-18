"""
Production Seed Script
======================
Run this ONCE after first deployment to Railway (or any fresh PostgreSQL instance).
The app will not function correctly without a 'default-user' row in the users table
because many services use 'default-user' as a hardcoded user_id FK reference.

Usage (Railway Shell):
    python scripts/seed_production.py

Or via Railway CLI:
    railway run python scripts/seed_production.py
"""

import asyncio
import sys
import os
import uuid

# Ensure the backend app package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, text
from passlib.context import CryptContext

from app.database import engine, Base, get_async_session
from app.models import User, Balance, Trader, Agent  # noqa: F401 — registers all models

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ── Hardcoded IDs used throughout the codebase ────────────────────────────────
# Most routes use "default-user" as user_id for positions/trades/balances.
DEFAULT_USER_ID = "default-user"
# position_sync.py uses this ID when writing live Phemex positions to the DB.
POSITION_SYNC_USER_ID = "00000000-0000-0000-0000-000000000001"

ADMIN_USERNAME = "admin"
ADMIN_EMAIL = "admin@phemex-ai-trader.local"
ADMIN_PASSWORD = "ChangeMe123!"  # User should change this via the UI after first login

INITIAL_PAPER_BALANCE_USDT = 50_000.0  # Paper trading starting balance


async def ensure_tables():
    """Create all tables if they don't exist yet (idempotent — safe to run twice)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✓ Tables created / verified.")


async def seed_users():
    async with get_async_session() as db:
        # ── Default user (hardcoded in most routes) ───────────────────────────
        existing = await db.get(User, DEFAULT_USER_ID)
        if not existing:
            user = User(
                id=DEFAULT_USER_ID,
                username=ADMIN_USERNAME,
                email=ADMIN_EMAIL,
                hashed_password=pwd_context.hash(ADMIN_PASSWORD),
                is_active=True,
                is_superuser=True,
            )
            db.add(user)
            print(f"✓ Created user id='{DEFAULT_USER_ID}' (username='{ADMIN_USERNAME}')")
        else:
            print(f"  User id='{DEFAULT_USER_ID}' already exists — skipped.")

        # ── Position-sync user (used by live position sync service) ───────────
        existing_sync = await db.get(User, POSITION_SYNC_USER_ID)
        if not existing_sync:
            sync_user = User(
                id=POSITION_SYNC_USER_ID,
                username="position_sync",
                email="position_sync@phemex-ai-trader.local",
                hashed_password=pwd_context.hash(str(uuid.uuid4())),  # random — this account isn't used for login
                is_active=True,
                is_superuser=False,
            )
            db.add(sync_user)
            print(f"✓ Created position-sync user id='{POSITION_SYNC_USER_ID}'")
        else:
            print(f"  Position-sync user already exists — skipped.")

        await db.commit()


async def seed_balance():
    async with get_async_session() as db:
        result = await db.execute(
            select(Balance).where(
                Balance.user_id == DEFAULT_USER_ID,
                Balance.asset == "USDT",
            )
        )
        existing = result.scalar_one_or_none()
        if not existing:
            balance = Balance(
                id=str(uuid.uuid4()),
                user_id=DEFAULT_USER_ID,
                asset="USDT",
                available=INITIAL_PAPER_BALANCE_USDT,
                locked=0.0,
            )
            db.add(balance)
            await db.commit()
            print(f"✓ Created paper USDT balance: {INITIAL_PAPER_BALANCE_USDT:,.0f} USDT")
        else:
            print(f"  USDT balance already exists (available={existing.available}) — skipped.")


async def seed_traders():
    """Seed the three default fund traders (Alex, Jordan, Sam)."""
    async with get_async_session() as db:
        result = await db.execute(select(Trader))
        existing = result.scalars().all()
        if existing:
            print(f"  {len(existing)} trader(s) already exist — skipped.")
            return

        default_traders = [
            {
                "id": str(uuid.uuid4()),
                "name": "Alex",
                "llm_provider": "openrouter",
                "llm_model": "anthropic/claude-sonnet-4",
                "allocation_pct": 33.3,
                "is_enabled": True,
                "config": {"personality": "aggressive", "risk_appetite": "high"},
            },
            {
                "id": str(uuid.uuid4()),
                "name": "Jordan",
                "llm_provider": "openrouter",
                "llm_model": "openai/gpt-4o-mini",
                "allocation_pct": 33.3,
                "is_enabled": True,
                "config": {"personality": "balanced", "risk_appetite": "medium"},
            },
            {
                "id": str(uuid.uuid4()),
                "name": "Sam",
                "llm_provider": "openrouter",
                "llm_model": "mistralai/mixtral-8x7b-instruct",
                "allocation_pct": 33.4,
                "is_enabled": True,
                "config": {"personality": "conservative", "risk_appetite": "low"},
            },
        ]

        for t in default_traders:
            trader = Trader(**t)
            db.add(trader)

        await db.commit()
        names = ", ".join(t["name"] for t in default_traders)
        print(f"✓ Created traders: {names}")
        return [t["id"] for t in default_traders]


async def seed_agents(trader_ids: list[str] | None = None):
    """Seed one starter agent per strategy type (one per trader if trader_ids provided)."""
    async with get_async_session() as db:
        result = await db.execute(select(Agent))
        existing = result.scalars().all()
        if existing:
            print(f"  {len(existing)} agent(s) already exist — skipped.")
            return

        # Fetch traders if not passed in
        if not trader_ids:
            traders_result = await db.execute(select(Trader).where(Trader.is_enabled == True))
            traders = traders_result.scalars().all()
            trader_ids = [t.id for t in traders]

        strategy_types = [
            ("momentum", "Momentum Rider", 300),
            ("mean_reversion", "Mean Reversion", 600),
            ("breakout", "Breakout Hunter", 300),
        ]

        created = 0
        for idx, (strategy_type, name_suffix, interval) in enumerate(strategy_types):
            trader_id = trader_ids[idx % len(trader_ids)] if trader_ids else None
            agent = Agent(
                id=str(uuid.uuid4()),
                user_id=DEFAULT_USER_ID,
                trader_id=trader_id,
                name=name_suffix,
                strategy_type=strategy_type,
                config={
                    "trading_pairs": [],  # uses global trading_pairs setting
                    "timeframe": "1h",
                    "stop_loss_pct": 3.0,
                    "take_profit_pct": 6.0,
                    "trailing_stop_pct": 1.5,
                },
                is_enabled=True,
                allocation_percentage=10.0,
                max_position_size=1000.0,
                risk_limit=100.0,
                run_interval_seconds=interval,
            )
            db.add(agent)
            created += 1

        await db.commit()
        print(f"✓ Created {created} starter agents.")


async def main():
    print("═" * 60)
    print(" Phemex AI Trader — Production Seed")
    print("═" * 60)

    await ensure_tables()
    await seed_users()
    await seed_balance()
    trader_ids = await seed_traders()
    await seed_agents(trader_ids)

    print()
    print("═" * 60)
    print(" Seed complete.")
    print(f" Default login: username='{ADMIN_USERNAME}' password='{ADMIN_PASSWORD}'")
    print(" IMPORTANT: Change the admin password after first login.")
    print("═" * 60)


if __name__ == "__main__":
    asyncio.run(main())
