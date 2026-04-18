"""
Shared pytest fixtures for all backend tests.

Provides an async in-memory SQLite database so tests can exercise
SQLAlchemy models and services without a running Postgres instance.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import event

from app.database import Base


# ---------------------------------------------------------------------------
# In-memory SQLite engine & session factory
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def engine():
    """Create a single in-memory SQLite engine for the whole test session."""
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )


@pytest_asyncio.fixture(scope="session", autouse=True)
async def create_tables(engine):
    """Create all ORM tables once for the session."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(session_factory):
    """Return a fresh async session for each test, rolled back on teardown."""
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
def patch_get_async_session(db_session, monkeypatch):
    """
    Patch app.database.get_async_session so paper_trading / live_trading
    services use the test session instead of a real Postgres connection.
    """
    @asynccontextmanager
    async def _fake_session():
        yield db_session

    monkeypatch.setattr("app.database.get_async_session", _fake_session)
    monkeypatch.setattr("app.services.paper_trading.get_async_session", _fake_session)
    return _fake_session


@pytest.fixture
def mock_phemex_client():
    """A mock PhemexClient that does not make real HTTP calls."""
    client = MagicMock()
    client.get_ticker = AsyncMock(return_value={"result": {"closeRp": "50000"}})
    client.set_leverage = AsyncMock(return_value={})
    client.place_order = AsyncMock(return_value={"orderId": "mock-order-id"})
    return client
