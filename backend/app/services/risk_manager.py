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
    leverage: float = 1.0
    max_leveraged_notional_pct: float = 200.0
    liquidation_buffer_pct: float = 12.5


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
    whale_short_pct: Optional[float] = None  # 0.0–1.0 fraction of whale notional that is SHORT

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
        current_positions: Optional[List[Dict[str, Any]]] = None
    ) -> RiskCheckResult:
        self._check_daily_reset()
        current_positions = current_positions or []
        
        total_exposure = sum(
            pos.get('quantity', 0) * pos.get('entry_price', 0)
            for pos in current_positions
        )

        total_leveraged_notional = sum(
            pos.get('notional', pos.get('quantity', 0) * pos.get('entry_price', 0))
            for pos in current_positions
        )
        
        trade_value = quantity * entry_price
        potential_exposure = total_exposure + trade_value
        potential_leveraged_notional = total_leveraged_notional + trade_value
        
        if potential_exposure > (risk_config.max_exposure or risk_config.max_position_size * 10):
            exposure_limit = risk_config.max_exposure or risk_config.max_position_size * 10
            return RiskCheckResult(
                allowed=False,
                action="reject",
                reason=f"Max exposure exceeded: ${exposure_limit:.0f}"
            )

        leveraged_notional_limit = 0.0
        if risk_config.total_capital > 0:
            leveraged_notional_limit = risk_config.total_capital * (risk_config.max_leveraged_notional_pct / 100)
        if leveraged_notional_limit > 0 and potential_leveraged_notional > leveraged_notional_limit:
            return RiskCheckResult(
                allowed=False,
                action="reject",
                reason=(
                    f"Leveraged notional cap exceeded: ${potential_leveraged_notional:,.0f} "
                    f"> ${leveraged_notional_limit:,.0f}"
                )
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

    def calculate_liquidation_price(
        self,
        entry_price: float,
        side: str,
        leverage: float,
        maintenance_margin_pct: float = 0.5,
    ) -> Optional[float]:
        """Approximate liquidation price for a leveraged position on cross margin.

        This intentionally uses a conservative approximation. We are not trying to
        mirror exchange liquidation math perfectly; we are establishing a danger zone
        early enough to preserve capital.
        """
        if entry_price <= 0 or leverage <= 1.0:
            return None

        leverage = max(leverage, 1.0)
        mmr = max(maintenance_margin_pct / 100, 0.0)
        side_l = str(side).lower()

        if side_l in ('buy', 'long'):
            return entry_price * (1.0 - (1.0 / leverage) + mmr)
        return entry_price * (1.0 + (1.0 / leverage) - mmr)

    def check_liquidation_risk(
        self,
        side: str,
        current_price: float,
        liquidation_price: Optional[float],
        liquidation_buffer_pct: float,
    ) -> Optional[Dict[str, Any]]:
        if not liquidation_price or liquidation_price <= 0 or current_price <= 0:
            return None

        distance_pct = abs((current_price - liquidation_price) / liquidation_price) * 100
        side_l = str(side).lower()
        breached = (
            current_price <= liquidation_price
            if side_l in ('buy', 'long')
            else current_price >= liquidation_price
        )

        if breached or distance_pct <= liquidation_buffer_pct:
            return {
                'risk_level': 'CRITICAL',
                'action': 'FORCE_CLOSE',
                'distance_pct': round(distance_pct, 2),
                'liquidation_price': liquidation_price,
            }
        if distance_pct <= liquidation_buffer_pct * 1.5:
            return {
                'risk_level': 'HIGH_ALERT',
                'action': 'WARN',
                'distance_pct': round(distance_pct, 2),
                'liquidation_price': liquidation_price,
            }
        return {
            'risk_level': 'SAFE',
            'action': 'HOLD',
            'distance_pct': round(distance_pct, 2),
            'liquidation_price': liquidation_price,
        }
    
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
            stop_triggered = current_price <= position.get('stop_loss', entry_price * 0.965)   # 3.5% SL
            profit_triggered = current_price >= position.get('take_profit', entry_price * 1.07)  # 7% TP
        else:
            stop_triggered = current_price >= position.get('stop_loss', entry_price * 1.035)   # 3.5% SL
            profit_triggered = current_price <= position.get('take_profit', entry_price * 0.93)  # 7% TP
        
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
            # Only meaningful when 2+ positions exist — a single position is always "100%"
            # but that's not a concentration problem, just a small active portfolio.
            if largest_pos and len(current_positions) >= 2:
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

            # --- Whale intelligence signals ---
            whale_signals: List[str] = []
            _whale_short_pct: Optional[float] = None  # tracked for direction-aware gate
            try:
                from app.services.whale_intelligence import whale_intelligence as _whale_svc
                whale_report = await _whale_svc.fetch_whale_report()
                if whale_report and whale_report.coin_biases:
                    # 1. Check each open position for opposing whale bias or exit pressure
                    for pos in current_positions:
                        symbol = pos.get("symbol", "")
                        pos_side = pos.get("side", "").lower()  # "long" or "short"
                        coin = _whale_svc.symbol_to_coin(symbol)
                        bias = whale_report.coin_biases.get(coin)
                        if bias is None:
                            continue

                        # Opposing bias: whale net is the opposite of our position
                        whale_dir = "long" if bias.net_notional > 0 else "short"
                        if pos_side and whale_dir != pos_side and abs(bias.net_notional) > 10_000:
                            whale_signals.append(
                                f"⚠️ Whale opposition on {coin}: we are {pos_side.upper()}, "
                                f"whales are NET {whale_dir.upper()} "
                                f"(${abs(bias.net_notional)/1000:.0f}K net, {bias.whale_count} wallet(s))"
                            )
                            if risk_level == "safe":
                                risk_level = "caution"

                        # Exit pressure: top whale sitting on large unrealised gain
                        if bias.top_positions:
                            top = bias.top_positions[0]
                            if top.notional_usd > 0:
                                pnl_ratio = top.unrealized_pnl / top.notional_usd
                                if pnl_ratio >= 0.25 and top.side.lower() == pos_side:
                                    # Whale is long & in huge profit on same coin we're long = dump risk
                                    whale_signals.append(
                                        f"⚠️ Exit pressure on {coin}: lead whale is {top.side.upper()} "
                                        f"+{pnl_ratio:.0%} unrealised — profit-taking may reverse price"
                                    )
                                    if risk_level == "safe":
                                        risk_level = "caution"
                                elif pnl_ratio <= -0.20 and top.side.lower() != pos_side:
                                    # Whale is trapped on opposite side — acts as a wall against our direction
                                    whale_signals.append(
                                        f"📌 Trapped whale on {coin}: lead whale {top.side.upper()} "
                                        f"is {pnl_ratio:.0%} underwater near {top.entry_price:.4g} "
                                        f"— price resistance/support wall"
                                    )

                    # 2. Aggregate market-level whale sentiment
                    bullish_notional = sum(
                        b.long_notional for b in whale_report.coin_biases.values()
                        if b.long_notional + b.short_notional > 10_000
                    )
                    bearish_notional = sum(
                        b.short_notional for b in whale_report.coin_biases.values()
                        if b.long_notional + b.short_notional > 10_000
                    )
                    total_whale_notional = bullish_notional + bearish_notional
                    if total_whale_notional > 0:
                        bear_pct = bearish_notional / total_whale_notional
                        _whale_short_pct = bear_pct  # expose for direction-aware entry gate
                        try:
                            from app.api.routes.settings import get_trading_gates as _get_whale_gates
                            _wg = _get_whale_gates()
                            _w_caution = _wg.whale_caution_threshold
                            _w_info = _wg.whale_info_threshold
                            _w_bull = _wg.whale_bull_threshold
                        except Exception:
                            _w_caution = 0.75
                            _w_info = 0.65
                            _w_bull = 0.30
                        if bear_pct >= _w_caution:
                            # Strong bearish whale positioning — genuine caution signal
                            whale_signals.append(
                                f"🐋 Macro whale sentiment: {bear_pct:.0%} SHORT across all tracked coins "
                                f"— broad bearish smart-money positioning"
                            )
                            if risk_level == "safe":
                                risk_level = "caution"
                        elif bear_pct >= _w_info:
                            # Moderately bearish — note it but don't elevate risk level
                            whale_signals.append(
                                f"🐋 Macro whale sentiment: {bear_pct:.0%} SHORT across tracked coins "
                                f"— bearish lean, monitor positions"
                            )
                        elif bear_pct <= _w_bull:
                            whale_signals.append(
                                f"🐋 Macro whale sentiment: {(1-bear_pct):.0%} LONG across tracked coins "
                                f"— broad bullish smart-money positioning"
                            )
            except Exception as _whale_err:
                logger.debug(f"Whale signals skipped: {_whale_err}")

            # Generate recommendations
            recommendations = []
            if exposure_pct > exposure_threshold:
                recommendations.append(f"Portfolio exposure high ({exposure_pct:.1f}% of capital, threshold: {exposure_threshold:.0f}%)")
            if daily_pnl < 0:
                recommendations.append(f"Daily P&L negative: ${daily_pnl:.2f} ({daily_pnl_pct:+.2f}% of capital)")
            if concentration == "high":
                recommendations.append(f"Concentration risk high: {largest_symbol} is {largest_exposure:.1f}% of portfolio")
            recommendations.extend(whale_signals)

            # Use LLM for detailed risk reasoning
            reasoning = await self._generate_risk_reasoning(
                risk_level, daily_pnl, exposure_pct, concentration, max_daily_loss_pct,
                whale_signals=whale_signals
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
                reasoning=reasoning,
                whale_short_pct=_whale_short_pct,
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
        max_daily_loss: float,
        whale_signals: List[str] = None
    ) -> str:
        """Generate LLM-based risk reasoning"""
        try:
            whale_section = ""
            if whale_signals:
                whale_section = "\nWHALE INTELLIGENCE SIGNALS:\n" + "\n".join(f"- {s}" for s in whale_signals) + "\n"

            prompt = f"""Assess the risk profile of this trading portfolio:

RISK METRICS:
- Risk Level: {risk_level}
- Daily P&L: ${daily_pnl:+.2f}
- Max Daily Loss Limit: {max_daily_loss}%
- Portfolio Exposure: {exposure_pct:.1f}% of capital
- Concentration Risk: {concentration}
{whale_section}
FUND POLICY CONTEXT:
This fund uses a scale-out exit strategy: positions are partially closed at 25% (33% of TP range), 35% (60% of TP range), with the remainder riding to full TP or trailing stop. Open positions therefore carry reducing risk as price progresses — factor this into your exposure assessment. A position that has already scaled out tranche 1 has its SL at breakeven, so the residual risk on the runner is effectively zero.

Provide brief risk assessment in JSON:
{{
  "risk_interpretation": "brief assessment including scale-out status and whale signals if present",
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
