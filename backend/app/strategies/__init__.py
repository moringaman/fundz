"""
Strategy Registry Loader
========================
Loads strategy definitions from registry.yaml and provides typed access.
This is the single Python interface to the YAML — all other code imports
from here rather than reading the file directly.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_REGISTRY_PATH = Path(__file__).parent / "registry.yaml"


@lru_cache(maxsize=1)
def _load_raw() -> dict:
    """Load and cache the raw YAML. Cached for process lifetime."""
    try:
        import yaml  # PyYAML
        with open(_REGISTRY_PATH) as f:
            data = yaml.safe_load(f)
        return data.get("strategies", {})
    except ImportError:
        logger.warning("PyYAML not installed — strategy registry unavailable. Run: pip install pyyaml")
        return {}
    except Exception as e:
        logger.error(f"Failed to load strategy registry: {e}")
        return {}


def get_all() -> dict[str, dict]:
    """Return all strategy definitions keyed by strategy_type slug."""
    return _load_raw()


def get(strategy_type: str) -> Optional[dict]:
    """Return a single strategy definition or None if not found."""
    return _load_raw().get(strategy_type)


def all_types() -> list[str]:
    """Return list of all strategy type slugs."""
    return list(_load_raw().keys())


def ai_proposable() -> list[str]:
    """Return strategy types the AI is allowed to propose creating."""
    return [k for k, v in _load_raw().items() if v.get("ai_propose", True) and not v.get("require_marina", False)]


def marina_gated() -> list[str]:
    """Return strategy types that require Marina's explicit recommendation before proposing."""
    return [k for k, v in _load_raw().items() if v.get("require_marina", False)]


def strategy_timeframes() -> dict[str, dict]:
    """Return STRATEGY_TIMEFRAMES-compatible dict for API/validation use."""
    result = {}
    for key, strat in _load_raw().items():
        tf = strat.get("timeframes", {})
        result[key] = {
            "default": tf.get("default", "1h"),
            "allowed": tf.get("allowed", ["1h"]),
        }
    return result


def strategy_profiles(risk_defaults: dict | None = None) -> dict[str, dict]:
    """
    Return STRATEGY_PROFILES-compatible dict for the agent scheduler.
    Merges YAML definitions with risk defaults from settings.
    """
    rd = risk_defaults or {}
    default_sl = rd.get("default_stop_loss_pct", 2.5)
    default_tp = rd.get("default_take_profit_pct", 6.0)

    result = {}
    for key, strat in _load_raw().items():
        risk = strat.get("risk", {})
        sl = risk.get("stop_loss_pct") or default_sl
        tp = risk.get("take_profit_pct") or default_tp
        result[key] = {
            "name": strat.get("label", key),
            "description": strat.get("description", ""),
            "stop_loss_pct": sl,
            "take_profit_pct": tp,
            "trailing_stop_pct": risk.get("trailing_stop_pct"),
            "indicators_config": strat.get("indicators", {}),
            "market_conditions": strat.get("market_conditions", []),
            "avoid_conditions": strat.get("avoid_conditions", []),
        }
    return result


def bootstrap_rr() -> dict[str, tuple[float, float]]:
    """Return _BOOTSTRAP_RR-compatible dict: {strategy_type: (sl_pct, tp_pct)}."""
    result = {}
    for key, strat in _load_raw().items():
        risk = strat.get("risk", {})
        sl = risk.get("stop_loss_pct", 2.5)
        tp = risk.get("take_profit_pct", 6.0)
        result[key] = (sl, tp)
    return result


def ai_prompt_summary() -> str:
    """
    Return a compact strategy reference string for inclusion in LLM prompts.
    Lists each strategy with its key conditions and naming conventions.
    """
    lines = ["Available strategy types:\n"]
    for key, strat in _load_raw().items():
        good = ", ".join(strat.get("market_conditions", [])) or "any"
        avoid = ", ".join(strat.get("avoid_conditions", [])) or "none"
        names = ", ".join(strat.get("agent_naming", [key])[:2])
        marina = " [requires Marina recommendation]" if strat.get("require_marina") else ""
        lines.append(
            f'  • **{key}** — {strat.get("label")}{marina}\n'
            f'    {strat.get("description", "")}\n'
            f'    Best in: {good}  |  Avoid: {avoid}\n'
            f'    SL: {strat.get("risk", {}).get("stop_loss_pct")}%  '
            f'TP: {strat.get("risk", {}).get("take_profit_pct")}%  '
            f'Trail: {strat.get("risk", {}).get("trailing_stop_pct")}%\n'
            f'    Example names: {names}\n'
        )
    return "\n".join(lines)


def for_ui(overrides: dict[str, Any] | None = None) -> list[dict]:
    """
    Return the UI-ready strategy list consumed by /api/strategies.
    If `overrides` is provided (keyed by strategy_type), DB values are
    merged on top of the YAML base: risk params, enabled flag, display_order.
    """
    result = []
    raw = _load_raw()
    ov = overrides or {}
    keys_ordered = sorted(raw.keys(), key=lambda k: ov.get(k, {}).get("display_order") or 999)
    for key in keys_ordered:
        strat = raw[key]
        db = ov.get(key, {})
        tf = strat.get("timeframes", {})
        yaml_risk = strat.get("risk", {})
        # DB risk overrides YAML where present
        merged_risk = {
            "stop_loss_pct":       db.get("default_stop_loss_pct")    or yaml_risk.get("stop_loss_pct"),
            "take_profit_pct":     db.get("default_take_profit_pct")  or yaml_risk.get("take_profit_pct"),
            "trailing_stop_pct":   db.get("default_trailing_stop_pct") or yaml_risk.get("trailing_stop_pct"),
        }
        result.append({
            "value": key,
            "label": strat.get("label", key),
            "description": strat.get("description", ""),
            "timeframes": tf.get("allowed", ["1h"]),
            "defaultTf": db.get("default_timeframe") or tf.get("default", "1h"),
            "risk": merged_risk,
            "yaml_risk": yaml_risk,                     # original YAML values (for reset UI)
            "indicators": strat.get("indicators", {}),
            "market_conditions": strat.get("market_conditions", []),
            "avoid_conditions": strat.get("avoid_conditions", []),
            "ai_guidance": strat.get("ai_guidance", ""),
            "ai_propose": strat.get("ai_propose", True),
            "require_marina": strat.get("require_marina", False),
            "enabled": db.get("enabled", True),
            "display_order": db.get("display_order"),
            "notes": db.get("notes"),
            "has_overrides": bool(db),
        })
    return result
