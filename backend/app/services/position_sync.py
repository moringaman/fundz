import asyncio
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.database import get_async_session
from app.clients.phemex import PhemexClient
from app.models import Position, OrderSide
from app.config import settings

class PositionSyncService:
    def __init__(self, phemex_client: PhemexClient):
        self.phemex_client = phemex_client
        self.logger = logging.getLogger(__name__)
    
    async def sync_positions(self):
        """
        Periodically sync positions from Phemex to the database
        """
        while True:
            try:
                # Get async database session
                async with get_async_session() as db:
                    # Fetch current positions from Phemex
                    raw_positions = await self.phemex_client.get_positions()
                    
                    # Track symbols to remove stale positions
                    current_symbols = set()
                    
                    for pos in raw_positions.get('data', []):
                        symbol = pos.get('symbol', '')
                        current_symbols.add(symbol)
                        
                        # Determine side - Phemex uses 'Buy'/'Sell' or 'Long'/'Short'
                        side_raw = pos.get('side', '').lower()
                        if side_raw in ['long', 'buy']:
                            side = OrderSide.BUY
                        elif side_raw in ['short', 'sell']:
                            side = OrderSide.SELL
                        else:
                            # Skip if side can't be determined
                            self.logger.warning(f"Unknown side for position: {side_raw}")
                            continue
                        
                        # Convert values - Phemex uses satoshi-like precision
                        quantity = abs(float(pos.get('size', 0)) / 100000000)
                        entry_price = float(pos.get('avgEntryPrice', 0)) / 100000000
                        current_price = float(pos.get('markPrice', 0)) / 100000000
                        unrealized_pnl = float(pos.get('unrealisedPnl', 0)) / 100000000
                        
                        # Find existing position
                        query = await db.execute(
                            select(Position)
                            .where(Position.symbol == symbol)
                            .where(Position.user_id == "default-user")
                        )
                        db_position = query.scalar_one_or_none()
                        
                        if db_position:
                            # Update existing position
                            db_position.quantity = quantity
                            db_position.entry_price = entry_price
                            db_position.current_price = current_price
                            db_position.unrealized_pnl = unrealized_pnl
                            db_position.side = side
                        else:
                            # Create new position
                            db_position = Position(
                                user_id="default-user",
                                symbol=symbol,
                                side=side,
                                quantity=quantity,
                                entry_price=entry_price,
                                current_price=current_price,
                                unrealized_pnl=unrealized_pnl
                            )
                            db.add(db_position)
                    
                    # Remove stale positions (no longer open)
                    await db.execute(
                        update(Position)
                        .where(Position.user_id == "default-user")
                        .where(Position.symbol.notin_(current_symbols))
                        .values(quantity=0, unrealized_pnl=0, current_price=None)
                    )
                    
                    await db.commit()
                    self.logger.info(f"Synced {len(raw_positions.get('data', []))} positions")
            
            except Exception as e:
                self.logger.error(f"Error syncing positions: {e}")
            
            # Wait for 30 seconds before next sync
            await asyncio.sleep(30)

async def start_position_sync(app):
    """
    Startup handler to initialize position sync
    """
    phemex_client = PhemexClient(
        api_key=settings.phemex_api_key,
        api_secret=settings.phemex_api_secret,
        testnet=settings.phemex_testnet
    )
    position_sync_service = PositionSyncService(phemex_client)
    asyncio.create_task(position_sync_service.sync_positions())
    return app