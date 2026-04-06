import logging
from typing import Dict, Any

from app.services.agent_scheduler import agent_scheduler
from app.services.paper_trading import paper_trading
from app.config import settings

logger = logging.getLogger(__name__)

async def diagnose_paper_trading() -> Dict[str, Any]:
    """
    Comprehensive diagnostic of paper trading and agent system
    """
    diagnostics = {
        "paper_trading": {
            "is_enabled": paper_trading.is_enabled,
            "current_balances": [],
            "error": None
        },
        "agent_scheduler": {
            "is_running": agent_scheduler.is_running,
            "enabled_agents": len(agent_scheduler._enabled_agents),
            "recent_runs": [],
            "error": None
        },
        "configuration": {
            "phemex_api_key": bool(settings.phemex_api_key),
            "phemex_testnet": settings.phemex_testnet
        }
    }

    try:
        # Get paper trading balances
        async with get_async_session() as db:
            query = select(PaperBalance).where(PaperBalance.user_id == "default-user")
            result = await db.execute(query)
            balances = result.scalars().all()
            
            diagnostics["paper_trading"]["current_balances"] = [
                {"asset": b.asset, "available": b.available, "locked": b.locked}
                for b in balances
            ]
    except Exception as e:
        diagnostics["paper_trading"]["error"] = str(e)

    # Get agent scheduler details
    try:
        recent_runs = agent_scheduler.get_recent_runs(limit=10)
        diagnostics["agent_scheduler"]["recent_runs"] = [
            {
                "agent_id": run.agent_id,
                "timestamp": run.timestamp.isoformat(),
                "symbol": run.symbol,
                "signal": run.signal,
                "confidence": run.confidence,
                "executed": run.executed,
                "error": run.error
            } for run in recent_runs
        ]
    except Exception as e:
        diagnostics["agent_scheduler"]["error"] = str(e)

    return diagnostics

def print_diagnostics(diagnostics: Dict[str, Any]):
    """
    Print a human-readable diagnostic report
    """
    print("\n===== Paper Trading Diagnostic Report =====")
    
    print("\n1. Paper Trading Status:")
    print(f"   Enabled: {diagnostics['paper_trading']['is_enabled']}")
    print("   Current Balances:")
    for balance in diagnostics['paper_trading']['current_balances']:
        print(f"   - {balance['asset']}: Available ${balance['available']:.2f}, Locked ${balance['locked']:.2f}")
    if diagnostics['paper_trading']['error']:
        print(f"   ERROR: {diagnostics['paper_trading']['error']}")

    print("\n2. Agent Scheduler Status:")
    print(f"   Running: {diagnostics['agent_scheduler']['is_running']}")
    print(f"   Enabled Agents: {diagnostics['agent_scheduler']['enabled_agents']}")
    print("   Recent Agent Runs:")
    for run in diagnostics['agent_scheduler']['recent_runs']:
        status = "✅ Executed" if run['executed'] else "❌ Not Executed"
        print(f"   - Agent {run['agent_id']}: {run['symbol']} {run['signal']} (Confidence: {run['confidence']:.2f}) {status}")
    if diagnostics['agent_scheduler']['error']:
        print(f"   ERROR: {diagnostics['agent_scheduler']['error']}")

    print("\n3. Configuration:")
    print(f"   Phemex API Key Configured: {diagnostics['configuration']['phemex_api_key']}")
    print(f"   Testnet Mode: {diagnostics['configuration']['phemex_testnet']}")

    print("\n==========================================\n")