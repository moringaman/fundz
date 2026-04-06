import asyncio
import sys
import os

# Add the project root to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from app.database import get_db
from app.models import Agent, Balance as PaperBalance, Position as PaperPosition, Trade as PaperOrder
from app.services.agent_scheduler import agent_scheduler
from app.services.paper_trading import paper_trading

async def diagnose_paper_trading():
    print("🕵️ Paper Trading Diagnostics 🕵️")
    
    # Check Agents
    print("\n🤖 AGENTS:")
    async for db in get_db():
        query = select(Agent).where(Agent.is_enabled == True)
        result = await db.execute(query)
        agents = result.scalars().all()
        
        for agent in agents:
            print(f"  - {agent.name}")
            print(f"    Strategy: {agent.strategy_type}")
            print(f"    Trading Pairs: {agent.config.get('trading_pairs', [])}")
            print(f"    Allocation: {agent.allocation_percentage}%")
    
    # Check Balances
    print("\n💰 PAPER BALANCES:")
    async for db in get_db():
        query = select(PaperBalance).where(PaperBalance.user_id == "default-user")
        result = await db.execute(query)
        balances = result.scalars().all()
        
        for balance in balances:
            print(f"  - {balance.asset}")
            print(f"    Available: ${balance.available:.2f}")
            print(f"    Locked: ${balance.locked:.2f}")
    
    # Check Positions
    print("\n📊 OPEN POSITIONS:")
    async for db in get_db():
        query = select(PaperPosition).where(PaperPosition.user_id == "default-user")
        result = await db.execute(query)
        positions = result.scalars().all()
        
        if not positions:
            print("  No open positions")
        
        for pos in positions:
            print(f"  - {pos.symbol}")
            print(f"    Side: {pos.side.value}")
            print(f"    Quantity: {pos.quantity}")
            print(f"    Entry Price: ${pos.entry_price:.2f}")
            print(f"    Current Price: ${pos.current_price:.2f}")
            print(f"    Unrealized P&L: ${pos.unrealized_pnl:.2f}")
    
    # Check Recent Orders
    print("\n📦 RECENT ORDERS:")
    async for db in get_db():
        query = select(PaperOrder).where(PaperOrder.user_id == "default-user").order_by(PaperOrder.created_at.desc()).limit(5)
        result = await db.execute(query)
        orders = result.scalars().all()
        
        if not orders:
            print("  No recent orders")
        
        for order in orders:
            print(f"  - {order.symbol}")
            print(f"    Side: {order.side.value}")
            print(f"    Quantity: {order.quantity}")
            print(f"    Price: ${order.price:.2f}")
            print(f"    Status: {order.status.value}")
            print(f"    Created At: {order.created_at}")
    
    # Verify Paper Trading Service
    print("\n🔧 PAPER TRADING SERVICE:")
    try:
        pnl = await paper_trading.calculate_pnl()
        print("  Trades Enabled: YES")
        print(f"  Total P&L: ${pnl['total_pnl']:.2f}")
        print(f"  Realized P&L: ${pnl['realized_pnl']:.2f}")
        print(f"  Unrealized P&L: ${pnl['unrealized_pnl']:.2f}")
        print(f"  Trade Count: {pnl['trade_count']}")
        print(f"  Open Positions: {pnl['open_positions']}")
    except Exception as e:
        print(f"  Error accessing paper trading service: {e}")
    
    # Verify Agent Scheduler
    print("\n🕰️ AGENT SCHEDULER:")
    print(f"  Running: {agent_scheduler.is_running}")
    metrics = agent_scheduler.get_all_metrics()
    print(f"  Total Agents: {len(metrics)}")
    
    # Recent Agent Runs
    if metrics:
        print("\n🏃 RECENT AGENT RUNS:")
        runs = agent_scheduler.get_recent_runs(limit=5)
        for run in runs:
            print(f"  - Agent: {run.agent_id}")
            print(f"    Symbol: {run.symbol}")
            print(f"    Signal: {run.signal}")
            print(f"    Confidence: {run.confidence:.2f}")
            print(f"    Executed: {'✅ YES' if run.executed else '❌ NO'}")

if __name__ == "__main__":
    asyncio.run(diagnose_paper_trading())