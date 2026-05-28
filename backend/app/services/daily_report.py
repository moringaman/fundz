"""
Daily Report Service — Generates end-of-day reports by aggregating
fund metrics, team discussions, P&L, trades, positions, and market conditions.

A report is generated once per day (triggered from the scheduler) and
persisted to the `daily_reports` table.
"""

from __future__ import annotations
from datetime import date, datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import logging

from sqlalchemy import select, func as sqlfunc, cast, Date

from app.database import get_async_session
from app.models import (
    DailyReport,
    AgentRunRecord,
    AgentMetricRecord,
    TeamChatMessageRecord,
)

logger = logging.getLogger(__name__)


class DailyReportService:

    async def generate_daily_report(
        self,
        report_date: Optional[date] = None,
        force: bool = False,
    ) -> dict:
        """
        Build and persist the daily report for *report_date* (defaults to today).
        If a report already exists for that date it is returned unless *force* is True.
        """
        target = report_date or date.today()
        date_str = target.isoformat()

        # Check for existing report
        if not force:
            existing = await self._get_report_from_db(date_str)
            if existing:
                logger.info(f"Daily report for {date_str} already exists")
                return existing

        logger.info(f"Generating daily report for {date_str}")

        # Gather all data concurrently-ish
        pnl_data = await self._gather_pnl()
        trade_data = await self._gather_trade_metrics(target)
        position_data = await self._gather_positions()
        balance_data = await self._gather_balances()
        agent_perf = await self._gather_agent_performance()
        market_data = await self._gather_market_conditions()
        risk_data = await self._gather_risk_summary()
        chat_data = await self._gather_discussion_summary(target)
        cio_data = await self._gather_cio_summary()

        # Build the leaderboard
        leaderboard = sorted(
            agent_perf.get("agents", []),
            key=lambda a: a.get("total_pnl", 0),
            reverse=True,
        )
        for i, entry in enumerate(leaderboard):
            entry["rank"] = i + 1

        best = leaderboard[0] if leaderboard else None
        worst = leaderboard[-1] if leaderboard else None

        portfolio_value = sum(b.get("value_usd", 0) for b in balance_data)
        # Include unrealized P&L from open positions
        unrealized_from_positions = sum(
            p.get("unrealized_pnl", 0) or 0 for p in position_data
        )
        portfolio_value += unrealized_from_positions

        report = {
            "report_date": date_str,
            "market_conditions": market_data,
            "total_pnl": pnl_data.get("total_pnl", 0),
            "realized_pnl": pnl_data.get("realized_pnl", 0),
            "unrealized_pnl": pnl_data.get("unrealized_pnl", 0),
            "daily_return_pct": pnl_data.get("daily_return_pct", 0),
            "trades_opened": trade_data.get("opened", 0),
            "trades_closed": trade_data.get("closed", 0),
            "total_buy_volume": trade_data.get("buy_volume", 0),
            "total_sell_volume": trade_data.get("sell_volume", 0),
            "open_positions_count": len(position_data),
            "team_performance": agent_perf,
            "team_discussion_summary": chat_data.get("summary", ""),
            "team_message_count": chat_data.get("count", 0),
            "agent_leaderboard": leaderboard,
            "best_agent_id": best["agent_id"] if best else None,
            "worst_agent_id": worst["agent_id"] if worst else None,
            "risk_summary": risk_data,
            "portfolio_value": portfolio_value,
            "portfolio_balances": {b["asset"]: b["available"] for b in balance_data},
            "cio_sentiment": cio_data.get("sentiment", "neutral"),
            "cio_summary": cio_data.get("summary", ""),
        }

        await self._persist_report(report)
        return report

    # ── Data gatherers ────────────────────────────────────────────────────────

    async def _gather_pnl(self) -> dict:
        try:
            from app.services.paper_trading import paper_trading
            pnl = await paper_trading.calculate_pnl()
            total = pnl.get("total_pnl", 0)
            realized = pnl.get("realized_pnl", 0)
            unrealized = pnl.get("unrealized_pnl", 0)
            balances = await paper_trading.get_all_balances()
            usdt = next((b for b in balances if b.asset == "USDT"), None)
            current_balance = (usdt.available + (usdt.locked or 0)) if usdt else 50000.0
            starting_capital = max(0, current_balance - total) if total else current_balance
            daily_return = (total / starting_capital * 100) if starting_capital > 0 else 0
            # APR: annualize from first trade date
            apr = 0.0
            try:
                all_trades = await paper_trading.get_closed_trades(limit=9999)
                if all_trades:
                    t = all_trades[-1]
                    first_date = t.get("exit_time") or t.get("filled_at") or t.get("created_at")
                    if first_date:
                        if isinstance(first_date, str):
                            first_date = datetime.fromisoformat(first_date.replace("Z", "+00:00"))
                        days = max(1, (datetime.utcnow() - first_date).total_seconds() / 86400)
                        apr = (total / starting_capital) * (365 / days) * 100
            except Exception:
                pass
            return {
                "total_pnl": total,
                "realized_pnl": realized,
                "unrealized_pnl": unrealized,
                "daily_return_pct": round(daily_return, 4),
                "apr": round(apr, 4),
                "starting_capital": round(starting_capital, 2),
            }
        except Exception as e:
            logger.error(f"Daily report – PnL gather failed: {e}")
            return {}

    async def _gather_trade_metrics(self, target: date) -> dict:
        try:
            from app.services.paper_trading import paper_trading
            orders = await paper_trading.get_orders(limit=500)

            day_start = datetime.combine(target, datetime.min.time()).replace(tzinfo=timezone.utc)
            day_end = day_start + timedelta(days=1)

            opened = 0
            closed = 0
            buy_vol = 0.0
            sell_vol = 0.0

            for o in orders:
                created = getattr(o, "created_at", None)
                if created and day_start <= created.replace(tzinfo=timezone.utc) < day_end:
                    side = getattr(o, "side", "").lower() if hasattr(o, "side") else ""
                    qty = float(getattr(o, "quantity", 0))
                    price = float(getattr(o, "price", 0))
                    vol = qty * price

                    if side == "buy":
                        opened += 1
                        buy_vol += vol
                    elif side == "sell":
                        closed += 1
                        sell_vol += vol

            return {"opened": opened, "closed": closed, "buy_volume": round(buy_vol, 2), "sell_volume": round(sell_vol, 2)}
        except Exception as e:
            logger.error(f"Daily report – trade metrics failed: {e}")
            return {}

    async def _gather_positions(self) -> list:
        try:
            from app.services.paper_trading import paper_trading
            positions = list(await paper_trading.get_positions_live() or [])
            # Also fetch live positions when in live mode
            try:
                from app.api.routes.settings import get_trading_prefs
                if not get_trading_prefs().paper_trading_default:
                    from app.services.live_trading import live_trading
                    positions.extend(list(await live_trading.get_positions() or []))
                    from app.services.hl_live_trading import hl_live_trading
                    positions.extend(list(await hl_live_trading.get_positions() or []))
            except Exception:
                pass
            return [
                {
                    "symbol": p.get("symbol", "") if isinstance(p, dict) else getattr(p, 'symbol', ''),
                    "side": p.get("side", "") if isinstance(p, dict) else str(getattr(getattr(p, 'side', None), 'value', getattr(p, 'side', ''))),
                    "quantity": p.get("quantity", 0) if isinstance(p, dict) else getattr(p, 'quantity', 0),
                    "entry_price": p.get("entry_price", 0) if isinstance(p, dict) else getattr(p, 'entry_price', 0),
                    "unrealized_pnl": p.get("unrealized_pnl", 0) if isinstance(p, dict) else getattr(p, 'unrealized_pnl', 0),
                }
                for p in positions
            ]
        except Exception as e:
            logger.error(f"Daily report – positions gather failed: {e}")
            return []

    async def _gather_balances(self) -> list:
        try:
            from app.services.paper_trading import paper_trading
            balances = await paper_trading.get_all_balances()

            results = []
            for b in balances:
                asset = getattr(b, "asset", "")
                available = float(getattr(b, "available", 0))
                locked = float(getattr(b, "locked", 0))
                total_held = available + locked

                if asset == "USDT":
                    value_usd = total_held
                elif total_held > 0:
                    # Convert to USD using live price
                    try:
                        price = await paper_trading.fetch_current_price(f"{asset}USDT")
                        value_usd = total_held * price if price > 0 else 0.0
                    except Exception:
                        value_usd = 0.0
                else:
                    value_usd = 0.0

                results.append({
                    "asset": asset,
                    "available": available,
                    "locked": locked,
                    "value_usd": value_usd,
                })
            return results
        except Exception as e:
            logger.error(f"Daily report – balances gather failed: {e}")
            return []

    async def _gather_agent_performance(self) -> dict:
        try:
            from app.services.agent_scheduler import agent_scheduler
            metrics = agent_scheduler._agent_metrics
            # Build name map from registered agents
            name_map = {a_id: cfg.get("name", a_id) for a_id, cfg in agent_scheduler._enabled_agents.items()}
            agents_list = []
            for agent_id, m in metrics.items():
                agents_list.append({
                    "agent_id": agent_id,
                    "name": name_map.get(agent_id, agent_id),
                    "total_pnl": m.total_pnl,
                    "win_rate": m.win_rate,
                    "total_runs": m.total_runs,
                    "buy_signals": m.buy_signals,
                    "sell_signals": m.sell_signals,
                    "hold_signals": m.hold_signals,
                })
            return {"agents": agents_list}
        except Exception as e:
            logger.error(f"Daily report – agent performance failed: {e}")
            return {"agents": []}

    async def _gather_market_conditions(self) -> dict:
        try:
            from app.services.agent_scheduler import agent_scheduler
            report = agent_scheduler._current_analyst_report
            if not report:
                return {}
            regime = report.market_regime
            # Phase 4 — surface the GMM statistical posterior alongside the
            # LLM regime label, and flag divergence when the two disagree on
            # macro direction. Both labels live in different vocabularies
            # (LLM: trending_up/ranging/etc; GMM: risk_on/range/risk_off) so
            # we map the LLM label to the same axis before comparing.
            stat_label = getattr(regime, 'statistical_label', None)
            stat_conf = getattr(regime, 'statistical_confidence', None)
            stat_posteriors = getattr(regime, 'statistical_posteriors', None)
            divergence = None
            if stat_label is not None:
                # LLM regime → expected GMM label
                llm_to_gmm = {
                    'trending_up': 'risk_on',
                    'trending_down': 'risk_off',
                    'ranging': 'range',
                    'volatility_expansion': 'risk_off',  # vol expansion usually ≈ risk-off in crypto
                }
                expected = llm_to_gmm.get(regime.regime)
                if expected and expected != stat_label:
                    divergence = f"LLM says {regime.regime} but GMM says {stat_label} (p={stat_conf:.2f})"

            return {
                "regime": regime.regime,
                "sentiment": regime.sentiment,
                "volatility": regime.volatility_regime,
                "correlation": regime.correlation_status,
                "top_opportunity": report.opportunities[0].symbol if report.opportunities else None,
                "analyst_recommendation": report.analyst_recommendation,
                "statistical_label": stat_label,
                "statistical_confidence": stat_conf,
                "statistical_posteriors": stat_posteriors,
                "regime_divergence": divergence,
            }
        except Exception as e:
            logger.debug(f"Daily report – market conditions failed: {e}")
            return {}

    async def _gather_risk_summary(self) -> dict:
        try:
            from app.services.agent_scheduler import agent_scheduler
            ra = agent_scheduler._current_risk_assessment
            if not ra:
                return {}
            return {
                "risk_level": ra.risk_level,
                "daily_pnl": ra.daily_pnl,
                "exposure_pct": getattr(ra, "exposure_pct_of_capital", 0),
                "concentration_risk": getattr(ra, "concentration_risk", "unknown"),
            }
        except Exception as e:
            logger.debug(f"Daily report – risk summary failed: {e}")
            return {}

    async def _gather_discussion_summary(self, target: date) -> dict:
        """Summarise the day's team chat messages."""
        try:
            from app.services.team_chat import team_chat
            messages = await team_chat.get_messages_for_date(target)
            count = len(messages)
            if count == 0:
                return {"summary": "No team discussions recorded today.", "count": 0}

            # Build a condensed summary from message types
            by_role: Dict[str, list] = {}
            warnings = 0
            for m in messages:
                role = m.get("agent_role", "unknown")
                by_role.setdefault(role, []).append(m)
                if m.get("message_type") == "warning":
                    warnings += 1

            lines = [f"{count} team messages exchanged today."]
            _ensure_profiles_loaded()
            for role, msgs in by_role.items():
                profile = AGENT_PROFILES.get(role, {"title": role})
                title = profile.get("title", role)
                lines.append(f"• {title}: {len(msgs)} contributions")
            if warnings:
                lines.append(f"⚠ {warnings} warning(s) were raised.")

            # Include the last CIO message as the day's closing thought
            cio_msgs = by_role.get("cio", [])
            if cio_msgs:
                lines.append(f"CIO closing remark: \"{cio_msgs[-1]['content'][:200]}\"")

            return {"summary": " ".join(lines), "count": count}
        except Exception as e:
            logger.error(f"Daily report – discussion summary failed: {e}")
            return {"summary": "", "count": 0}

    async def _gather_cio_summary(self) -> dict:
        try:
            from app.services.agent_scheduler import agent_scheduler
            report = agent_scheduler._current_cio_report
            if not report:
                return {}
            return {
                "sentiment": report.cio_sentiment,
                "summary": getattr(report, "executive_summary", "") or "",
            }
        except Exception as e:
            logger.debug(f"Daily report – CIO summary failed: {e}")
            return {}

    # ── Persistence ───────────────────────────────────────────────────────────

    async def _persist_report(self, data: dict) -> None:
        try:
            async with get_async_session() as db:
                # Upsert by report_date
                existing = await db.execute(
                    select(DailyReport).where(DailyReport.report_date == data["report_date"])
                )
                record = existing.scalars().first()

                if record:
                    for key, val in data.items():
                        if key != "report_date" and hasattr(record, key):
                            setattr(record, key, val)
                    record.generated_at = datetime.now(timezone.utc)
                else:
                    record = DailyReport(**data)
                    db.add(record)

                await db.commit()
                logger.info(f"Daily report persisted for {data['report_date']}")
        except Exception as e:
            logger.error(f"Failed to persist daily report: {e}")

    async def _get_report_from_db(self, date_str: str) -> Optional[dict]:
        try:
            async with get_async_session() as db:
                result = await db.execute(
                    select(DailyReport).where(DailyReport.report_date == date_str)
                )
                record = result.scalars().first()
                if not record:
                    return None
                return self._record_to_dict(record)
        except Exception as e:
            logger.error(f"Failed to fetch daily report: {e}")
            return None

    async def get_report(self, report_date: Optional[date] = None) -> Optional[dict]:
        """Get a daily report by date (from DB)."""
        target = report_date or date.today()
        return await self._get_report_from_db(target.isoformat())

    async def get_reports(self, limit: int = 30) -> List[dict]:
        """Get the most recent daily reports."""
        try:
            async with get_async_session() as db:
                result = await db.execute(
                    select(DailyReport)
                    .order_by(DailyReport.report_date.desc())
                    .limit(limit)
                )
                records = result.scalars().all()
                return [self._record_to_dict(r) for r in records]
        except Exception as e:
            logger.error(f"Failed to fetch daily reports: {e}")
            return []

    def _record_to_dict(self, r: DailyReport) -> dict:
        return {
            "id": r.id,
            "report_date": r.report_date,
            "generated_at": r.generated_at.isoformat() if r.generated_at else None,
            "market_conditions": r.market_conditions or {},
            "total_pnl": r.total_pnl,
            "realized_pnl": r.realized_pnl,
            "unrealized_pnl": r.unrealized_pnl,
            "daily_return_pct": r.daily_return_pct,
            "trades_opened": r.trades_opened,
            "trades_closed": r.trades_closed,
            "total_buy_volume": r.total_buy_volume,
            "total_sell_volume": r.total_sell_volume,
            "open_positions_count": r.open_positions_count,
            "team_performance": r.team_performance or {},
            "team_discussion_summary": r.team_discussion_summary,
            "team_message_count": r.team_message_count,
            "agent_leaderboard": r.agent_leaderboard or [],
            "best_agent_id": r.best_agent_id,
            "worst_agent_id": r.worst_agent_id,
            "risk_summary": r.risk_summary or {},
            "portfolio_value": r.portfolio_value,
            "portfolio_balances": r.portfolio_balances or {},
            "cio_sentiment": r.cio_sentiment,
            "cio_summary": r.cio_summary,
        }


# Import agent profiles for discussion summary
from app.services.team_chat import AGENT_PROFILES, _ensure_profiles_loaded

# Singleton
daily_report_service = DailyReportService()
