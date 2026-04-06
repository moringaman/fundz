import asyncio
import sys
import os

# Add the project root to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from app.database import get_db
from app.models import Agent
from app.services.agent_scheduler import agent_scheduler
from app.config import settings

async def create_default_agents():
    async for db in get_db():
        # Check if agents already exist
        existing_agents = await db.execute(select(Agent))
        existing_agents = existing_agents.scalars().all()
        
        if not existing_agents:
            # Create default agents
            agents_config = [
                {
                    "name": "Momentum BTC Strategy",
                    "strategy_type": "momentum",
                    "trading_pairs": ["BTCUSDT"],
                    "allocation_percentage": 10,
                    "max_position_size": 0.1,
                    "run_interval_seconds": 3600,  # 1 hour
                    "stop_loss_pct": 2.0,
                    "take_profit_pct": 4.0,
                    "is_enabled": True
                },
                {
                    "name": "Mean Reversion ETH Strategy",
                    "strategy_type": "mean_reversion",
                    "trading_pairs": ["ETHUSDT"],
                    "allocation_percentage": 5,
                    "max_position_size": 0.05,
                    "run_interval_seconds": 1800,  # 30 minutes
                    "stop_loss_pct": 3.0,
                    "take_profit_pct": 5.0,
                    "is_enabled": True
                },
                {
                    "name": "AI-Powered Trading Agent",
                    "strategy_type": "ai",
                    "trading_pairs": ["BTCUSDT", "ETHUSDT"],
                    "allocation_percentage": 15,
                    "max_position_size": 0.2,
                    "run_interval_seconds": 7200,  # 2 hours
                    "stop_loss_pct": 2.5,
                    "take_profit_pct": 5.0,
                    "is_enabled": True
                }
            ]

            for config in agents_config:
                agent = Agent(
                    name=config['name'],
                    strategy_type=config['strategy_type'],
                    config=config,
                    is_enabled=config['is_enabled'],
                    allocation_percentage=config['allocation_percentage'],
                    max_position_size=config['max_position_size'],
                    risk_limit=5.0,  # Daily risk limit
                    run_interval_seconds=config['run_interval_seconds']
                )
                db.add(agent)
            
            await db.commit()
            print(f"Created {len(agents_config)} default agents")
        else:
            print(f"Found {len(existing_agents)} existing agents")

async def start_scheduler():
    # Register existing enabled agents
    async for db in get_db():
        query = select(Agent).where(Agent.is_enabled == True)
        result = await db.execute(query)
        agents = result.scalars().all()

        for agent in agents:
            agent_config = {
                **agent.config,
                'id': agent.id,
                'name': agent.name,
                'strategy_type': agent.strategy_type,
                'run_interval_seconds': agent.run_interval_seconds,
                'allocation_percentage': agent.allocation_percentage,
                'max_position_size': agent.max_position_size
            }
            agent_scheduler.register_agent(agent_config)

    # Start the scheduler
    await agent_scheduler.start()
    print("Agent scheduler started")

async def main():
    await create_default_agents()
    await start_scheduler()

if __name__ == "__main__":
    asyncio.run(main())