from typing import Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class PendingOrder:
    """Pending order waiting for execution"""
    order_id: str
    agent_id: str
    symbol: str
    side: str  # "buy" or "sell"
    quantity: float
    limit_price: Optional[float] = None
    timestamp: datetime = None


@dataclass
class ExecutionPriority:
    """Execution priority for an order"""
    order_id: str
    priority_score: float  # 0.0-1.0, higher = execute sooner
    reasoning: str
    estimated_slippage: float  # percentage


@dataclass
class ExecutionPlan:
    """Optimized execution plan for pending orders"""
    timestamp: datetime
    pending_orders_count: int
    execution_sequence: List[str]  # order IDs in recommended execution order
    priorities: List[ExecutionPriority]
    aggregate_slippage_estimate: float  # % of total order value
    recommended_action: str  # "execute_all", "batch_execute", "wait"
    reasoning: str


class ExecutionCoordinator:
    """
    Execution Coordinator: Optimizes order execution timing, routing, and
    slippage management. Responsible for determining execution priority
    across multiple pending orders from different agents.
    """

    def __init__(self):
        self.order_history = []  # Track recent executions for slippage analysis

    async def optimize_execution_plan(
        self,
        pending_orders: List[PendingOrder]
    ) -> ExecutionPlan:
        """
        Analyze pending orders and return optimized execution plan
        """
        timestamp = datetime.utcnow()

        if not pending_orders:
            return ExecutionPlan(
                timestamp=timestamp,
                pending_orders_count=0,
                execution_sequence=[],
                priorities=[],
                aggregate_slippage_estimate=0.0,
                recommended_action="wait",
                reasoning="No pending orders to execute"
            )

        try:
            # Calculate execution priorities
            priorities = []
            for order in pending_orders:
                priority = self._calculate_priority(order, pending_orders)
                priorities.append(priority)

            # Sort by priority score (highest first)
            priorities.sort(key=lambda p: p.priority_score, reverse=True)
            execution_sequence = [p.order_id for p in priorities]

            # Estimate aggregate slippage
            aggregate_slippage = sum(p.estimated_slippage for p in priorities) / len(priorities) if priorities else 0

            # Recommend action
            recommended_action = self._recommend_action(pending_orders, aggregate_slippage)
            reasoning = self._build_reasoning(pending_orders, priorities, aggregate_slippage)

            plan = ExecutionPlan(
                timestamp=timestamp,
                pending_orders_count=len(pending_orders),
                execution_sequence=execution_sequence,
                priorities=priorities,
                aggregate_slippage_estimate=aggregate_slippage,
                recommended_action=recommended_action,
                reasoning=reasoning
            )

            return plan

        except Exception as e:
            logger.error(f"Execution plan optimization failed: {e}")
            return self._default_execution_plan(timestamp, pending_orders)

    def _calculate_priority(
        self,
        order: PendingOrder,
        all_orders: List[PendingOrder]
    ) -> ExecutionPriority:
        """
        Calculate execution priority for a single order based on:
        - Order age (older = higher priority)
        - Position type (reduce risk first, then growth)
        - Liquidity conditions
        - Other pending orders for same symbol
        """
        priority_score = 0.5  # Base score

        # Factor 1: Order age (how long it's been waiting)
        now = datetime.utcnow()
        age_minutes = (now - (order.timestamp or now)).total_seconds() / 60 if order.timestamp else 0
        age_priority = min(age_minutes / 60, 1.0)  # Normalize to 1.0 max
        priority_score += age_priority * 0.2

        # Factor 2: Order type priority
        # Sell orders (reduce risk) get higher priority than buy orders
        if order.side == "sell":
            priority_score += 0.15
        else:
            priority_score += 0.05

        # Factor 3: Multiple orders for same symbol
        same_symbol_orders = [o for o in all_orders if o.symbol == order.symbol]
        if len(same_symbol_orders) > 1:
            # Execute sells before buys for same symbol
            if order.side == "sell":
                priority_score += 0.1
            else:
                priority_score -= 0.05

        # Factor 4: Order size (larger orders may have worse execution)
        # Smaller orders get slightly higher priority for better execution
        normalized_size = min(order.quantity / 1000, 1.0)  # Normalize to 1.0
        priority_score -= normalized_size * 0.05

        # Cap at 1.0
        priority_score = min(max(priority_score, 0.0), 1.0)

        # Estimate slippage
        estimated_slippage = self._estimate_slippage(order, normalized_size)

        reasoning = f"Age: {age_minutes:.0f}min ({age_priority:.1%}), Type: {order.side} (+{0.15 if order.side == 'sell' else 0.05:.2f}), Size: {order.quantity:.4f}"

        return ExecutionPriority(
            order_id=order.order_id,
            priority_score=priority_score,
            reasoning=reasoning,
            estimated_slippage=estimated_slippage
        )

    def _estimate_slippage(
        self,
        order: PendingOrder,
        normalized_size: float
    ) -> float:
        """
        Estimate slippage for order execution
        Based on: order size, symbol volatility, time of day, etc.
        """
        base_slippage = 0.02  # 0.2% base for crypto trades

        # Larger orders have more slippage
        size_slippage = normalized_size * 0.03  # Up to 0.3% for large orders

        # Sell orders might have slightly more slippage
        side_slippage = 0.01 if order.side == "sell" else 0.0

        # For now, assume stable conditions
        # In production, would check real order book depth
        total_slippage = base_slippage + size_slippage + side_slippage

        return min(total_slippage, 0.1)  # Cap at 1%

    def _recommend_action(
        self,
        pending_orders: List[PendingOrder],
        aggregate_slippage: float
    ) -> str:
        """
        Recommend execution strategy:
        - execute_all: Execute all orders immediately
        - batch_execute: Group similar orders and execute
        - wait: Market conditions not favorable, wait
        """
        if not pending_orders:
            return "wait"

        # Count order types
        sells = sum(1 for o in pending_orders if o.side == "sell")
        buys = len(pending_orders) - sells

        # If many sell orders (risk reduction), execute immediately
        if sells >= 2:
            return "execute_all"

        # If slippage is low and orders are old, execute
        max_age = max(
            (datetime.utcnow() - (o.timestamp or datetime.utcnow())).total_seconds() / 60
            for o in pending_orders if o.timestamp
        ) if any(o.timestamp for o in pending_orders) else 0

        if max_age > 30 and aggregate_slippage < 0.05:  # 30min old, <0.05% slippage
            return "execute_all"

        # Batch execution for mixed orders
        if buys > 0 and sells > 0:
            return "batch_execute"

        # Default: wait for better conditions
        return "wait"

    def _build_reasoning(
        self,
        pending_orders: List[PendingOrder],
        priorities: List[ExecutionPriority],
        aggregate_slippage: float
    ) -> str:
        """Build detailed reasoning for execution plan"""
        lines = [
            f"Total pending orders: {len(pending_orders)}",
            f"Estimated aggregate slippage: {aggregate_slippage:.3%}",
            "Execution priority (high to low):",
        ]

        # Show top 5 priorities
        for priority in priorities[:5]:
            lines.append(f"  {priority.order_id}: {priority.priority_score:.1%} "
                        f"(slippage: {priority.estimated_slippage:.3%}) - {priority.reasoning}")

        sells = sum(1 for o in pending_orders if o.side == "sell")
        buys = len(pending_orders) - sells
        if sells > 0:
            lines.append(f"Risk reduction: {sells} sell orders prioritized")
        if buys > 0:
            lines.append(f"Growth orders: {buys} buy orders")

        return "\n".join(lines)

    def record_execution(
        self,
        order_id: str,
        symbol: str,
        side: str,
        executed_price: float,
        expected_price: Optional[float] = None
    ):
        """Record execution for slippage analysis"""
        if expected_price:
            slippage = abs(executed_price - expected_price) / expected_price
            self.order_history.append({
                'order_id': order_id,
                'symbol': symbol,
                'side': side,
                'executed_price': executed_price,
                'expected_price': expected_price,
                'slippage': slippage,
                'timestamp': datetime.utcnow()
            })

            # Keep last 100 executions
            if len(self.order_history) > 100:
                self.order_history = self.order_history[-100:]

    def get_recent_slippage_stats(self) -> Dict:
        """Get recent execution slippage statistics"""
        if not self.order_history:
            return {
                'avg_slippage': 0.0,
                'max_slippage': 0.0,
                'min_slippage': 0.0,
                'executions_count': 0
            }

        slippages = [e['slippage'] for e in self.order_history]
        return {
            'avg_slippage': sum(slippages) / len(slippages),
            'max_slippage': max(slippages),
            'min_slippage': min(slippages),
            'executions_count': len(self.order_history)
        }

    def _default_execution_plan(
        self,
        timestamp: datetime,
        pending_orders: List[PendingOrder]
    ) -> ExecutionPlan:
        """Return safe default execution plan"""
        return ExecutionPlan(
            timestamp=timestamp,
            pending_orders_count=len(pending_orders),
            execution_sequence=[o.order_id for o in pending_orders],
            priorities=[],
            aggregate_slippage_estimate=0.0,
            recommended_action="wait",
            reasoning="Execution plan generation failed. Manual review recommended."
        )


# Global singleton
execution_coordinator = ExecutionCoordinator()
