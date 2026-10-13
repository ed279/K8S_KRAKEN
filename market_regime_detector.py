"""
Market Regime Detection Engine
Identify bull/bear/sideways market conditions
"""

import logging
import sqlite3
from typing import Dict, Optional, Tuple
from enum import Enum
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    """Market regime classification."""
    BULL = "bull"  # Strong uptrend
    BEAR = "bear"  # Strong downtrend
    SIDEWAYS = "sideways"  # Range-bound / consolidation
    UNKNOWN = "unknown"  # Insufficient data


class MarketRegimeDetector:
    """
    Detect market regime using multiple indicators.
    
    Goals:
    - Bull: RSI consistently > 60, price above SMA(21)
    - Bear: RSI consistently < 40, price below SMA(21)
    - Sideways: RSI 40-60, price oscillates around SMA(21)
    
    Used for:
    - Route signals to regime-optimal strategies
    - Adjust position sizing (larger in bull/bear, smaller in sideways)
    - Skip trades during regime transitions
    """
    
    def __init__(self, db_path: str, lookback_hours: int = 168):
        self.db_path = db_path
        self.lookback_hours = lookback_hours  # 1 week default
    
    def get_price_trend(self, pair: str, tier: str = "paper") -> Optional[Dict]:
        """
        Calculate price trend metrics.
        
        Returns:
        {
          "current_price": 40000,
          "sma_9": 39500,
          "sma_21": 39000,
          "higher_than_sma21": True,
          "trend_strength": 0.65  # (price - sma21) / sma21
        }
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cutoff_time = int((__import__('time').time()) - (self.lookback_hours * 3600))
            
            # Get recent trades for trend
            cursor.execute("""
                SELECT 
                    entry_price, exit_time
                FROM trades
                WHERE pair = ? AND tier = ? AND entry_time > ?
                ORDER BY entry_time DESC
                LIMIT 100
            """, (pair, tier, cutoff_time))
            
            trades = cursor.fetchall()
            conn.close()
            
            if len(trades) < 5:
                return None
            
            # Calculate SMAs from entry prices
            prices = [t[0] for t in trades]
            
            # SMA(9)
            sma_9 = sum(prices[:9]) / min(9, len(prices))
            # SMA(21)
            sma_21 = sum(prices[:21]) / min(21, len(prices))
            
            current_price = prices[0]  # Most recent entry price
            
            # Trend strength: how far above/below SMA(21)
            trend_strength = (current_price - sma_21) / sma_21 if sma_21 > 0 else 0
            
            return {
                "current_price": current_price,
                "sma_9": sma_9,
                "sma_21": sma_21,
                "higher_than_sma21": current_price > sma_21,
                "trend_strength": trend_strength,
                "sample_size": len(trades)
            }
        
        except Exception as e:
            logger.error(f"Failed to get price trend for {pair}: {e}")
            return None
    
    def get_rsi_regime(self, pair: str, tier: str = "paper") -> Optional[Dict]:
        """
        Analyze RSI values over lookback period.
        
        Returns:
        {
          "current_rsi": 65,
          "avg_rsi": 58,
          "rsi_above_60_pct": 0.65,  # % of readings > 60
          "rsi_below_40_pct": 0.10,
          "volatility": 8.5  # std dev of RSI values
        }
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cutoff_time = int((__import__('time').time()) - (self.lookback_hours * 3600))
            
            # Get RSI readings (using signal_confidence as proxy for now)
            # In real implementation, would fetch actual RSI indicators
            cursor.execute("""
                SELECT signal_confidence
                FROM trades
                WHERE pair = ? AND tier = ? AND entry_time > ?
                AND signal_type IN ('RSI_OVERSOLD', 'RSI_OVERBOUGHT')
                ORDER BY entry_time DESC
                LIMIT 100
            """, (pair, tier, cutoff_time))
            
            rsi_readings = [row[0] * 100 for row in cursor.fetchall()]
            conn.close()
            
            if len(rsi_readings) < 5:
                return None
            
            avg_rsi = sum(rsi_readings) / len(rsi_readings)
            above_60 = sum(1 for r in rsi_readings if r > 60) / len(rsi_readings)
            below_40 = sum(1 for r in rsi_readings if r < 40) / len(rsi_readings)
            
            # Volatility as std dev
            variance = sum((r - avg_rsi) ** 2 for r in rsi_readings) / len(rsi_readings)
            volatility = variance ** 0.5
            
            return {
                "current_rsi": rsi_readings[0],
                "avg_rsi": avg_rsi,
                "rsi_above_60_pct": above_60,
                "rsi_below_40_pct": below_40,
                "volatility": volatility,
                "sample_size": len(rsi_readings)
            }
        
        except Exception as e:
            logger.error(f"Failed to get RSI regime for {pair}: {e}")
            return None
    
    def detect_regime(self, pair: str, tier: str = "paper") -> Tuple[MarketRegime, float]:
        """
        Detect market regime for a pair.
        
        Returns: (regime, confidence)
        - confidence: 0-1 score of how confident we are in this regime
        """
        price_trend = self.get_price_trend(pair, tier)
        rsi_regime = self.get_rsi_regime(pair, tier)
        
        if not price_trend or not rsi_regime:
            return MarketRegime.UNKNOWN, 0.0
        
        confidence = 0.0
        regime = MarketRegime.UNKNOWN
        
        # Bull regime: price > SMA(21) AND RSI > 60%+ of the time
        if price_trend["higher_than_sma21"] and rsi_regime["rsi_above_60_pct"] > 0.60:
            regime = MarketRegime.BULL
            # Confidence increases with trend strength and RSI consistency
            confidence = (price_trend["trend_strength"] + rsi_regime["rsi_above_60_pct"]) / 2
            confidence = min(1.0, abs(confidence))
        
        # Bear regime: price < SMA(21) AND RSI < 40%+ of the time
        elif not price_trend["higher_than_sma21"] and rsi_regime["rsi_below_40_pct"] > 0.60:
            regime = MarketRegime.BEAR
            # Confidence increases with downtrend strength and RSI consistency
            confidence = (-price_trend["trend_strength"] + rsi_regime["rsi_below_40_pct"]) / 2
            confidence = min(1.0, abs(confidence))
        
        # Sideways: RSI in middle zone, price around SMA(21)
        elif (abs(price_trend["trend_strength"]) < 0.05 and 
              0.30 < rsi_regime["rsi_above_60_pct"] < 0.70):
            regime = MarketRegime.SIDEWAYS
            # Confidence based on RSI oscillation around 50
            mid_rsi_pct = sum(1 for r in [rsi_regime["current_rsi"]] 
                             if 40 < r < 60) / 1
            confidence = 0.50 + (0.50 * (1 - rsi_regime["volatility"] / 50))
        
        return regime, max(0.0, confidence)
    
    def should_skip_trade_for_regime(self, regime: MarketRegime, 
                                    confidence: float, pair: str) -> bool:
        """
        Decide if we should skip trading during regime transitions.
        
        Rules:
        - Skip if confidence < 0.40 (uncertain regime)
        - Skip if regime is UNKNOWN
        - Otherwise: allow trade (regime-adjusted sizing handled elsewhere)
        """
        if regime == MarketRegime.UNKNOWN or confidence < 0.40:
            logger.warning(f"Skipping {pair}: regime={regime.value}, confidence={confidence:.1%}")
            return True
        
        return False
    
    def get_regime_adjusted_gates(self, regime: MarketRegime) -> Dict:
        """
        Get signal gates adjusted for market regime.
        
        Bull: more aggressive (lower gates)
        Bear: defensive (higher gates)
        Sideways: balanced
        """
        if regime == MarketRegime.BULL:
            return {
                "rsi_gate": 0.55,  # Lower for bull momentum
                "crossover_gate": 0.50,
                "breakout_gate": 0.48,
                "position_size_mult": 1.20  # 20% larger positions
            }
        
        elif regime == MarketRegime.BEAR:
            return {
                "rsi_gate": 0.60,  # Higher for bear caution
                "crossover_gate": 0.58,
                "breakout_gate": 0.55,
                "position_size_mult": 0.80  # 20% smaller positions
            }
        
        else:  # SIDEWAYS
            return {
                "rsi_gate": 0.58,  # Balanced
                "crossover_gate": 0.55,
                "breakout_gate": 0.52,
                "position_size_mult": 1.00  # Normal sizing
            }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    detector = MarketRegimeDetector("/tmp/trades.db")
    
    regime, confidence = detector.detect_regime("BTC/USD", "paper")
    print(f"Regime: {regime.value} @ {confidence:.1%}")
    
    skip = detector.should_skip_trade_for_regime(regime, confidence, "BTC/USD")
    print(f"Skip trading: {skip}")
    
    gates = detector.get_regime_adjusted_gates(regime)
    print(f"Adjusted gates: {gates}")
