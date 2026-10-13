"""
Signal Correlation & Confidence History Engine
- Per-signal-type win rate tracking
- Correlation thresholds (avoid redundant signals)
- Confidence trend detection (6h moving average)
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from collections import deque

from atomic_file_utils import AtomicFileWriter, FileLocker

logger = logging.getLogger(__name__)


class SignalCorrelationEngine:
    """
    Analyze signal types and their relationships.
    
    Goals:
    - RSI_OVERSOLD + SMA_CROSSOVER_BULLISH = stronger signal (0.7 correlation)
    - RSI_OVERSOLD + RSI_DIVERGENCE = weak (conflicting indicators)
    - Avoid firing redundant signals (wait for confirmation)
    """
    
    # Signal correlation matrix (higher = more likely to appear together)
    SIGNAL_CORRELATIONS = {
        "RSI_OVERSOLD": {
            "SMA_CROSSOVER_BULLISH": 0.70,  # Strong confirmation
            "BREAKOUT_HIGH": 0.60,
            "RSI_DIVERGENCE": 0.65,
            "VOLATILITY_SQUEEZE": 0.50
        },
        "RSI_OVERBOUGHT": {
            "SMA_CROSSOVER_BEARISH": 0.70,
            "BREAKOUT_LOW": 0.60,
            "RSI_DIVERGENCE": 0.65,
        },
        "SMA_CROSSOVER_BULLISH": {
            "BREAKOUT_HIGH": 0.55,
            "RSI_OVERSOLD": 0.70
        },
        "SMA_CROSSOVER_BEARISH": {
            "BREAKOUT_LOW": 0.55,
            "RSI_OVERBOUGHT": 0.70
        }
    }
    
    def __init__(self, db_path: str, history_window_minutes: int = 360):
        self.db_path = db_path
        self.base_dir = Path(db_path).parent
        self.history_window = history_window_minutes  # 6 hours default
        self.correlation_file = str(self.base_dir / "signal_correlation.json")
        self.writer = AtomicFileWriter()
        self.confidence_history = deque(maxlen=100)  # In-memory for current session
    
    def get_signal_win_rate(self, signal_type: str, pair: str = None, 
                           tier: str = "shadow", min_trades: int = 10) -> Optional[float]:
        """
        Get historical win rate for a signal type.
        
        Returns: win rate (0-1) or None if insufficient data
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            where = "WHERE signal_type = ? AND tier = ?"
            params = [signal_type, tier]
            
            if pair:
                where += " AND pair = ?"
                params.append(pair)
            
            cursor.execute(f"""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
                FROM trades {where}
            """, params)
            
            row = cursor.fetchone()
            conn.close()
            
            if not row or row[0] < min_trades:
                return None
            
            total, wins = row
            return wins / total if total > 0 else 0.0
        
        except Exception as e:
            logger.error(f"Failed to get win rate for {signal_type}: {e}")
            return None
    
    def get_correlated_signals(self, signal_type: str) -> Dict[str, float]:
        """Get correlated signal types and confidence boosts."""
        return self.SIGNAL_CORRELATIONS.get(signal_type, {})
    
    def calculate_signal_confidence_boost(self, primary_signal: str,
                                        recent_signals: List[str]) -> float:
        """
        Calculate confidence boost based on recent signal correlation.
        
        If primary signal type recently appeared with correlated types,
        boost the confidence.
        
        Returns: boost amount (0-0.2)
        """
        correlations = self.get_correlated_signals(primary_signal)
        if not correlations:
            return 0.0
        
        boost = 0.0
        for signal in recent_signals:
            if signal in correlations:
                boost += correlations[signal] * 0.05  # Max 0.05 per correlation
        
        return min(0.20, boost)  # Cap at 0.2
    
    def record_confidence_observation(self, signal_type: str, confidence: float,
                                     result: bool, timestamp: int = None):
        """
        Record a confidence observation for trend detection.
        
        Used to detect if confidence is improving or degrading over time.
        """
        if timestamp is None:
            timestamp = int(__import__('time').time())
        
        self.confidence_history.append({
            "signal_type": signal_type,
            "confidence": confidence,
            "result": result,
            "timestamp": timestamp
        })
    
    def get_confidence_trend(self, signal_type: str) -> Optional[Dict]:
        """
        Get confidence trend for a signal type (6h moving average).
        
        Returns:
        {
          "trend": "improving" | "degrading" | "stable",
          "current_ma": 0.65,
          "previous_ma": 0.60,
          "observations": 45
        }
        """
        if not self.confidence_history:
            return None
        
        now = __import__('time').time()
        cutoff = now - (self.history_window * 60)
        
        # Filter by signal type and time window
        recent = [
            obs for obs in self.confidence_history
            if obs["signal_type"] == signal_type and obs["timestamp"] >= cutoff
        ]
        
        if len(recent) < 5:
            return None
        
        # Split into two halves
        mid = len(recent) // 2
        first_half = recent[:mid]
        second_half = recent[mid:]
        
        prev_ma = sum(o["confidence"] for o in first_half) / len(first_half)
        current_ma = sum(o["confidence"] for o in second_half) / len(second_half)
        
        # Determine trend
        delta = current_ma - prev_ma
        if delta > 0.05:
            trend = "improving"
        elif delta < -0.05:
            trend = "degrading"
        else:
            trend = "stable"
        
        return {
            "trend": trend,
            "current_ma": round(current_ma, 3),
            "previous_ma": round(prev_ma, 3),
            "delta": round(delta, 3),
            "observations": len(recent)
        }
    
    def should_fire_signal(self, signal_type: str, confidence: float,
                          pair: str, recent_signals: List[str]) -> bool:
        """
        Gate: should we fire this signal or wait for confirmation?
        
        Rules:
        - Base confidence must be > 0.50
        - RSI signals need > 0.55 confidence
        - Breakouts need correlated signals (SMA crossover, etc.)
        - Signal type must have > 50% historical win rate
        """
        # Minimum confidence gate
        if confidence < 0.50:
            return False
        
        # Signal-type-specific gates
        if signal_type in ["RSI_OVERSOLD", "RSI_OVERBOUGHT"]:
            if confidence < 0.55:
                return False
        
        if signal_type in ["BREAKOUT_HIGH", "BREAKOUT_LOW"]:
            # Breakouts need confirmation from other signal types
            correlations = self.get_correlated_signals(signal_type)
            has_confirmation = any(s in recent_signals for s in correlations.keys())
            if not has_confirmation:
                return False
        
        # Historical win rate gate
        win_rate = self.get_signal_win_rate(signal_type, pair)
        if win_rate and win_rate < 0.50:
            logger.warning(f"{signal_type} on {pair} has poor win rate: {win_rate:.1%}")
            return False
        
        return True
    
    def load_correlation_data(self) -> Dict:
        """Load correlation tracking file."""
        return self.writer.read_json_safe(self.correlation_file, {})
    
    def save_correlation_data(self, data: Dict) -> bool:
        """Save correlation data atomically."""
        return self.writer.write_json(self.correlation_file, data)
    
    def analyze_signal_correlations(self, tier: str = "shadow") -> Dict:
        """
        Analyze actual correlations in trade history.
        
        Returns correlation matrix based on real data.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get all signal types in tier
            cursor.execute("""
                SELECT DISTINCT signal_type FROM trades 
                WHERE tier = ? AND signal_type != ''
            """, (tier,))
            
            signal_types = {row[0] for row in cursor.fetchall()}
            
            # Calculate win rate for each
            win_rates = {}
            for sig_type in signal_types:
                cursor.execute("""
                    SELECT 
                        COUNT(*) as total,
                        SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
                    FROM trades WHERE signal_type = ? AND tier = ?
                """, (sig_type, tier))
                
                row = cursor.fetchone()
                if row and row[0] >= 10:
                    win_rates[sig_type] = row[1] / row[0]
            
            conn.close()
            
            try:
                with FileLocker(self.correlation_file, timeout=5.0):
                    data = self.load_correlation_data()
                    data["signal_type_win_rates"] = win_rates
                    data["analysis_date"] = datetime.utcnow().isoformat()
                    data["tier"] = tier
                    
                    self.save_correlation_data(data)
                    
                    return {
                        "win_rates": win_rates,
                        "signal_types_analyzed": len(win_rates),
                        "best_signal": max(win_rates.items(), key=lambda x: x[1])[0] if win_rates else None
                    }
            
            except TimeoutError:
                logger.error("Could not acquire lock for correlation file")
                return {"error": "timeout"}
        
        except Exception as e:
            logger.error(f"Correlation analysis failed: {e}")
            return {"error": str(e)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    engine = SignalCorrelationEngine("/tmp/trades.db")
    
    # Test signal gating
    fire = engine.should_fire_signal("RSI_OVERSOLD", 0.60, "BTC/USD", ["SMA_CROSSOVER_BULLISH"])
    print(f"Fire RSI_OVERSOLD @ 0.60: {fire}")
    
    # Analyze correlations
    result = engine.analyze_signal_correlations()
    print(f"Correlation analysis: {result}")
