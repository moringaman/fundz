import pytest
import pandas as pd
import numpy as np
from app.services.indicators import IndicatorService, Signal


@pytest.fixture
def sample_price_data():
    np.random.seed(42)
    base_price = 40000
    prices = [base_price]
    for _ in range(100):
        prices.append(prices[-1] * (1 + np.random.uniform(-0.02, 0.02)))
    return pd.Series(prices)


@pytest.fixture
def sample_ohlcv_data():
    np.random.seed(42)
    base_price = 40000
    data = []
    for i in range(100):
        open_price = base_price * (1 + np.random.uniform(-0.01, 0.01))
        close_price = open_price * (1 + np.random.uniform(-0.02, 0.02))
        high_price = max(open_price, close_price) * (1 + np.random.uniform(0, 0.01))
        low_price = min(open_price, close_price) * (1 - np.random.uniform(0, 0.01))
        volume = np.random.uniform(100, 1000)
        data.append({
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": volume
        })
        base_price = close_price
    return pd.DataFrame(data)


class TestIndicatorService:
    def test_calculate_rsi(self, sample_price_data):
        service = IndicatorService()
        rsi = service.calculate_rsi(sample_price_data)
        
        assert len(rsi) == len(sample_price_data)
        assert rsi.max() <= 100
        assert rsi.min() >= 0

    def test_calculate_bollinger_bands(self, sample_price_data):
        service = IndicatorService()
        bb = service.calculate_bollinger_bands(sample_price_data)
        
        assert "upper" in bb.columns
        assert "middle" in bb.columns
        assert "lower" in bb.columns
        assert (bb["upper"] >= bb["middle"]).all()
        assert (bb["middle"] >= bb["lower"]).all()

    def test_calculate_sma(self, sample_price_data):
        service = IndicatorService()
        sma = service.calculate_sma(sample_price_data, 20)
        
        assert len(sma) == len(sample_price_data)
        assert sma.iloc[-1] > 0

    def test_calculate_ema(self, sample_price_data):
        service = IndicatorService()
        ema = service.calculate_ema(sample_price_data, 20)
        
        assert len(ema) == len(sample_price_data)

    def test_calculate_macd(self, sample_price_data):
        service = IndicatorService()
        macd = service.calculate_macd(sample_price_data)
        
        assert "macd" in macd.columns
        assert "signal" in macd.columns
        assert "histogram" in macd.columns

    def test_calculate_atr(self, sample_ohlcv_data):
        service = IndicatorService()
        atr = service.calculate_atr(
            sample_ohlcv_data["high"],
            sample_ohlcv_data["low"],
            sample_ohlcv_data["close"]
        )
        
        assert len(atr) == len(sample_ohlcv_data)
        assert (atr > 0).all()

    def test_generate_signal_oversold(self, sample_ohlcv_data):
        service = IndicatorService()
        
        oversold_data = sample_ohlcv_data.copy()
        oversold_data["close"] = 30000
        
        signal = service.generate_signal(oversold_data, {})
        
        assert signal.signal in [Signal.BUY, Signal.HOLD]

    def test_generate_signal_overbought(self, sample_ohlcv_data):
        service = IndicatorService()
        
        overbought_data = sample_ohlcv_data.copy()
        overbought_data["close"] = 50000
        
        signal = service.generate_signal(overbought_data, {})
        
        assert signal.signal in [Signal.SELL, Signal.HOLD]

    def test_calculate_all(self, sample_ohlcv_data):
        service = IndicatorService()
        indicators = service.calculate_all(sample_ohlcv_data)
        
        assert "rsi" in indicators
        assert "bb_upper" in indicators
        assert "bb_lower" in indicators
        assert "sma_20" in indicators
        assert "macd" in indicators
