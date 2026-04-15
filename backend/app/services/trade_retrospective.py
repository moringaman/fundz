"""Trade Retrospective Engine — analyses closed trades to find patterns,
learn from wins/losses, and recommend parameter adjustments.

Runs every 20 min alongside CIO report. Feeds insights back into agent
LLM prompts via `_build_team_context()` in agent_scheduler.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from app.clients.phemex import PhemexClient
from app.config import settings
from app.services.indicators import IndicatorService

logger = logging.getLogger(__name__)


@dataclass
class TradeAnalysis:
    """Analysis of a single closed trade."""
    symbol: str
    agent_id: Optional[str]
    side: str  # long | short
    entry_price: float
    exit_price: float
    entry_time: str
    exit_time: str
    net_pnl: float
    pnl_pct: float
    result: str  # win | loss | breakeven
    holding_hours: float
    # Market context at entry
    rsi_at_entry: Optional[float] = None
    trend_at_entry: Optional[str] = None  # up | down | sideways
    volatility_at_entry: Optional[str] = None  # high | medium | low
    # Post-trade analysis
    max_favorable: Optional[float] = None  # best unrealized % during trade
    max_adverse: Optional[float] = None    # worst unrealized % during trade
    exit_efficiency: Optional[float] = None  # actual exit % / best possible %
    pattern_label: Optional[str] = None


class TradeRetrospectiveService:
    """Analyses closed trades to identify patterns and recommend improvements."""

    def __init__(self):
        self.indicator_service = IndicatorService()
        self.phemex = PhemexClient(
            api_key=settings.phemex_api_key,
            api_secret=settings.phemex_api_secret,
            testnet=settings.phemex_testnet,
        )
        self._last_analysis_time: Optional[datetime] = None
        self._cached_result: Optional[Dict] = None
        # Track which trades we've already analyzed (by entry_time+symbol+agent)
        self._analyzed_trade_keys: set = set()

    async def analyze_recent_trades(
        self,
        agents_list: List[Dict],
        lookback_hours: int = 48,
    ) -> Optional[Dict[str, Any]]:
        """Main entry point — analyse closed trades from the last N hours.

        Returns a dict with:
        - trade_analyses: List[TradeAnalysis] — per-trade breakdowns
        - agent_insights: Dict[agent_id, insight_dict] — per-agent patterns
        - parameter_adjustments: List[Dict] — recommended SL/TP changes
        - summary: str — human-readable summary
        """
        try:
            from app.services.paper_trading import paper_trading
            closed = await paper_trading.get_closed_trades(limit=200)
            if not closed:
                return None

            cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)
            recent = []
            for t in closed:
                exit_str = t.get("exit_time")
                if not exit_str:
                    continue
                exit_dt = datetime.fromisoformat(exit_str)
                # Strip timezone info so comparison is always naive UTC
                if exit_dt.tzinfo is not None:
                    exit_dt = exit_dt.replace(tzinfo=None)
                if exit_dt > cutoff:
                    recent.append(t)
            if not recent:
                return None

            # Filter out already-analyzed trades (only analyze new ones)
            new_trades = []
            for t in recent:
                key = f"{t.get('entry_time')}_{t.get('symbol')}_{t.get('agent_id')}"
                if key not in self._analyzed_trade_keys:
                    new_trades.append(t)
                    self._analyzed_trade_keys.add(key)

            # If no new trades but we have cached results, return those
            if not new_trades and self._cached_result:
                return self._cached_result

            # Even if no new trades, re-analyze all recent for complete picture
            trades_to_analyze = recent

            # Fetch market data for analysis context
            symbols = list(set(t["symbol"] for t in trades_to_analyze))
            market_data: Dict[str, pd.DataFrame] = {}
            for sym in symbols:
                try:
                    klines = await self.phemex.get_klines(sym, "1h", 200)
                    data = klines if isinstance(klines, list) else klines.get("data", [])
                    if data:
                        df = pd.DataFrame(
                            data,
                            columns=["timestamp", "resolution", "open", "high", "low", "close", "turnover", "volume", "symbol"],
                        )
                        for col in ["open", "high", "low", "close", "volume"]:
                            df[col] = pd.to_numeric(df[col], errors="coerce")
                        df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
                        df = df.sort_values("timestamp").reset_index(drop=True)
                        # Calculate indicators
                        df["rsi"] = self.indicator_service.calculate_rsi(df["close"])
                        sma_20 = self.indicator_service.calculate_sma(df["close"], 20)
                        sma_50 = self.indicator_service.calculate_sma(df["close"], 50)
                        df["sma_20"] = sma_20
                        df["sma_50"] = sma_50
                        df["atr"] = self.indicator_service.calculate_atr(df["high"], df["low"], df["close"])
                        market_data[sym] = df
                except Exception as e:
                    logger.warning(f"Retrospective: failed to fetch market data for {sym}: {e}")

            # Analyze each trade
            analyses: List[TradeAnalysis] = []
            for trade in trades_to_analyze:
                analysis = self._analyze_trade(trade, market_data.get(trade["symbol"]))
                if analysis:
                    analyses.append(analysis)

            # Aggregate per-agent insights
            agents_by_id = {a["id"]: a for a in agents_list}
            agent_insights = self._compute_agent_insights(analyses, agents_by_id)

            # Aggregate cross-agent strategy insights
            strategy_insights = self._compute_strategy_insights(analyses, agents_by_id)

            # Generate parameter adjustment recommendations (including SL widening)
            adjustments = self._recommend_adjustments(analyses, agents_by_id)

            # Build summary
            summary = self._build_summary(analyses, agent_insights)

            result = {
                "trade_analyses": [self._analysis_to_dict(a) for a in analyses],
                "agent_insights": agent_insights,
                "strategy_insights": strategy_insights,
                "parameter_adjustments": adjustments,
                "summary": summary,
                "analyzed_at": datetime.utcnow().isoformat(),
                "trade_count": len(analyses),
            }

            self._cached_result = result
            self._last_analysis_time = datetime.utcnow()
            return result

        except Exception as e:
            logger.error(f"Trade retrospective failed: {e}", exc_info=True)
            return None

    def _analyze_trade(
        self,
        trade: Dict,
        market_df: Optional[pd.DataFrame],
    ) -> Optional[TradeAnalysis]:
        """Analyze a single closed trade against market context."""
        try:
            entry_time = datetime.fromisoformat(trade["entry_time"])
            exit_time = datetime.fromisoformat(trade["exit_time"])
            if entry_time.tzinfo is not None:
                entry_time = entry_time.replace(tzinfo=None)
            if exit_time.tzinfo is not None:
                exit_time = exit_time.replace(tzinfo=None)
            holding_hours = (exit_time - entry_time).total_seconds() / 3600

            analysis = TradeAnalysis(
                symbol=trade["symbol"],
                agent_id=trade.get("agent_id"),
                side=trade["side"],
                entry_price=trade["entry_price"],
                exit_price=trade["exit_price"],
                entry_time=trade["entry_time"],
                exit_time=trade["exit_time"],
                net_pnl=trade["net_pnl"],
                pnl_pct=trade["pnl_pct"],
                result=trade["result"],
                holding_hours=round(holding_hours, 1),
            )

            if market_df is not None and not market_df.empty:
                entry_ts = entry_time.timestamp()
                exit_ts = exit_time.timestamp()

                # Find closest candle to entry
                entry_idx = (market_df["timestamp"] - entry_ts).abs().idxmin()
                if entry_idx is not None:
                    analysis.rsi_at_entry = round(market_df.loc[entry_idx, "rsi"], 1) if pd.notna(market_df.loc[entry_idx, "rsi"]) else None
                    sma20 = market_df.loc[entry_idx, "sma_20"]
                    sma50 = market_df.loc[entry_idx, "sma_50"]
                    close = market_df.loc[entry_idx, "close"]
                    if pd.notna(sma20) and pd.notna(sma50):
                        if close > sma20 > sma50:
                            analysis.trend_at_entry = "up"
                        elif close < sma20 < sma50:
                            analysis.trend_at_entry = "down"
                        else:
                            analysis.trend_at_entry = "sideways"

                    atr = market_df.loc[entry_idx, "atr"]
                    if pd.notna(atr) and close > 0:
                        atr_pct = atr / close * 100
                        if atr_pct > 3:
                            analysis.volatility_at_entry = "high"
                        elif atr_pct > 1.5:
                            analysis.volatility_at_entry = "medium"
                        else:
                            analysis.volatility_at_entry = "low"

                # MFE/MAE: max favorable/adverse excursion during trade
                trade_candles = market_df[
                    (market_df["timestamp"] >= entry_ts) & (market_df["timestamp"] <= exit_ts)
                ]
                if not trade_candles.empty:
                    if trade["side"] == "long":
                        max_price = trade_candles["high"].max()
                        min_price = trade_candles["low"].min()
                        analysis.max_favorable = round((max_price - trade["entry_price"]) / trade["entry_price"] * 100, 2)
                        analysis.max_adverse = round((trade["entry_price"] - min_price) / trade["entry_price"] * 100, 2)
                    else:  # short
                        max_price = trade_candles["high"].max()
                        min_price = trade_candles["low"].min()
                        analysis.max_favorable = round((trade["entry_price"] - min_price) / trade["entry_price"] * 100, 2)
                        analysis.max_adverse = round((max_price - trade["entry_price"]) / trade["entry_price"] * 100, 2)

                    # Exit efficiency: how much of the max favorable move was captured
                    if analysis.max_favorable and analysis.max_favorable > 0:
                        actual_pct = abs(trade["pnl_pct"])
                        analysis.exit_efficiency = round(min(actual_pct / analysis.max_favorable, 1.0), 2) if trade["result"] == "win" else 0.0

                # Label trade pattern
                analysis.pattern_label = self._label_pattern(analysis)

            return analysis
        except Exception as e:
            logger.debug(f"Trade analysis failed: {e}")
            return None

    def _label_pattern(self, a: TradeAnalysis) -> str:
        """Classify the trade into a pattern category for learning."""
        labels = []

        # Timing patterns
        if a.holding_hours < 1:
            labels.append("scalp")
        elif a.holding_hours < 6:
            labels.append("swing_short")
        elif a.holding_hours < 24:
            labels.append("swing_medium")
        else:
            labels.append("swing_long")

        # Entry quality
        if a.result == "win":
            if a.exit_efficiency and a.exit_efficiency > 0.7:
                labels.append("good_exit")
            elif a.exit_efficiency and a.exit_efficiency < 0.3:
                labels.append("early_exit")
            if a.max_adverse and a.max_adverse < 1.0:
                labels.append("clean_entry")
        else:
            if a.max_favorable and a.max_favorable > 2.0:
                labels.append("missed_exit")  # was profitable but didn't take profits
            if a.max_adverse and a.max_adverse > 3.0:
                labels.append("poor_entry")

        # Trend alignment
        if a.trend_at_entry:
            if (a.side == "long" and a.trend_at_entry == "up") or \
               (a.side == "short" and a.trend_at_entry == "down"):
                labels.append("trend_aligned")
            elif (a.side == "long" and a.trend_at_entry == "down") or \
                 (a.side == "short" and a.trend_at_entry == "up"):
                labels.append("counter_trend")

        # RSI context
        if a.rsi_at_entry is not None:
            if a.side == "long" and a.rsi_at_entry > 70:
                labels.append("overbought_entry")
            elif a.side == "long" and a.rsi_at_entry < 30:
                labels.append("oversold_entry")
            elif a.side == "short" and a.rsi_at_entry < 30:
                labels.append("oversold_short")
            elif a.side == "short" and a.rsi_at_entry > 70:
                labels.append("overbought_short")

        return ", ".join(labels) if labels else "uncategorized"

    def _compute_agent_insights(
        self,
        analyses: List[TradeAnalysis],
        agents_by_id: Dict[str, Dict],
    ) -> Dict[str, Dict[str, Any]]:
        """Aggregate trade analyses into per-agent pattern insights."""
        agent_trades: Dict[str, List[TradeAnalysis]] = {}
        for a in analyses:
            aid = a.agent_id or "__none__"
            agent_trades.setdefault(aid, []).append(a)

        insights: Dict[str, Dict[str, Any]] = {}
        for agent_id, trades in agent_trades.items():
            if agent_id == "__none__":
                continue
            wins = [t for t in trades if t.result == "win"]
            losses = [t for t in trades if t.result == "loss"]
            total = len(trades)
            if total == 0:
                continue

            win_rate = len(wins) / total
            avg_win_pct = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
            avg_loss_pct = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0
            avg_holding_win = sum(t.holding_hours for t in wins) / len(wins) if wins else 0
            avg_holding_loss = sum(t.holding_hours for t in losses) / len(losses) if losses else 0

            # Pattern frequency
            patterns = {}
            for t in trades:
                if t.pattern_label:
                    for p in t.pattern_label.split(", "):
                        patterns.setdefault(p, {"total": 0, "wins": 0})
                        patterns[p]["total"] += 1
                        if t.result == "win":
                            patterns[p]["wins"] += 1

            # Best/worst patterns
            pattern_win_rates = {
                p: s["wins"] / s["total"]
                for p, s in patterns.items()
                if s["total"] >= 2  # only with enough samples
            }
            best_pattern = max(pattern_win_rates, key=pattern_win_rates.get) if pattern_win_rates else None
            worst_pattern = min(pattern_win_rates, key=pattern_win_rates.get) if pattern_win_rates else None

            # Exit efficiency
            efficiencies = [t.exit_efficiency for t in wins if t.exit_efficiency is not None]
            avg_exit_eff = sum(efficiencies) / len(efficiencies) if efficiencies else None

            # MFE/MAE stats
            win_mfe = [t.max_favorable for t in wins if t.max_favorable is not None]
            loss_mfe = [t.max_favorable for t in losses if t.max_favorable is not None]
            loss_mae = [t.max_adverse for t in losses if t.max_adverse is not None]

            agent_name = agents_by_id.get(agent_id, {}).get("name", agent_id)

            insight = {
                "agent_name": agent_name,
                "total_trades": total,
                "win_rate": round(win_rate, 3),
                "avg_win_pct": round(avg_win_pct, 2),
                "avg_loss_pct": round(avg_loss_pct, 2),
                "avg_holding_win_hours": round(avg_holding_win, 1),
                "avg_holding_loss_hours": round(avg_holding_loss, 1),
                "avg_exit_efficiency": round(avg_exit_eff, 2) if avg_exit_eff is not None else None,
                "best_pattern": best_pattern,
                "worst_pattern": worst_pattern,
                "strengths": [],
                "weaknesses": [],
                "learning_summary": "",
            }

            # Derive strengths/weaknesses
            if avg_exit_eff is not None and avg_exit_eff > 0.6:
                insight["strengths"].append("Good exit timing — captures most of favorable moves")
            elif avg_exit_eff is not None and avg_exit_eff < 0.3:
                insight["weaknesses"].append("Exits too early — leaving profit on the table")

            if loss_mfe and sum(loss_mfe) / len(loss_mfe) > 2.0:
                insight["weaknesses"].append("Many losing trades were profitable at some point — consider trailing stops")

            if best_pattern and pattern_win_rates.get(best_pattern, 0) > 0.7:
                insight["strengths"].append(f"Excels at {best_pattern} trades ({pattern_win_rates[best_pattern]:.0%} win rate)")

            if worst_pattern and pattern_win_rates.get(worst_pattern, 0) < 0.3:
                insight["weaknesses"].append(f"Struggles with {worst_pattern} trades ({pattern_win_rates[worst_pattern]:.0%} win rate)")

            if avg_holding_loss > 0 and avg_holding_win > 0 and avg_holding_loss > avg_holding_win * 2:
                insight["weaknesses"].append("Holds losing trades much longer than winners — tighten stop-losses")

            if "counter_trend" in (worst_pattern or ""):
                insight["weaknesses"].append("Counter-trend trades underperform — consider skipping them")

            # Build learning summary for LLM prompt
            parts = []
            if insight["strengths"]:
                parts.append(f"Strengths: {'; '.join(insight['strengths'])}")
            if insight["weaknesses"]:
                parts.append(f"Weaknesses: {'; '.join(insight['weaknesses'])}")
            if best_pattern:
                parts.append(f"Best setup: {best_pattern}")
            insight["learning_summary"] = ". ".join(parts) if parts else "Insufficient data for pattern analysis"

            insights[agent_id] = insight

        return insights

    def _compute_strategy_insights(
        self,
        analyses: List[TradeAnalysis],
        agents_by_id: Dict[str, Dict],
    ) -> Dict[str, Dict[str, Any]]:
        """Aggregate trade analyses across ALL agents of the same strategy type.

        This produces cross-agent learning: if every momentum agent is losing
        on counter-trend entries, the insight applies to momentum as a whole.
        """
        # Map agent_id → strategy_type
        agent_strategy: Dict[str, str] = {}
        for agent_id, agent in agents_by_id.items():
            stype = (agent.get("config") or {}).get("strategy_type") or agent.get("strategy_type", "")
            if stype:
                agent_strategy[agent_id] = stype

        # Group by strategy
        strategy_trades: Dict[str, List[TradeAnalysis]] = {}
        for a in analyses:
            if not a.agent_id:
                continue
            stype = agent_strategy.get(a.agent_id)
            if stype:
                strategy_trades.setdefault(stype, []).append(a)

        insights: Dict[str, Dict[str, Any]] = {}
        for stype, trades in strategy_trades.items():
            if len(trades) < 2:
                continue  # need at least 2 trades for any meaningful signal

            wins = [t for t in trades if t.result == "win"]
            losses = [t for t in trades if t.result == "loss"]
            total = len(trades)
            win_rate = len(wins) / total
            avg_win_pct = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0.0
            avg_loss_pct = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0.0

            # Pattern frequency across all agents in this strategy
            patterns: Dict[str, Dict] = {}
            for t in trades:
                if t.pattern_label:
                    for p in t.pattern_label.split(", "):
                        patterns.setdefault(p, {"total": 0, "wins": 0})
                        patterns[p]["total"] += 1
                        if t.result == "win":
                            patterns[p]["wins"] += 1

            pattern_win_rates = {
                p: s["wins"] / s["total"]
                for p, s in patterns.items()
                if s["total"] >= 2
            }
            best_pattern = max(pattern_win_rates, key=pattern_win_rates.get) if pattern_win_rates else None
            worst_pattern = min(pattern_win_rates, key=pattern_win_rates.get) if pattern_win_rates else None

            # Confidence multiplier: strategy-level WR drives a multiplier applied
            # to all agents of this type during signal generation.
            # Excellent (>65%) → +10% boost; Poor (<35%) → -20% penalty; neutral otherwise
            if win_rate > 0.65:
                confidence_adj = 0.10
                confidence_adj_reason = f"{stype} strategy firing well across all agents ({win_rate:.0%} WR) — boosting confidence"
            elif win_rate < 0.35:
                confidence_adj = -0.20
                confidence_adj_reason = f"{stype} strategy underperforming across all agents ({win_rate:.0%} WR) — reducing confidence"
            else:
                confidence_adj = 0.0
                confidence_adj_reason = ""

            weaknesses = []
            strengths = []

            if worst_pattern and pattern_win_rates.get(worst_pattern, 1.0) < 0.30:
                weaknesses.append(f"{worst_pattern} setups fail strategy-wide ({pattern_win_rates[worst_pattern]:.0%} WR)")
            if best_pattern and pattern_win_rates.get(best_pattern, 0.0) > 0.70:
                strengths.append(f"{best_pattern} setups work strategy-wide ({pattern_win_rates[best_pattern]:.0%} WR)")
            if avg_loss_pct < -3.0:
                weaknesses.append(f"Average loss is large ({avg_loss_pct:.1f}%) — SL may be too wide across strategy")
            if win_rate < 0.35:
                weaknesses.append(f"Win rate below 35% across all {stype} agents — consider pausing strategy or reviewing regime fit")

            # Agents contributing to this strategy
            agent_ids = list({a.agent_id for a in trades if a.agent_id})

            insights[stype] = {
                "strategy_type": stype,
                "total_trades": total,
                "agent_count": len(agent_ids),
                "win_rate": round(win_rate, 3),
                "avg_win_pct": round(avg_win_pct, 2),
                "avg_loss_pct": round(avg_loss_pct, 2),
                "best_pattern": best_pattern,
                "worst_pattern": worst_pattern,
                "strengths": strengths,
                "weaknesses": weaknesses,
                "confidence_adj": confidence_adj,
                "confidence_adj_reason": confidence_adj_reason,
            }

        return insights

    def _recommend_adjustments(
        self,
        analyses: List[TradeAnalysis],
        agents_by_id: Dict[str, Dict],
    ) -> List[Dict[str, Any]]:
        """Recommend SL/TP parameter adjustments based on trade patterns."""
        adjustments = []

        # Group by agent
        agent_trades: Dict[str, List[TradeAnalysis]] = {}
        for a in analyses:
            if a.agent_id:
                agent_trades.setdefault(a.agent_id, []).append(a)

        for agent_id, trades in agent_trades.items():
            if len(trades) < 2:
                continue  # need minimum sample size

            losses = [t for t in trades if t.result == "loss"]
            wins = [t for t in trades if t.result == "win"]

            if not losses and not wins:
                continue

            agent_name = agents_by_id.get(agent_id, {}).get("name", agent_id)

            # Check if losses have high MFE (trades that were profitable but ended as losses)
            loss_mfes = [t.max_favorable for t in losses if t.max_favorable is not None]
            if loss_mfes and sum(loss_mfes) / len(loss_mfes) > 2.0:
                # Recommend trailing stop or tighter TP
                avg_mfe = sum(loss_mfes) / len(loss_mfes)
                recommended_tp = round(avg_mfe * 0.6, 1)  # capture 60% of typical favorable move
                if 1.0 <= recommended_tp <= 15.0:
                    adjustments.append({
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "take_profit_pct": recommended_tp,
                        "reason": f"Losing trades had avg {avg_mfe:.1f}% favorable move before reversing — tighter TP at {recommended_tp:.1f}%",
                    })

            # Check if losses have very high MAE (stops too wide)
            loss_maes = [t.max_adverse for t in losses if t.max_adverse is not None]
            if loss_maes:
                avg_mae = sum(loss_maes) / len(loss_maes)
                win_maes = [t.max_adverse for t in wins if t.max_adverse is not None]
                avg_win_mae = sum(win_maes) / len(win_maes) if win_maes else 1.0

                # If losing trades go much further against us than winning trades — tighten SL
                if avg_mae > avg_win_mae * 2 and avg_mae > 2.0:
                    recommended_sl = round(avg_win_mae * 1.5, 1)  # 1.5x the typical winner's drawdown
                    if 0.5 <= recommended_sl <= 8.0:
                        adjustments.append({
                            "agent_id": agent_id,
                            "agent_name": agent_name,
                            "stop_loss_pct": recommended_sl,
                            "reason": f"Stop-losses too wide (avg {avg_mae:.1f}% adverse) — tighter SL at {recommended_sl:.1f}% based on winning trade drawdowns",
                        })

            # ── NEW: detect stops being hit by noise (SL too tight) ──────────
            # Signal: many losses have very small MAE (< 1%) but good MFE later
            # — the trade was stopped out on a minor dip before recovering.
            # Recommend a wider SL to give the trade room to breathe.
            if wins and losses:
                # Small-MAE losses: stopped out within 1% of entry
                small_mae_losses = [t for t in losses if t.max_adverse is not None and t.max_adverse < 1.0]
                if len(small_mae_losses) >= 2 and len(small_mae_losses) / max(len(losses), 1) >= 0.5:
                    # At least half of losses are small-MAE — likely noise stops
                    avg_small_mae = sum(t.max_adverse for t in small_mae_losses) / len(small_mae_losses)
                    # Only recommend wider SL if wins have higher MAE (they survive the dip)
                    if win_maes:
                        avg_win_mae_for_wide = sum(win_maes) / len(win_maes)
                        if avg_win_mae_for_wide > avg_small_mae * 1.5:
                            recommended_sl = round(avg_win_mae_for_wide * 1.2, 1)
                            current_sl = (agents_by_id.get(agent_id) or {}).get("config", {})
                            if isinstance(current_sl, dict):
                                current_sl = current_sl.get("stop_loss_pct", 999)
                            else:
                                current_sl = 999
                            if 0.5 <= recommended_sl <= 10.0 and recommended_sl > float(current_sl) * 0.9:
                                adjustments.append({
                                    "agent_id": agent_id,
                                    "agent_name": agent_name,
                                    "stop_loss_pct": recommended_sl,
                                    "reason": (
                                        f"{len(small_mae_losses)} losses stopped out within 1% of entry "
                                        f"(avg MAE {avg_small_mae:.2f}%) while wins survive avg {avg_win_mae_for_wide:.2f}% dip "
                                        f"— widening SL to {recommended_sl:.1f}% to reduce noise stops"
                                    ),
                                })

            # Check for exit efficiency issues (leaving money on the table)
            win_effs = [t.exit_efficiency for t in wins if t.exit_efficiency is not None]
            if win_effs and sum(win_effs) / len(win_effs) < 0.3:
                avg_win_mfe = sum(t.max_favorable for t in wins if t.max_favorable) / max(len([t for t in wins if t.max_favorable]), 1)
                if avg_win_mfe > 3.0:
                    recommended_tp = round(avg_win_mfe * 0.5, 1)
                    if 1.0 <= recommended_tp <= 15.0:
                        adjustments.append({
                            "agent_id": agent_id,
                            "agent_name": agent_name,
                            "take_profit_pct": recommended_tp,
                            "reason": f"Exit efficiency low ({sum(win_effs)/len(win_effs):.0%}) — raise TP to {recommended_tp:.1f}% to capture more of the move",
                        })

        return adjustments

    def _build_summary(
        self,
        analyses: List[TradeAnalysis],
        agent_insights: Dict[str, Dict],
    ) -> str:
        """Build a concise human-readable summary."""
        if not analyses:
            return "No recent trades to analyse."

        total = len(analyses)
        wins = sum(1 for a in analyses if a.result == "win")
        losses = sum(1 for a in analyses if a.result == "loss")
        total_pnl = sum(a.net_pnl for a in analyses)

        parts = [f"Reviewed {total} trades: {wins}W/{losses}L, net ${total_pnl:+.2f}"]

        # Per-agent highlights
        for aid, insight in agent_insights.items():
            name = insight.get("agent_name", aid[:8])
            wr = insight.get("win_rate", 0)
            if insight.get("weaknesses"):
                parts.append(f"{name} ({wr:.0%} WR): {insight['weaknesses'][0]}")
            elif insight.get("strengths"):
                parts.append(f"{name} ({wr:.0%} WR): {insight['strengths'][0]}")

        return ". ".join(parts[:5])  # Cap at 5 lines

    def _analysis_to_dict(self, a: TradeAnalysis) -> Dict[str, Any]:
        return {
            "symbol": a.symbol,
            "agent_id": a.agent_id,
            "side": a.side,
            "entry_price": a.entry_price,
            "exit_price": a.exit_price,
            "entry_time": a.entry_time,
            "exit_time": a.exit_time,
            "net_pnl": a.net_pnl,
            "pnl_pct": a.pnl_pct,
            "result": a.result,
            "holding_hours": a.holding_hours,
            "rsi_at_entry": a.rsi_at_entry,
            "trend_at_entry": a.trend_at_entry,
            "volatility_at_entry": a.volatility_at_entry,
            "max_favorable": a.max_favorable,
            "max_adverse": a.max_adverse,
            "exit_efficiency": a.exit_efficiency,
            "pattern_label": a.pattern_label,
        }


# Singleton
trade_retrospective = TradeRetrospectiveService()
