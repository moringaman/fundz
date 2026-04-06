from fastapi import APIRouter, HTTPException
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from app.services.llm import llm_service

router = APIRouter(prefix="/llm", tags=["llm"])


class MarketDataRequest(BaseModel):
    symbol: str
    price: float
    price_change_percent: float
    high: float
    low: float
    volume: float
    rsi: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_middle: Optional[float] = None
    bb_lower: Optional[float] = None
    sma_20: Optional[float] = None
    sma_50: Optional[float] = None


class SignalRequest(BaseModel):
    indicators: Dict[str, float]
    current_price: float


class StrategyEvaluationRequest(BaseModel):
    strategy_config: Dict[str, Any]
    performance: Dict[str, Any]


class LLMResponse(BaseModel):
    action: str
    confidence: float
    reasoning: str
    risk_level: Optional[str] = "medium"


@router.get("/status")
async def get_llm_status():
    return {
        "provider": llm_service.provider,
        "model": llm_service.model,
        "initialized": llm_service._client is not None
    }


@router.post("/analyze-market", response_model=LLMResponse)
async def analyze_market(request: MarketDataRequest):
    market_data = request.model_dump()
    result = await llm_service.analyze_market(market_data)
    
    return LLMResponse(
        action=result.action,
        confidence=result.confidence,
        reasoning=result.reasoning,
        risk_level="medium"
    )


@router.post("/generate-signal", response_model=LLMResponse)
async def generate_signal(request: SignalRequest):
    price_data = {"current": request.current_price}
    result = await llm_service.generate_signal(request.indicators, price_data)
    
    return LLMResponse(
        action=result.action,
        confidence=result.confidence,
        reasoning=result.reasoning,
        risk_level="medium"
    )


@router.post("/evaluate-strategy", response_model=LLMResponse)
async def evaluate_strategy(request: StrategyEvaluationRequest):
    result = await llm_service.evaluate_strategy(
        request.strategy_config, 
        request.performance
    )
    
    return LLMResponse(
        action=result.action,
        confidence=result.confidence,
        reasoning=result.reasoning
    )
