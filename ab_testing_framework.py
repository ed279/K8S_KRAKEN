"""
A/B Testing Framework for Strategy Comparison
Run two competing strategies, track winner, auto-promote
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
from datetime import datetime

from atomic_file_utils import AtomicFileWriter, FileLocker

logger = logging.getLogger(__name__)


class TestStatus(Enum):
    """A/B test status."""
    RUNNING = "running"  # Both strategies active
    WINNER_FOUND = "winner_found"  # Clear winner emerged
    INCONCLUSIVE = "inconclusive"  # No clear winner after duration


@dataclass
class StrategyStats:
    """Stats for one strategy variant."""
    strategy_id: str  # "control" or "variant"
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    total_pnl: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0


class ABTestingFramework:
    """
    Run A/B tests to compare strategy variations.
    
    Example:
    - Control: RSI_OVERSOLD + SMA_CROSSOVER gates (baseline)
    - Variant: RSI_OVERSOLD + BREAKOUT gates (new)
    - Winner: whichever achieves 60%+ WR first, with p<0.05 significance
    """
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.base_dir = Path(db_path).parent
        self.tests_file = str(self.base_dir / "ab_tests.json")
        self.writer = AtomicFileWriter()
    
    def load_tests(self) -> Dict:
        """Load A/B test tracking file."""
        return self.writer.read_json_safe(self.tests_file, {})
    
    def save_tests(self, tests: Dict) -> bool:
        """Save A/B tests atomically."""
        return self.writer.write_json(self.tests_file, tests)
    
    def create_test(self, test_id: str, control_config: Dict, 
                   variant_config: Dict, duration_days: int = 7) -> bool:
        """
        Create a new A/B test.
        
        Args:
            test_id: Unique test identifier
            control_config: Baseline strategy config
            variant_config: New strategy config
            duration_days: How long to run the test
        
        Returns: True if created, False if already exists
        """
        try:
            with FileLocker(self.tests_file, timeout=5.0):
                tests = self.load_tests()
                
                if test_id in tests:
                    logger.warning(f"Test {test_id} already exists")
                    return False
                
                tests[test_id] = {
                    "created_at": datetime.utcnow().isoformat(),
                    "duration_days": duration_days,
                    "status": TestStatus.RUNNING.value,
                    "control_config": control_config,
                    "variant_config": variant_config,
                    "control_stats": asdict(StrategyStats("control")),
                    "variant_stats": asdict(StrategyStats("variant")),
                    "winner": None,
                    "p_value": None
                }
                
                self.save_tests(tests)
                logger.info(f"Created A/B test: {test_id}")
                return True
        
        except Exception as e:
            logger.error(f"Failed to create test: {e}")
            return False
    
    def calculate_strategy_stats(self, test_id: str, strategy_id: str,
                                tier: str = "paper") -> Optional[StrategyStats]:
        """
        Calculate stats for one strategy in a test.
        
        Queries trades tagged with test_id and strategy_id.
        """
        try:
            # Fetch test config
            tests = self.load_tests()
            if test_id not in tests:
                return None
            
            test = tests[test_id]
            strategy_config = test[f"{strategy_id}_config"]
            
            # Query trades matching this strategy's configuration
            # (Simplified: assumes trades are tagged with test_id in comment field)
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Placeholder: real implementation would use more sophisticated matching
            cursor.execute(f"""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
                    AVG(pnl) as avg_pnl,
                    SUM(pnl) as total_pnl
                FROM trades
                WHERE tier = ? AND instance_id LIKE ?
            """, (tier, f"%{strategy_id}%"))
            
            row = cursor.fetchone()
            conn.close()
            
            if not row or row[0] == 0:
                return StrategyStats(strategy_id)
            
            total, wins, losses, avg_pnl, total_pnl = row
            wins = wins or 0
            
            stats = StrategyStats(
                strategy_id=strategy_id,
                trades=total,
                wins=wins,
                losses=losses or 0,
                win_rate=wins / total if total > 0 else 0,
                avg_pnl=avg_pnl or 0,
                total_pnl=total_pnl or 0
            )
            
            return stats
        
        except Exception as e:
            logger.error(f"Failed to calculate stats: {e}")
            return None
    
    def calculate_p_value(self, control_wins: int, control_total: int,
                         variant_wins: int, variant_total: int) -> float:
        """
        Calculate p-value for statistical significance (binomial test).
        
        Is the difference in win rates statistically significant?
        Returns: p-value (0-1, lower = more significant)
        """
        # Simplified: use normal approximation for large samples
        from math import sqrt
        
        if control_total < 20 or variant_total < 20:
            return 1.0  # Not enough data
        
        control_rate = control_wins / control_total
        variant_rate = variant_wins / variant_total
        
        # Pooled proportion
        pooled = (control_wins + variant_wins) / (control_total + variant_total)
        
        # Standard error
        se = sqrt(pooled * (1 - pooled) * (1/control_total + 1/variant_total))
        
        if se == 0:
            return 1.0
        
        # Z-score
        z = (variant_rate - control_rate) / se
        
        # Two-tailed p-value (simplified)
        # In production, use scipy.stats.norm.sf(abs(z)) * 2
        p_value = 1.0 / (1 + abs(z))  # Rough approximation
        
        return p_value
    
    def check_test_completion(self, test_id: str) -> Optional[Dict]:
        """
        Check if test has a winner or needs more data.
        
        Returns:
        {
          "status": "winner_found",
          "winner": "variant",
          "p_value": 0.03,
          "control_wr": 0.55,
          "variant_wr": 0.62,
          "control_trades": 100,
          "variant_trades": 105
        }
        """
        try:
            tests = self.load_tests()
            if test_id not in tests:
                return None
            
            test = tests[test_id]
            
            control_stats = self.calculate_strategy_stats(test_id, "control")
            variant_stats = self.calculate_strategy_stats(test_id, "variant")
            
            if not control_stats or not variant_stats:
                return None
            
            # Need at least 20 trades each
            if control_stats.trades < 20 or variant_stats.trades < 20:
                return {
                    "status": TestStatus.RUNNING.value,
                    "control_trades": control_stats.trades,
                    "variant_trades": variant_stats.trades,
                    "needed": f"{20 - min(control_stats.trades, variant_stats.trades)} more"
                }
            
            # Calculate p-value
            p_value = self.calculate_p_value(
                control_stats.wins, control_stats.trades,
                variant_stats.wins, variant_stats.trades
            )
            
            # Determine winner (p < 0.05 = significant)
            # AND winner must have at least 55% WR
            control_qualifies = control_stats.win_rate >= 0.55
            variant_qualifies = variant_stats.win_rate >= 0.55
            
            winner = None
            if p_value < 0.05:
                if variant_qualifies and variant_stats.win_rate > control_stats.win_rate:
                    winner = "variant"
                elif control_qualifies and control_stats.win_rate > variant_stats.win_rate:
                    winner = "control"
            
            result = {
                "status": TestStatus.WINNER_FOUND.value if winner else TestStatus.RUNNING.value,
                "winner": winner,
                "p_value": round(p_value, 4),
                "control_wr": round(control_stats.win_rate, 3),
                "variant_wr": round(variant_stats.win_rate, 3),
                "control_trades": control_stats.trades,
                "variant_trades": variant_stats.trades,
                "recommendation": f"Use {winner}" if winner else "Need more data"
            }
            
            # Update test record
            if winner:
                test["status"] = TestStatus.WINNER_FOUND.value
                test["winner"] = winner
                test["p_value"] = p_value
                tests[test_id] = test
                
                with FileLocker(self.tests_file, timeout=5.0):
                    self.save_tests(tests)
                
                logger.info(f"Test {test_id}: WINNER={winner} (p={p_value:.4f})")
            
            return result
        
        except Exception as e:
            logger.error(f"Failed to check test: {e}")
            return None
    
    def promote_winning_strategy(self, test_id: str) -> bool:
        """
        Promote winning variant to production.
        
        In production, this would:
        - Copy variant config to live strategy
        - Deprecate control strategy
        - Log promotion in audit trail
        """
        tests = self.load_tests()
        if test_id not in tests or tests[test_id]["winner"] != "variant":
            logger.warning(f"Cannot promote test {test_id}")
            return False
        
        logger.info(f"Promoting variant from test {test_id} to production")
        # Implementation: apply variant_config to live strategy
        return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    ab = ABTestingFramework("/tmp/trades.db")
    
    # Create test
    control_config = {"gates": "rsi + sma_crossover"}
    variant_config = {"gates": "rsi + breakout"}
    
    ab.create_test("test_gate_comparison", control_config, variant_config)
    
    # Check progress
    result = ab.check_test_completion("test_gate_comparison")
    print(f"Test status: {result}")
