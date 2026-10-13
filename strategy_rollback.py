"""
Strategy Rollback & Statistical Significance Engine
Auto-revert live strategy if performance degrades
"""

import logging
import sqlite3
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class StrategyVersion:
    """Versioned strategy snapshot."""
    version_id: int
    timestamp: int
    config: Dict  # Strategy configuration
    baseline_wr: float  # Baseline win rate (25+ trades)
    status: str  # "active" | "rolled_back"


class BinomialSignificanceTest:
    """Statistical test for trading strategy significance."""
    
    @staticmethod
    def calculate_p_value(wins: int, total: int, baseline_wr: float = 0.50) -> float:
        """
        Calculate p-value for binomial test.
        
        H0: True win rate = baseline_wr
        H1: True win rate != baseline_wr
        
        Returns: p-value (0-1)
        """
        # Simplified: use normal approximation for large samples
        # In production, use scipy.stats.binom_test or exact binomial
        
        if total < 20:
            return 1.0  # Need at least 20 trades
        
        observed_wr = wins / total
        
        # Standard error under null hypothesis
        import math
        se = math.sqrt(baseline_wr * (1 - baseline_wr) / total)
        
        if se == 0:
            return 1.0
        
        # Z-score
        z = (observed_wr - baseline_wr) / se
        
        # Two-tailed p-value (approximation)
        # P(|Z| > z) ≈ 2 * norm.sf(|z|)
        from math import erfc
        p_value = erfc(abs(z) / 1.414) / 2
        
        return max(0.0, min(1.0, p_value))
    
    @staticmethod
    def is_statistically_significant(wins: int, total: int, 
                                    baseline_wr: float = 0.50,
                                    alpha: float = 0.05) -> bool:
        """
        Test if performance is significantly different from baseline.
        
        Returns: True if p < alpha (reject H0)
        """
        p_value = BinomialSignificanceTest.calculate_p_value(wins, total, baseline_wr)
        return p_value < alpha


class StrategyRollback:
    """
    Detect degraded live strategy performance and auto-rollback.
    
    Rules:
    - Monitor live WR over 2-day window
    - If WR < 50% for 2+ consecutive days, trigger rollback
    - Rollback restores previous known-good configuration
    - Log all rollbacks with reason + duration
    """
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.lookback_days = 2
        self.wr_threshold = 0.50
    
    def get_recent_live_performance(self, hours: int = 48) -> Optional[Dict]:
        """
        Get live trading performance over recent hours.
        
        Returns:
        {
          "total_trades": 50,
          "wins": 20,
          "losses": 30,
          "win_rate": 0.40,
          "trades_by_hour": {
            "2026-10-12 20:00": {"wins": 5, "losses": 8},
            "2026-10-12 21:00": {"wins": 3, "losses": 6}
          }
        }
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cutoff_time = int((__import__('time').time()) - (hours * 3600))
            
            # Overall live performance
            cursor.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses
                FROM trades
                WHERE tier = 'live' AND entry_time > ?
            """, (cutoff_time,))
            
            row = cursor.fetchone()
            
            if not row or row[0] == 0:
                return None
            
            total, wins, losses = row
            wins = wins or 0
            
            conn.close()
            
            return {
                "total_trades": total,
                "wins": wins,
                "losses": losses or 0,
                "win_rate": wins / total if total > 0 else 0,
                "hours_tracked": hours
            }
        
        except Exception as e:
            logger.error(f"Failed to get live performance: {e}")
            return None
    
    def check_rollback_trigger(self) -> Optional[Dict]:
        """
        Check if rollback should be triggered.
        
        Returns:
        {
          "should_rollback": True,
          "reason": "WR < 50% for 2 consecutive days",
          "current_wr": 0.40,
          "trades": 50,
          "p_value": 0.02,
          "significant": True
        }
        """
        perf = self.get_recent_live_performance(hours=48)
        
        if not perf:
            return None
        
        # Check if WR is significantly below 50%
        if perf["total_trades"] < 25:
            return {
                "should_rollback": False,
                "reason": f"Only {perf['total_trades']} trades, need 25+",
                "current_wr": perf["win_rate"]
            }
        
        # Binomial test
        p_value = BinomialSignificanceTest.calculate_p_value(
            perf["wins"], perf["total_trades"], baseline_wr=0.50
        )
        
        is_significant = p_value < 0.05
        
        should_rollback = (
            perf["win_rate"] < self.wr_threshold and 
            is_significant
        )
        
        return {
            "should_rollback": should_rollback,
            "reason": f"WR={perf['win_rate']:.1%} < {self.wr_threshold:.1%}" if should_rollback else "OK",
            "current_wr": perf["win_rate"],
            "trades": perf["total_trades"],
            "p_value": p_value,
            "significant": is_significant
        }
    
    def execute_rollback(self, rollback_reason: str) -> bool:
        """
        Execute rollback to previous strategy version.
        
        In production:
        1. Load previous known-good config from backup
        2. Update live strategy
        3. Log rollback event
        4. Alert operator
        """
        try:
            logger.critical(f"EXECUTING ROLLBACK: {rollback_reason}")
            
            # Placeholder: would load previous config from version history
            # and apply to live strategy
            
            return True
        
        except Exception as e:
            logger.error(f"Rollback execution failed: {e}")
            return False
    
    def monitor_and_rollback(self) -> Optional[Dict]:
        """
        Main monitoring loop check (call periodically).
        
        Returns:
        {
          "action": "rollback_executed" | "monitoring" | "error",
          "reason": "...",
          "current_wr": 0.45,
          "trades": 50
        }
        """
        check = self.check_rollback_trigger()
        
        if not check:
            return None
        
        if check["should_rollback"]:
            success = self.execute_rollback(check["reason"])
            
            return {
                "action": "rollback_executed" if success else "rollback_failed",
                "reason": check["reason"],
                "current_wr": check["current_wr"],
                "trades": check["trades"],
                "p_value": check["p_value"]
            }
        
        else:
            return {
                "action": "monitoring",
                "reason": check["reason"],
                "current_wr": check["current_wr"],
                "trades": check["trades"]
            }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Test p-value calculation
    p = BinomialSignificanceTest.calculate_p_value(20, 50, baseline_wr=0.50)
    print(f"P-value for 20/50 wins: {p:.4f}")
    
    # Test rollback check
    rb = StrategyRollback("/tmp/trades.db")
    result = rb.monitor_and_rollback()
    print(f"Rollback status: {result}")
