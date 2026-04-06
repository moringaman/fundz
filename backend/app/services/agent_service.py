from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime

from app.services.indicators import IndicatorService, TradingSignal, Signal
from app.clients.phemex import PhemexClient
from app.config import settings


@dataclass
class AgentConfig:
    name: str
    strategy_type: str
    trading_pairs: List[str]
    allocation_percentage: float
    max_position_size: float
    risk_limit: float
    run_interval_seconds: int
    indicators_config: Dict[str, Any]


class AgentService:
    def __init__(self, phemex_client: PhemexClient):
        self.phemex_client = phemex_client
        self.indicator_service = IndicatorService()

    async def run_agent(self, agent_config: AgentConfig) -> List[TradingSignal]:
        signals = []
        
        for symbol in agent_config.trading_pairs:
            try:
                klines = await self.phemex_client.get_klines(
                    symbol=symbol,
                    interval="1h",
                    limit=200
                )
                
                if not klines:
                    continue

                df_data = []
                for k in klines:
                    df_data.append({
                        "timestamp": k[0],
                        "open": float(k[2]),
                        "high": float(k[3]),
                        "low": float(k[4]),
                        "close": float(k[5]),
                        "volume": float(k[6])
                    })

                import pandas as pd
                df = pd.DataFrame(df_data)
                df.set_index("timestamp", inplace=True)

                signal = self.indicator_service.generate_signal(df, agent_config.indicators_config)
                signal.symbol = symbol
                signals.append(signal)
                
            except Exception as e:
                print(f"Error running agent for {symbol}: {e}")
                continue

        return signals

    async def execute_signal(self, signal: TradingSignal, quantity: float) -> Dict[str, Any]:
        side = "Buy" if signal.signal == Signal.BUY else "Sell" if signal.signal == Signal.SELL else None
        
        if not side:
            return {"status": "skipped", "reason": "No signal"}

        result = await self.phemex_client.place_order(
            symbol=signal.symbol,
            side=side,
            quantity=quantity
        )
        
        return result
