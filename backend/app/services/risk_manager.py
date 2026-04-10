from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import json
from app.config import settings
from app.services.llm import LLMService

logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    stop_loss_pct: float = 3.5
    take_profit_pct: float = 7.0
    max_daily_loss: float = 5.0       # percentage of total capital (e.g. 5.0 = 5%)
    total_capital: float = 0.0        # used to convert max_daily_loss % → dollar threshold
    max_position_size: float = 1000.0
    trailing_stop_pct: Optional[float] = None
    max_open_positions: int = 3
    max_exposure: Optional[float] = None  # absolute $ cap; if None, falls back to max_position_size * 10


@dataclass
class RiskCheckResult:
    allowed: bool
    action: str
    reason: str
    adjusted_quantity: Optional[float] = None
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None


@dataclass
class RiskAssessment:
    """Portfolio-level risk assessment from Risk Manager"""
    timestamp: datetime
    risk_level: str  # "safe", "caution", "danger"
    daily_pnl: float
    portfolio_exposure: float
    max_daily_loss_limit: float
    exposure_pct_of_capital: float
    largest_position_symbol: Optional[str] = None
    largest_position_size: float = 0.0
    concentration_risk: str = "low"  # low, medium, high
    recommendations: List[str] = None
    reasoning: str = ""

    def __post_init__(self):
        if self.recommendations is None:
            self.recommendations = []


class RiskManager:
    def __init__(self):
        self._daily_pnl: Dict[str, float] = {}
        self._last_reset: Optional[datetime] = None
        self.llm_service = LLMService()
    
    def check_trade(
        self,
        side: str,
        quantity: float,
        entry_price: float,
        risk_config: RiskConfig,
        current_positions: List[Dict[str, Any]] = []
    ) -> RiskCheckResult:
        self._check_daily_reset()
        
        total_exposure = sum(
            pos.get('quantity', 0) * pos.get('entry_price', 0)
            for pos in current_positions
        )
        
        trade_value = quantity * entry_price
        potential_exposure = total_exposure + trade_value
        
        if potential_exposure > (risk_config.max_exposure or risk_config.max_position_size * 10):
            exposure_limit = risk_config.max_exposure or risk_config.max_position_size * 10
            return RiskCheckResult(
                allowed=False,
                action="reject",
                reason=f"Max exposure exceeded: ${exposure_limit:.0f}"
            )
        
        if len(current_positions) >= risk_config.max_open_positions:
            return RiskCheckResult(
                allowed=False,
                action="reject",
                reason=f"Max open positions reached: {risk_config.max_open_positions}"
            )
        
        daily_loss = self._daily_pnl.get('today', 0)
        # Convert percentage limit to a dollar threshold using total_capital.
        # daily_loss is in dollars; max_daily_loss is a percentage (e.g. 5.0 = 5%).
        if risk_config.total_capital > 0:
            daily_loss_limit_dollars = risk_config.total_capital * risk_config.max_daily_loss / 100
        else:
            # Fallback: treat as dollar value if total_capital not provided (legacy behaviour)
            daily_loss_limit_dollars = risk_config.max_daily_loss
        if daily_loss <= -daily_loss_limit_dollars:
            return RiskCheckResult(
                allowed=False,
                action="stop",
                reason=f"Daily loss limit reached: {risk_config.max_daily_loss}% (${daily_loss_limit_dollars:,.0f})"
            )
        
        if side.lower() == 'buy':
            stop_loss = entry_price * (1 - risk_config.stop_loss_pct / 100)
            take_profit = entry_price * (1 + risk_config.take_profit_pct / 100)
        else:
            stop_loss = entry_price * (1 + risk_config.stop_loss_pct / 100)
            take_profit = entry_price * (1 - risk_config.take_profit_pct / 100)
        
        return RiskCheckResult(
            allowed=True,
            action="proceed",
            reason="Trade within risk parameters",
            stop_loss_price=stop_loss,
            take_profit_price=take_profit
        )
    
    def check_exit(
        self,
        position: Dict[str, Any],
        current_price: float,
        risk_config: RiskConfig
    ) -> RiskCheckResult:
        side = position.get('side', 'buy')
        entry_price = position.get('entry_price', 0)
        
        if side.lower() == 'buy':
            pnl_pct = ((current_price - entry_price) / entry_price) * 100
        else:
            pnl_pct = ((entry_price - current_price) / entry_price) * 100
        
        stop_triggered = False
        profit_triggered = False
        
        if side.lower() == 'buy':
            stop_triggered = current_price <= position.get('stop_loss', entry_price * 0.98)
            profit_triggered = current_price >= position.get('take_profit', entry_price * 1.02)
        else:
            stop_triggered = current_price >= position.get('stop_loss', entry_price * 1.02)
            profit_triggered = current_price <= position.get('take_profit', entry_price * 0.98)
        
        if stop_triggered:
            return RiskCheckResult(
                allowed=True,
                action="exit",
                reason=f"Stop-loss triggered at ${current_price:.2f} (pnl: {pnl_pct:.2f}%)"
            )
        
        if profit_triggered:
            return RiskCheckResult(
                allowed=True,
                action="exit",
                reason=f"Take-profit triggered at ${current_price:.2f} (pnl: {pnl_pct:.2f}%)"
            )
        
        trailing_stop = risk_config.trailing_stop_pct
        if trailing_stop and position.get('highest_price'):
            highest = position['highest_price']
            if side.lower() == 'buy':
                # LONG: exit when price drops trailing_stop% below the high-water mark
                trail_price = highest * (1 - trailing_stop / 100)
                trailing_trigger = current_price <= trail_price
            else:
                # SHORT: highest_price tracks the LOWEST price (best for shorts)
                # Exit when price rises trailing_stop% above the low-water mark
                trail_price = highest * (1 + trailing_stop / 100)
                trailing_trigger = current_price >= trail_price
            
            if trailing_trigger:
                return RiskCheckResult(
                    allowed=True,
                    action="exit",
                    reason=(
                        f"Trailing stop triggered at ${current_price:.2f} "
                        f"(best: ${highest:.2f}, trail: {trailing_stop}%)"
                    )
                )
        
        return RiskCheckResult(
            allowed=False,
            action="hold",
            reason="No exit conditions met"
        )
    
    def record_pnl(self, pnl: float):
        self._check_daily_reset()
        current = self._daily_pnl.get('today', 0)
        self._daily_pnl['today'] = current + pnl
    
    def _check_daily_reset(self):
        now = datetime.now()
        if self._last_reset is None or now.date() > self._last_reset.date():
            self._daily_pnl = {'today': 0}
            self._last_reset = now
    
    def get_daily_pnl(self) -> float:
        self._check_daily_reset()
        return self._daily_pnl.get('today', 0)
    
    def reset_daily(self):
        self._daily_pnl = {'today': 0}
        self._last_reset = datetime.now()

    async def generate_risk_assessment(
        self,
        current_positions: List[Dict[str, Any]] = None,
        daily_pnl: Optional[float] = None,
        total_capital: float = 10000,
        max_daily_loss_pct: float = 5.0
    ) -> RiskAssessment:
        """
        Generate portfolio-level risk assessment.
        max_daily_loss_pct is the maximum daily loss as a PERCENTAGE of total capital.
        """
        current_positions = current_positions or []
        daily_pnl = daily_pnl if daily_pnl is not None else self.get_daily_pnl()

        try:
            timestamp = datetime.utcnow()

            # Calculate portfolio metrics
            total_exposure = sum(
                pos.get('quantity', 0) * pos.get('current_price', pos.get('entry_price', 0))
                for pos in current_positions
            )

            exposure_pct = (total_exposure / total_capital * 100) if total_capital else 0

            # Convert daily P&L to percentage of capital for threshold comparison
            daily_pnl_pct = (daily_pnl / total_capital * 100) if total_capital else 0

            # Find largest position
            largest_pos = None
            largest_symbol = None
            if current_positions:
                largest_pos = max(
                    current_positions,
                    key=lambda p: p.get('quantity', 0) * p.get('current_price', p.get('entry_price', 0))
                )
                largest_symbol = largest_pos.get('symbol')

            # Determine concentration risk
            if largest_pos:
                largest_exposure = (
                    largest_pos.get('quantity', 0) * largest_pos.get('current_price', largest_pos.get('entry_price', 0))
                ) / (total_exposure or 1) * 100
                if largest_exposure > 40:
                    concentration = "high"
                elif largest_exposure > 25:
                    concentration = "medium"
                else:
                    concentration = "low"
            else:
                concentration = "low"

            # Determine risk level
            # Exposure threshold is configurable via Settings → Risk Limits
            try:
                from app.api.routes.settings import get_risk_limits
                exposure_threshold = get_risk_limits().exposure_threshold_pct
            except Exception:
                exposure_threshold = 80.0

            # Compare daily P&L percentage against max daily loss percentage
            if daily_pnl_pct <= -max_daily_loss_pct:
                risk_level = "danger"
            elif daily_pnl_pct <= -max_daily_loss_pct / 2 or exposure_pct > exposure_threshold:
                risk_level = "caution"
            else:
                risk_level = "safe"

            # Generate recommendations
            recommendations = []
            if risk_level != "safe":
                recommendations.append(f"Current risk level: {risk_level}")
            if exposure_pct > exposure_threshold:
                recommendations.append(f"Portfolio exposure high ({exposure_pct:.1f}% of capital, threshold: {exposure_threshold:.0f}%)")
            if daily_pnl < 0:
                recommendations.append(f"Daily P&L negative: ${daily_pnl:.2f} ({daily_pnl_pct:+.2f}% of capital)")
            if concentration == "high":
                recommendations.append(f"Concentration risk high: {largest_symbol} is {largest_exposure:.1f}% of portfolio")

            # Use LLM for detailed risk reasoning
            reasoning = await self._generate_risk_reasoning(
                risk_level, daily_pnl, exposure_pct, concentration, max_daily_loss_pct
            )

            return RiskAssessment(
                timestamp=timestamp,
                risk_level=risk_level,
                daily_pnl=daily_pnl,
                portfolio_exposure=total_exposure,
                max_daily_loss_limit=max_daily_loss_pct,
                exposure_pct_of_capital=exposure_pct,
                largest_position_symbol=largest_symbol,
                largest_position_size=largest_pos.get('quantity', 0) if largest_pos else 0.0,
                concentration_risk=concentration,
                recommendations=recommendations,
                reasoning=reasoning
            )

        except Exception as e:
            logger.error(f"Risk assessment failed: {e}")
            return RiskAssessment(
                timestamp=datetime.utcnow(),
                risk_level="unknown",
                daily_pnl=daily_pnl or 0,
                portfolio_exposure=0,
                max_daily_loss_limit=max_daily_loss,
                exposure_pct_of_capital=0,
                recommendations=["Risk assessment failed - manual review required"],
                reasoning=f"Error: {str(e)}"
            )

    async def recommend_position_adjustments(
        self,
        current_positions: List[Dict[str, Any]],
        risk_assessment: RiskAssessment
    ) -> List[Dict[str, Any]]:
        """
        Recommend which positions to reduce if risk level is high
        """
        adjustments = []

        if risk_assessment.risk_level not in ["caution", "danger"]:
            return adjustments  # No adjustments needed

        try:
            # Sort positions by size (largest first)
            sorted_positions = sorted(
                current_positions,
                key=lambda p: p.get('quantity', 0) * p.get('current_price', p.get('entry_price', 0)),
                reverse=True
            )

            # Recommend reducing top 2 largest positions
            reduction_amount = 0.0
            if risk_assessment.risk_level == "danger":
                reduction_amount = 0.5  # Reduce by 50%
            elif risk_assessment.risk_level == "caution":
                reduction_amount = 0.25  # Reduce by 25%

            for position in sorted_positions[:2]:
                adjustment = {
                    'symbol': position.get('symbol'),
                    'side': position.get('side'),
                    'action': 'reduce',
                    'reduction_pct': reduction_amount * 100,
                    'reason': f"Risk level {risk_assessment.risk_level} - reduce exposure"
                }
                adjustments.append(adjustment)

        except Exception as e:
            logger.error(f"Position adjustment recommendation failed: {e}")

        return adjustments

    async def _generate_risk_reasoning(
        self,
        risk_level: str,
        daily_pnl: float,
        exposure_pct: float,
        concentration: str,
        max_daily_loss: float
    ) -> str:
        """Generate LLM-based risk reasoning"""
        try:
            prompt = f"""Assess the risk profile of this trading portfolio:

RISK METRICS:
- Risk Level: {risk_level}
- Daily P&L: ${daily_pnl:+.2f}
- Max Daily Loss Limit: {max_daily_loss}%
- Portfolio Exposure: {exposure_pct:.1f}% of capital
- Concentration Risk: {concentration}

Provide brief risk assessment in JSON:
{{
  "risk_interpretation": "brief assessment of what these metrics mean",
  "primary_concern": "main risk factor",
  "recommended_action": "specific action if needed"
}}
"""

            response = await self.llm_service._call_llm(prompt)

            try:
                data = json.loads(response.content)
                return data.get('risk_interpretation', 'Unable to assess')
            except (json.JSONDecodeError, ValueError):
                return f"Risk level: {risk_level}. Daily P&L: ${daily_pnl:+.2f}. Exposure: {exposure_pct:.1f}%"

        except Exception as e:
            logger.warning(f"Risk reasoning generation failed: {e}")
            return f"Risk level: {risk_level}. Unable to generate detailed reasoning."


risk_manager = RiskManager()
