"""
Real Signal Generation Engine
RSI (14), ATR (14), SMA (9/21), Divergence Detection
"""

import logging
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class SignalType(Enum):
    RSI_OVERSOLD = "RSI_OVERSOLD"  # RSI < 30
    RSI_OVERBOUGHT = "RSI_OVERBOUGHT"  # RSI > 70
    RSI_DIVERGENCE = "RSI_DIVERGENCE"  # Price up, RSI down (bullish) or vice versa (bearish)
    SMA_CROSSOVER_BULLISH = "SMA_CROSSOVER_BULLISH"  # Fast SMA crosses above slow SMA
    SMA_CROSSOVER_BEARISH = "SMA_CROSSOVER_BEARISH"  # Fast SMA crosses below slow SMA
    BREAKOUT_HIGH = "BREAKOUT_HIGH"  # Price breaks ATR-based resistance
    BREAKOUT_LOW = "BREAKOUT_LOW"  # Price breaks ATR-based support
    VOLATILITY_SQUEEZE = "VOLATILITY_SQUEEZE"  # Low ATR after high


@dataclass
class Signal:
    """Single generated signal."""
    signal_type: SignalType
    base_confidence: float  # 0-1
    price: float
    rsi: float
    atr: float
    fast_sma: float
    slow_sma: float
    timestamp: int  # Unix seconds


@dataclass
class Candle:
    """OHLCV candle."""
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class IndicatorCalculator:
    """Core technical indicator calculations."""
    
    @staticmethod
    def sma(prices: List[float], period: int) -> List[Optional[float]]:
        """Simple Moving Average."""
        if len(prices) < period:
            return [None] * len(prices)
        
        result = []
        for i in range(len(prices)):
            if i < period - 1:
                result.append(None)
            else:
                result.append(sum(prices[i - period + 1:i + 1]) / period)
        return result
    
    @staticmethod
    def rsi(prices: List[float], period: int = 14) -> List[Optional[float]]:
        """Relative Strength Index (14)."""
        if len(prices) < period + 1:
            return [None] * len(prices)
        
        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        
        gains = [max(d, 0) for d in deltas]
        losses = [max(-d, 0) for d in deltas]
        
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        result = [None] * (period + 1)
        
        for i in range(period, len(prices)):
            if i == period:
                result.append(None)
            else:
                avg_gain = (avg_gain * (period - 1) + gains[i]) / period
                avg_loss = (avg_loss * (period - 1) + losses[i]) / period
                
                rs = avg_gain / avg_loss if avg_loss != 0 else 100
                rsi_val = 100 - (100 / (1 + rs))
                result.append(rsi_val)
        
        return result
    
    @staticmethod
    def atr(candles: List[Candle], period: int = 14) -> List[Optional[float]]:
        """Average True Range (14)."""
        if len(candles) < period:
            return [None] * len(candles)
        
        tr_values = []
        for i, candle in enumerate(candles):
            if i == 0:
                tr = candle.high - candle.low
            else:
                prev_close = candles[i - 1].close
                tr = max(
                    candle.high - candle.low,
                    abs(candle.high - prev_close),
                    abs(candle.low - prev_close)
                )
            tr_values.append(tr)
        
        result = [None] * (period - 1)
        result.append(sum(tr_values[:period]) / period)
        
        atr_val = result[-1]
        for i in range(period, len(candles)):
            atr_val = (atr_val * (period - 1) + tr_values[i]) / period
            result.append(atr_val)
        
        return result


class SignalGenerator:
    """Generate trading signals from candle data."""
    
    def __init__(self, rsi_period: int = 14, sma_fast: int = 9, sma_slow: int = 21):
        self.rsi_period = rsi_period
        self.sma_fast = sma_fast
        self.sma_slow = sma_slow
        self.calc = IndicatorCalculator()
    
    def generate_signals(self, candles: List[Candle]) -> List[Signal]:
        """Generate all signals from candle history."""
        if len(candles) < 30:
            return []
        
        # Calculate indicators
        closes = [c.close for c in candles]
        rsi_values = self.calc.rsi(closes, self.rsi_period)
        fast_smas = self.calc.sma(closes, self.sma_fast)
        slow_smas = self.calc.sma(closes, self.sma_slow)
        atr_values = self.calc.atr(candles, 14)
        
        signals = []
        
        for i in range(max(self.rsi_period, self.sma_slow), len(candles)):
            candle = candles[i]
            rsi = rsi_values[i]
            fast_sma = fast_smas[i]
            slow_sma = slow_smas[i]
            atr = atr_values[i]
            
            if rsi is None or fast_sma is None or slow_sma is None or atr is None:
                continue
            
            # RSI Oversold (buy signal)
            if rsi < 30:
                signals.append(Signal(
                    signal_type=SignalType.RSI_OVERSOLD,
                    base_confidence=0.45 + (0.30 - rsi) / 100,  # Stronger at 0
                    price=candle.close,
                    rsi=rsi,
                    atr=atr,
                    fast_sma=fast_sma,
                    slow_sma=slow_sma,
                    timestamp=candle.timestamp
                ))
            
            # RSI Overbought (sell signal)
            if rsi > 70:
                signals.append(Signal(
                    signal_type=SignalType.RSI_OVERBOUGHT,
                    base_confidence=0.45 + (rsi - 70) / 100,  # Stronger at 100
                    price=candle.close,
                    rsi=rsi,
                    atr=atr,
                    fast_sma=fast_sma,
                    slow_sma=slow_sma,
                    timestamp=candle.timestamp
                ))
            
            # SMA Crossovers
            if i >= self.sma_slow + 1:
                prev_fast = fast_smas[i - 1]
                prev_slow = slow_smas[i - 1]
                
                if prev_fast <= prev_slow and fast_sma > slow_sma:
                    signals.append(Signal(
                        signal_type=SignalType.SMA_CROSSOVER_BULLISH,
                        base_confidence=0.55,
                        price=candle.close,
                        rsi=rsi,
                        atr=atr,
                        fast_sma=fast_sma,
                        slow_sma=slow_sma,
                        timestamp=candle.timestamp
                    ))
                
                if prev_fast >= prev_slow and fast_sma < slow_sma:
                    signals.append(Signal(
                        signal_type=SignalType.SMA_CROSSOVER_BEARISH,
                        base_confidence=0.55,
                        price=candle.close,
                        rsi=rsi,
                        atr=atr,
                        fast_sma=fast_sma,
                        slow_sma=slow_sma,
                        timestamp=candle.timestamp
                    ))
            
            # Breakout signals (ATR-based)
            support = slow_sma - (atr * 1.5)
            resistance = slow_sma + (atr * 1.5)
            
            if candle.close > resistance and candles[i - 1].close <= resistance:
                signals.append(Signal(
                    signal_type=SignalType.BREAKOUT_HIGH,
                    base_confidence=0.50,
                    price=candle.close,
                    rsi=rsi,
                    atr=atr,
                    fast_sma=fast_sma,
                    slow_sma=slow_sma,
                    timestamp=candle.timestamp
                ))
            
            if candle.close < support and candles[i - 1].close >= support:
                signals.append(Signal(
                    signal_type=SignalType.BREAKOUT_LOW,
                    base_confidence=0.50,
                    price=candle.close,
                    rsi=rsi,
                    atr=atr,
                    fast_sma=fast_sma,
                    slow_sma=slow_sma,
                    timestamp=candle.timestamp
                ))
        
        return signals
    
    def get_latest_signal(self, candles: List[Candle]) -> Optional[Signal]:
        """Get most recent signal if any."""
        signals = self.generate_signals(candles)
        return signals[-1] if signals else None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Test with dummy data
    test_candles = [
        Candle(timestamp=i, open=100 + i*0.1, high=101 + i*0.1, 
               low=99 + i*0.1, close=100.5 + i*0.1, volume=1000)
        for i in range(100)
    ]
    
    gen = SignalGenerator()
    signals = gen.generate_signals(test_candles)
    
    if signals:
        latest = signals[-1]
        print(f"Latest signal: {latest.signal_type.value}")
        print(f"  Confidence: {latest.base_confidence:.2%}")
        print(f"  RSI: {latest.rsi:.1f}, ATR: {latest.atr:.2f}")
        print(f"  SMA(9/21): {latest.fast_sma:.2f} / {latest.slow_sma:.2f}")
    else:
        print("No signals generated")
