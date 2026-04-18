"""
Tests for PaperTradingService leverage mechanics.

Covers:
  - leveraged long: open, profitable close, loss close
  - leveraged short: open, profitable cover, loss cover
  - partial close of a leveraged position (proportional margin release)
  - unleveraged baseline: behaviour unchanged when leverage == 1.0
  - additive position sizing when adding to an existing leveraged long
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from unittest.mock import patch

from sqlalchemy import select

from app.models import (
    Balance as PaperBalance,
    Position as PaperPosition,
    Trade as PaperOrder,
    OrderSide,
)
from app.services.paper_trading import PaperTradingService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

INITIAL_BALANCE = 10_000.0
SYMBOL = "BTCUSDT"
AGENT_ID = "test-agent"
FEE_RATE = PaperTradingService.SPOT_FEE_RATE  # 0.001


async def _seed_balance(db, amount: float = INITIAL_BALANCE) -> PaperBalance:
    """Insert a fresh USDT balance for the default user."""
    bal = PaperBalance(user_id="default-user", asset="USDT", available=amount, locked=0.0)
    db.add(bal)
    await db.commit()
    await db.refresh(bal)
    return bal


async def _get_balance(db) -> float:
    bal = await db.scalar(
        select(PaperBalance).where(
            PaperBalance.user_id == "default-user",
            PaperBalance.asset == "USDT",
        )
    )
    return bal.available if bal else 0.0


async def _get_position(db, side: OrderSide) -> PaperPosition | None:
    return await db.scalar(
        select(PaperPosition).where(
            PaperPosition.user_id == "default-user",
            PaperPosition.symbol == SYMBOL,
            PaperPosition.agent_id == AGENT_ID,
            PaperPosition.side == side,
        )
    )


def _make_service(mock_phemex_client) -> PaperTradingService:
    svc = PaperTradingService(phemex_client=mock_phemex_client)
    return svc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def clean_tables(db_session):
    """Truncate positions, trades, and balances before each test."""
    from sqlalchemy import delete
    from app.models import Trade, Position, Balance
    for model in (Trade, Position, Balance):
        await db_session.execute(delete(model))
    await db_session.commit()


@pytest.fixture
def svc(mock_phemex_client, patch_get_async_session) -> PaperTradingService:
    return _make_service(mock_phemex_client)


# ---------------------------------------------------------------------------
# Leveraged long: open
# ---------------------------------------------------------------------------

class TestLeveragedLongOpen:
    async def test_margin_deducted_not_full_notional(self, svc, db_session):
        """Opening a 2x leveraged long should deduct margin (notional/2), not full notional."""
        await _seed_balance(db_session, INITIAL_BALANCE)
        entry_price = 1000.0
        qty = 5.0
        leverage = 2.0
        notional = qty * entry_price           # 5_000
        margin = notional / leverage           # 2_500
        fee = notional * FEE_RATE              # 5

        await svc.place_order(
            symbol=SYMBOL,
            side=OrderSide.BUY,
            quantity=qty,
            price=entry_price,
            agent_id=AGENT_ID,
            leverage=leverage,
            margin_used=margin,
        )

        balance_after = await _get_balance(db_session)
        # Initial balance minus margin minus fee
        expected = INITIAL_BALANCE - margin - fee
        assert abs(balance_after - expected) < 0.01, (
            f"Expected ~{expected:.2f}, got {balance_after:.2f}"
        )

    async def test_position_leverage_stored(self, svc, db_session):
        await _seed_balance(db_session)
        entry_price = 1000.0
        qty = 2.0
        leverage = 3.0
        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.BUY, quantity=qty,
            price=entry_price, agent_id=AGENT_ID,
            leverage=leverage, margin_used=qty * entry_price / leverage,
        )
        pos = await _get_position(db_session, OrderSide.BUY)
        assert pos is not None
        assert abs(pos.leverage - leverage) < 1e-6
        assert abs(pos.margin_used - (qty * entry_price / leverage)) < 0.01

    async def test_liquidation_price_stored(self, svc, db_session):
        await _seed_balance(db_session)
        liq_price = 600.0
        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.BUY, quantity=2.0,
            price=1000.0, agent_id=AGENT_ID,
            leverage=3.0, margin_used=666.67, liquidation_price=liq_price,
        )
        pos = await _get_position(db_session, OrderSide.BUY)
        assert pos is not None
        assert abs(pos.liquidation_price - liq_price) < 0.01


# ---------------------------------------------------------------------------
# Leveraged long: profitable close
# ---------------------------------------------------------------------------

class TestLeveragedLongClose:
    async def test_profitable_close_returns_margin_plus_profit(self, svc, db_session):
        """Closing a leveraged long at a profit credits margin_release + realized P&L."""
        await _seed_balance(db_session, INITIAL_BALANCE)
        entry = 1000.0
        exit_price = 1100.0
        qty = 5.0
        leverage = 2.0
        notional = qty * entry
        margin = notional / leverage           # 2_500
        open_fee = notional * FEE_RATE         # 5

        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.BUY, quantity=qty,
            price=entry, agent_id=AGENT_ID,
            leverage=leverage, margin_used=margin,
        )
        balance_after_open = await _get_balance(db_session)

        pos = await _get_position(db_session, OrderSide.BUY)
        assert pos is not None

        # Close the position manually via a SELL
        close_notional = qty * exit_price
        close_fee = close_notional * FEE_RATE
        raw_pnl = (exit_price - entry) * qty   # 500

        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.SELL, quantity=qty,
            price=exit_price, agent_id=AGENT_ID,
        )

        balance_after_close = await _get_balance(db_session)
        # After close: balance_after_open + margin_release + raw_pnl - one_fee
        # (open fee already deducted at open; close fee deducted once at close top-level)
        close_fee = qty * exit_price * FEE_RATE
        expected = balance_after_open + margin + raw_pnl - close_fee
        assert abs(balance_after_close - expected) < 0.10, (
            f"Expected ~{expected:.2f}, got {balance_after_close:.2f}"
        )

    async def test_loss_close_returns_margin_minus_loss(self, svc, db_session):
        """Closing a leveraged long at a loss credits margin_release minus realized loss."""
        await _seed_balance(db_session, INITIAL_BALANCE)
        entry = 1000.0
        exit_price = 900.0
        qty = 5.0
        leverage = 2.0
        margin = qty * entry / leverage        # 2_500

        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.BUY, quantity=qty,
            price=entry, agent_id=AGENT_ID,
            leverage=leverage, margin_used=margin,
        )
        balance_after_open = await _get_balance(db_session)

        close_fee = qty * exit_price * FEE_RATE
        raw_pnl = (exit_price - entry) * qty   # -500

        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.SELL, quantity=qty,
            price=exit_price, agent_id=AGENT_ID,
        )
        balance_after_close = await _get_balance(db_session)

        close_fee_actual = qty * exit_price * FEE_RATE
        expected = balance_after_open + margin + raw_pnl - close_fee_actual
        assert abs(balance_after_close - expected) < 0.10, (
            f"Expected ~{expected:.2f}, got {balance_after_close:.2f}"
        )

    async def test_position_deleted_after_full_close(self, svc, db_session):
        await _seed_balance(db_session)
        qty = 3.0
        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.BUY, quantity=qty,
            price=1000.0, agent_id=AGENT_ID,
            leverage=2.0, margin_used=1500.0,
        )
        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.SELL, quantity=qty,
            price=1050.0, agent_id=AGENT_ID,
        )
        pos = await _get_position(db_session, OrderSide.BUY)
        assert pos is None, "Position should be deleted after full close"


# ---------------------------------------------------------------------------
# Leveraged short: open
# ---------------------------------------------------------------------------

class TestLeveragedShortOpen:
    async def test_margin_deducted_not_full_notional(self, svc, db_session):
        await _seed_balance(db_session, INITIAL_BALANCE)
        entry = 1000.0
        qty = 5.0
        leverage = 2.0
        notional = qty * entry
        margin = notional / leverage       # 2_500
        fee = notional * FEE_RATE

        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.SELL, quantity=qty,
            price=entry, agent_id=AGENT_ID,
            leverage=leverage, margin_used=margin,
        )

        balance_after = await _get_balance(db_session)
        expected = INITIAL_BALANCE - margin - fee
        assert abs(balance_after - expected) < 0.01

    async def test_short_position_stored_with_leverage(self, svc, db_session):
        await _seed_balance(db_session)
        qty = 4.0
        leverage = 2.0
        margin = qty * 1000.0 / leverage
        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.SELL, quantity=qty,
            price=1000.0, agent_id=AGENT_ID,
            leverage=leverage, margin_used=margin,
        )
        pos = await _get_position(db_session, OrderSide.SELL)
        assert pos is not None
        assert abs(pos.leverage - leverage) < 1e-6
        assert abs(pos.margin_used - margin) < 0.01


# ---------------------------------------------------------------------------
# Leveraged short: profitable cover
# ---------------------------------------------------------------------------

class TestLeveragedShortCover:
    async def test_profitable_cover_returns_margin_plus_profit(self, svc, db_session):
        await _seed_balance(db_session, INITIAL_BALANCE)
        entry = 1000.0
        cover_price = 900.0   # price dropped → short profit
        qty = 5.0
        leverage = 2.0
        margin = qty * entry / leverage        # 2_500

        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.SELL, quantity=qty,
            price=entry, agent_id=AGENT_ID,
            leverage=leverage, margin_used=margin,
        )
        balance_after_open = await _get_balance(db_session)

        close_fee = qty * cover_price * FEE_RATE
        raw_pnl = (entry - cover_price) * qty  # +500

        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.BUY, quantity=qty,
            price=cover_price, agent_id=AGENT_ID,
        )
        balance_after_close = await _get_balance(db_session)

        # After cover: balance_after_open + margin + realized - cover_fee (once)
        cover_fee = qty * cover_price * FEE_RATE
        expected = balance_after_open + margin + raw_pnl - cover_fee
        assert abs(balance_after_close - expected) < 0.10

    async def test_loss_cover_returns_margin_minus_loss(self, svc, db_session):
        await _seed_balance(db_session, INITIAL_BALANCE)
        entry = 1000.0
        cover_price = 1100.0  # price rose → short loss
        qty = 5.0
        leverage = 2.0
        margin = qty * entry / leverage

        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.SELL, quantity=qty,
            price=entry, agent_id=AGENT_ID,
            leverage=leverage, margin_used=margin,
        )
        balance_after_open = await _get_balance(db_session)

        close_fee = qty * cover_price * FEE_RATE
        raw_pnl = (entry - cover_price) * qty  # -500

        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.BUY, quantity=qty,
            price=cover_price, agent_id=AGENT_ID,
        )
        balance_after_close = await _get_balance(db_session)

        cover_fee_actual = qty * cover_price * FEE_RATE
        expected = balance_after_open + margin + raw_pnl - cover_fee_actual
        assert abs(balance_after_close - expected) < 0.10


# ---------------------------------------------------------------------------
# Partial close of a leveraged position
# ---------------------------------------------------------------------------

class TestLeveragedPartialClose:
    async def test_partial_close_releases_proportional_margin(self, svc, db_session):
        """Partial close should release exactly close_pct * margin_used."""
        await _seed_balance(db_session, INITIAL_BALANCE)
        entry = 1000.0
        qty = 10.0
        leverage = 3.0
        margin = qty * entry / leverage        # 3_333.33

        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.BUY, quantity=qty,
            price=entry, agent_id=AGENT_ID,
            leverage=leverage, margin_used=margin,
        )

        pos = await _get_position(db_session, OrderSide.BUY)
        pos_id = pos.id
        balance_before_partial = await _get_balance(db_session)

        close_pct = 0.5
        exit_price = 1050.0
        result = await svc.partial_close(pos_id, close_pct, exit_price)

        assert result is not None
        close_qty = qty * close_pct                              # 5
        expected_margin_release = margin * close_pct            # 1_666.67
        raw_pnl = close_qty * (exit_price - entry)              # 250
        close_fee = close_qty * exit_price * FEE_RATE           # ~5.25

        balance_after_partial = await _get_balance(db_session)
        expected_balance = balance_before_partial + expected_margin_release + raw_pnl - close_fee
        assert abs(balance_after_partial - expected_balance) < 0.20, (
            f"Expected ~{expected_balance:.2f}, got {balance_after_partial:.2f}"
        )

    async def test_partial_close_reduces_position_quantity(self, svc, db_session):
        await _seed_balance(db_session, INITIAL_BALANCE)
        qty = 8.0
        leverage = 2.0
        margin = qty * 1000.0 / leverage

        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.BUY, quantity=qty,
            price=1000.0, agent_id=AGENT_ID,
            leverage=leverage, margin_used=margin,
        )
        pos = await _get_position(db_session, OrderSide.BUY)
        pos_id = pos.id

        await svc.partial_close(pos_id, 0.25, 1020.0)

        pos = await db_session.get(PaperPosition, pos_id)
        expected_qty = qty * 0.75
        assert abs(pos.quantity - expected_qty) < 1e-6

    async def test_partial_close_reduces_margin_used(self, svc, db_session):
        await _seed_balance(db_session, INITIAL_BALANCE)
        qty = 6.0
        leverage = 2.0
        margin = qty * 1000.0 / leverage   # 3_000

        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.BUY, quantity=qty,
            price=1000.0, agent_id=AGENT_ID,
            leverage=leverage, margin_used=margin,
        )
        pos = await _get_position(db_session, OrderSide.BUY)
        pos_id = pos.id

        await svc.partial_close(pos_id, 0.5, 1000.0)

        pos = await db_session.get(PaperPosition, pos_id)
        expected_margin = margin * 0.5
        assert abs(pos.margin_used - expected_margin) < 0.01

    async def test_full_partial_close_deletes_position(self, svc, db_session):
        await _seed_balance(db_session, INITIAL_BALANCE)
        qty = 4.0
        leverage = 2.0
        margin = qty * 1000.0 / leverage

        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.BUY, quantity=qty,
            price=1000.0, agent_id=AGENT_ID,
            leverage=leverage, margin_used=margin,
        )
        pos = await _get_position(db_session, OrderSide.BUY)
        pos_id = pos.id

        await svc.partial_close(pos_id, 1.0, 1010.0)

        pos = await db_session.get(PaperPosition, pos_id)
        assert pos is None, "Position should be deleted when fully closed via partial_close"


# ---------------------------------------------------------------------------
# Unleveraged baseline: behaviour unchanged at 1x
# ---------------------------------------------------------------------------

class TestUnleveragedBaseline:
    async def test_1x_long_open_deducts_full_notional(self, svc, db_session):
        await _seed_balance(db_session, INITIAL_BALANCE)
        entry = 1000.0
        qty = 2.0
        fee = qty * entry * FEE_RATE

        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.BUY, quantity=qty,
            price=entry, agent_id=AGENT_ID,
            leverage=1.0,
        )
        balance_after = await _get_balance(db_session)
        # At 1x: full notional deducted
        expected = INITIAL_BALANCE - qty * entry - fee
        assert abs(balance_after - expected) < 0.01

    async def test_1x_position_leverage_field_is_one(self, svc, db_session):
        await _seed_balance(db_session)
        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.BUY, quantity=2.0,
            price=1000.0, agent_id=AGENT_ID, leverage=1.0,
        )
        pos = await _get_position(db_session, OrderSide.BUY)
        assert pos is not None
        assert abs((pos.leverage or 1.0) - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# Additive leveraged position sizing
# ---------------------------------------------------------------------------

class TestAdditiveLeveragedLong:
    async def test_add_to_existing_leveraged_long_averages_entry(self, svc, db_session):
        await _seed_balance(db_session, INITIAL_BALANCE)
        leverage = 2.0
        qty1, price1 = 2.0, 1000.0
        qty2, price2 = 2.0, 1100.0
        margin1 = qty1 * price1 / leverage
        margin2 = qty2 * price2 / leverage

        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.BUY, quantity=qty1,
            price=price1, agent_id=AGENT_ID,
            leverage=leverage, margin_used=margin1,
        )
        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.BUY, quantity=qty2,
            price=price2, agent_id=AGENT_ID,
            leverage=leverage, margin_used=margin2,
        )

        pos = await _get_position(db_session, OrderSide.BUY)
        assert pos is not None
        expected_qty = qty1 + qty2
        expected_entry = (price1 * qty1 + price2 * qty2) / expected_qty
        assert abs(pos.quantity - expected_qty) < 1e-6
        assert abs(pos.entry_price - expected_entry) < 0.01

    async def test_add_to_existing_leveraged_long_accumulates_margin(self, svc, db_session):
        await _seed_balance(db_session, INITIAL_BALANCE)
        leverage = 2.0
        qty = 2.0
        price = 1000.0
        margin = qty * price / leverage

        # Two identical orders
        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.BUY, quantity=qty,
            price=price, agent_id=AGENT_ID,
            leverage=leverage, margin_used=margin,
        )
        await svc.place_order(
            symbol=SYMBOL, side=OrderSide.BUY, quantity=qty,
            price=price, agent_id=AGENT_ID,
            leverage=leverage, margin_used=margin,
        )

        pos = await _get_position(db_session, OrderSide.BUY)
        assert pos is not None
        assert abs(pos.margin_used - margin * 2) < 0.01
