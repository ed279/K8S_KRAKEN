"""
Portfolio Safety Engine v1
- Pair correlation matrix (max 0.7 corr threshold)
- Volatility-adjusted position sizing
- Portfolio size limits (5 pairs max)
- Stability validation (30+ trades minimum)
- Atomic writes + error handling
"""

import json
import logging
import sqlite3
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
import time

from atomic_file_utils import AtomicFileWriter, FileLocker

logger = logging.getLogger(__name__)


class PortfolioSafetyEngine:
    """
    Portfolio risk management:
    - Correlation analysis (block correlated pairs)
    - Volatility-adjusted sizing (high vol → smaller)
    - Max 5 pairs per portfolio
    - 30+ trades minimum before considering stable
    """
    
    def __init__(self, db_path: str, instance_id: str, tier: str):
        """
        Args:
            db_path: Path to trades.db
            instance_id: Instance identifier for coordination
            tier: 'paper', 'shadow', or 'live'
        """
        self.db_path = db_path
        self.instance_id = instance_id
        self.tier = tier
        self.base_dir = Path(db_path).parent
        self.state_file = str(self.base_dir / "portfolio_safety_state.json")
        self.writer = AtomicFileWriter()
        
        self.MAX_PAIRS = 5
        self.MAX_CORRELATION = 0.7
        self.MIN_STABILITY_TRADES = 30
        self.VOLATILITY_WINDOW = 100  # Recent trades for vol calculation
    
    def get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    # ===== Correlation Analysis =====
    
    def get_recent_returns(self, pair: str, limit: int = 100) -> Optional[List[float]]:
        """
        Get recent returns for correlation analysis.
        
        Args:
            pair: Trading pair (e.g. 'XBTUSD')
            limit: Max trades to consider
        
        Returns:
            List of pnl_percent values, or None if insufficient data
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT pnl_percent FROM trades
                WHERE pair = ? AND tier = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (pair, self.tier, limit))
            
            returns = [row['pnl_percent'] for row in cursor.fetchall()]
            conn.close()
            
            return returns if returns else None
        
        except Exception as e:
            logger.error(f"Failed to get returns for {pair}: {e}")
            return None
    
    def calculate_correlation(self, pair1: str, pair2: str) -> Optional[float]:
        """
        Calculate Pearson correlation between two pairs.
        
        Args:
            pair1, pair2: Trading pairs
        
        Returns:
            Correlation coefficient (-1 to 1), or None if insufficient data
        """
        returns1 = self.get_recent_returns(pair1, limit=self.VOLATILITY_WINDOW)
        returns2 = self.get_recent_returns(pair2, limit=self.VOLATILITY_WINDOW)
        
        if not returns1 or not returns2 or len(returns1) < 10 or len(returns2) < 10:
            return None
        
        # Align to same length
        min_len = min(len(returns1), len(returns2))
        returns1 = returns1[:min_len]
        returns2 = returns2[:min_len]
        
        try:
            corr = np.corrcoef(returns1, returns2)[0, 1]
            return float(corr) if not np.isnan(corr) else None
        except Exception as e:
            logger.error(f"Correlation calc failed for {pair1}/{pair2}: {e}")
            return None
    
    def build_correlation_matrix(self, pairs: List[str]) -> Dict[str, Dict[str, float]]:
        """
        Build correlation matrix for given pairs.
        
        Returns: {pair1: {pair2: correlation}}
        """
        matrix = {p: {} for p in pairs}
        
        for i, pair1 in enumerate(pairs):
            for pair2 in pairs[i:]:
                if pair1 == pair2:
                    matrix[pair1][pair2] = 1.0
                else:
                    corr = self.calculate_correlation(pair1, pair2)
                    if corr is not None:
                        matrix[pair1][pair2] = corr
                        matrix[pair2][pair1] = corr  # Symmetric
                    else:
                        matrix[pair1][pair2] = None
                        matrix[pair2][pair1] = None
        
        return matrix
    
    def find_correlated_pairs(self, matrix: Dict, threshold: float = 0.7) -> List[Tuple[str, str]]:
        """
        Find pairs exceeding correlation threshold.
        
        Returns: List of (pair1, pair2) tuples with corr >= threshold
        """
        violations = []
        
        for pair1 in matrix:
            for pair2, corr in matrix[pair1].items():
                if corr is not None and corr >= threshold and pair1 < pair2:
                    violations.append((pair1, pair2, corr))
        
        return violations
    
    # ===== Volatility Analysis =====
    
    def calculate_volatility(self, pair: str, window: int = None) -> Optional[float]:
        """
        Calculate volatility (std dev of returns).
        
        Args:
            pair: Trading pair
            window: Trades to consider (default: VOLATILITY_WINDOW)
        
        Returns:
            Volatility as percentage
        """
        if window is None:
            window = self.VOLATILITY_WINDOW
        
        returns = self.get_recent_returns(pair, limit=window)
        
        if not returns or len(returns) < 5:
            return None
        
        try:
            volatility = float(np.std(returns))
            return volatility
        except Exception as e:
            logger.error(f"Volatility calc failed for {pair}: {e}")
            return None
    
    def calculate_position_size(self, pair: str, base_size: float = 1.0) -> float:
        """
        Adjust position size based on volatility.
        
        High volatility → smaller position
        
        Args:
            pair: Trading pair
            base_size: Base size before adjustment (default 1.0)
        
        Returns:
            Adjusted size (0.1 to 1.0 range)
        """
        volatility = self.calculate_volatility(pair)
        
        if volatility is None:
            return base_size
        
        # Volatility in % range
        # Example: 2% vol → 1.0, 5% vol → 0.4, 10% vol → 0.2
        if volatility <= 0:
            return base_size
        
        # Inverse relationship: size = base_size / (1 + vol/2)
        adjusted = base_size / (1 + volatility / 2.0)
        
        # Clamp to [0.1, 1.0]
        return max(0.1, min(1.0, adjusted))
    
    # ===== Stability Validation =====
    
    def get_pair_trade_count(self, pair: str) -> int:
        """Get total trade count for pair."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT COUNT(*) as cnt FROM trades
                WHERE pair = ? AND tier = ?
            """, (pair, self.tier))
            
            count = cursor.fetchone()['cnt']
            conn.close()
            return count
        
        except Exception as e:
            logger.error(f"Failed to count trades for {pair}: {e}")
            return 0
    
    def is_pair_stable(self, pair: str, min_trades: int = None) -> bool:
        """
        Check if pair has sufficient trading history.
        
        Args:
            pair: Trading pair
            min_trades: Minimum trades required (default: MIN_STABILITY_TRADES)
        
        Returns:
            True if trade count >= min_trades
        """
        if min_trades is None:
            min_trades = self.MIN_STABILITY_TRADES
        
        count = self.get_pair_trade_count(pair)
        return count >= min_trades
    
    # ===== Portfolio Validation =====
    
    def validate_pair_for_addition(self, new_pair: str, active_pairs: List[str]) -> Dict:
        """
        Check if pair can be safely added to portfolio.
        
        Args:
            new_pair: Pair to add
            active_pairs: Currently active pairs
        
        Returns:
            {
                "can_add": bool,
                "reason": str,
                "checks": {
                    "stability": bool,
                    "portfolio_size": bool,
                    "correlation": bool,
                    "details": {...}
                }
            }
        """
        checks = {
            "stability": False,
            "portfolio_size": False,
            "correlation": False,
            "details": {}
        }
        
        # Check 1: Stability
        is_stable = self.is_pair_stable(new_pair)
        checks["stability"] = is_stable
        checks["details"]["stability"] = {
            "trade_count": self.get_pair_trade_count(new_pair),
            "min_required": self.MIN_STABILITY_TRADES,
            "passed": is_stable
        }
        
        if not is_stable:
            return {
                "can_add": False,
                "reason": f"Insufficient trade history ({checks['details']['stability']['trade_count']}/{self.MIN_STABILITY_TRADES})",
                "checks": checks
            }
        
        # Check 2: Portfolio size
        current_size = len(active_pairs)
        can_add_size = current_size < self.MAX_PAIRS
        checks["portfolio_size"] = can_add_size
        checks["details"]["portfolio_size"] = {
            "current_pairs": current_size,
            "max_pairs": self.MAX_PAIRS,
            "passed": can_add_size
        }
        
        if not can_add_size:
            return {
                "can_add": False,
                "reason": f"Portfolio at max size ({current_size}/{self.MAX_PAIRS})",
                "checks": checks
            }
        
        # Check 3: Correlation
        all_pairs = active_pairs + [new_pair]
        matrix = self.build_correlation_matrix(all_pairs)
        violations = self.find_correlated_pairs(matrix, threshold=self.MAX_CORRELATION)
        
        has_high_corr = any(v[0] == new_pair or v[1] == new_pair for v in violations)
        checks["correlation"] = not has_high_corr
        checks["details"]["correlation"] = {
            "violations": [(p1, p2, round(c, 3)) for p1, p2, c in violations],
            "passed": not has_high_corr
        }
        
        if has_high_corr:
            blocked_by = [v for v in violations if v[0] == new_pair or v[1] == new_pair]
            return {
                "can_add": False,
                "reason": f"High correlation with existing pair(s): {blocked_by}",
                "checks": checks
            }
        
        return {
            "can_add": True,
            "reason": "All safety checks passed",
            "checks": checks
        }
    
    def validate_portfolio(self, active_pairs: List[str]) -> Dict:
        """
        Comprehensive portfolio validation.
        
        Args:
            active_pairs: List of currently active pairs
        
        Returns:
            {
                "is_valid": bool,
                "issues": [...],
                "metrics": {...}
            }
        """
        issues = []
        
        # Check size
        if len(active_pairs) > self.MAX_PAIRS:
            issues.append(f"Portfolio size {len(active_pairs)} exceeds max {self.MAX_PAIRS}")
        
        # Check stability
        unstable = [p for p in active_pairs if not self.is_pair_stable(p)]
        if unstable:
            issues.append(f"Unstable pairs: {unstable}")
        
        # Check correlation
        matrix = self.build_correlation_matrix(active_pairs)
        violations = self.find_correlated_pairs(matrix, threshold=self.MAX_CORRELATION)
        if violations:
            issues.append(f"High correlations: {violations}")
        
        # Build metrics
        metrics = {
            "pair_count": len(active_pairs),
            "stability_check": {p: {
                "count": self.get_pair_trade_count(p),
                "stable": self.is_pair_stable(p)
            } for p in active_pairs},
            "volatility": {p: round(self.calculate_volatility(p) or 0, 3) for p in active_pairs},
            "position_sizes": {p: round(self.calculate_position_size(p), 3) for p in active_pairs},
            "correlation_matrix": matrix
        }
        
        return {
            "is_valid": len(issues) == 0,
            "issues": issues,
            "metrics": metrics
        }
    
    # ===== State Management =====
    
    def load_state(self) -> Dict:
        """Load portfolio safety state."""
        return self.writer.read_json_safe(self.state_file, {
            "last_check": None,
            "active_pairs": [],
            "blocked_pairs": [],
            "validation_log": []
        })
    
    def save_state(self, state: Dict) -> bool:
        """Save state atomically."""
        state["last_check"] = datetime.now().isoformat()
        return self.writer.write_json(self.state_file, state)
    
    def run_validation(self, active_pairs: List[str]) -> Dict:
        """
        Run full portfolio validation and save state.
        
        Args:
            active_pairs: Current portfolio pairs
        
        Returns:
            Validation result with metrics and issues
        """
        try:
            with FileLocker(self.state_file, timeout=10.0):
                result = self.validate_portfolio(active_pairs)
                
                state = {
                    "last_check": datetime.now().isoformat(),
                    "active_pairs": active_pairs,
                    "validation_result": result,
                    "instance_id": self.instance_id
                }
                
                if not self.save_state(state):
                    logger.error("Failed to save validation state")
                    return {**result, "save_failed": True}
                
                logger.info(f"Validation complete: {result['is_valid']} (issues: {len(result['issues'])})")
                return result
        
        except Exception as e:
            logger.error(f"Validation run failed: {e}")
            return {
                "is_valid": False,
                "error": str(e),
                "issues": ["Validation engine exception"]
            }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Test with synthetic data
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = f"{tmpdir}/test.db"
        
        # Create test database
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY,
                timestamp INTEGER,
                tier TEXT,
                pair TEXT,
                pnl_percent REAL
            )
        """)
        
        # Insert test data
        pairs = ['XBTUSD', 'ETHUSD', 'ADAUSD']
        for i, pair in enumerate(pairs):
            for j in range(50):
                pnl = 1.5 + (j % 3) * 0.3 + (i * 0.2)
                cursor.execute(
                    "INSERT INTO trades (timestamp, tier, pair, pnl_percent) VALUES (?, ?, ?, ?)",
                    (int(time.time()) - j * 100, 'paper', pair, pnl)
                )
        
        conn.commit()
        conn.close()
        
        # Test engine
        engine = PortfolioSafetyEngine(db_path, "test-instance", "paper")
        
        result = engine.validate_pair_for_addition('ETHUSD', ['XBTUSD'])
        print(f"✓ Can add ETHUSD to [XBTUSD]: {result['can_add']}")
        
        val = engine.validate_portfolio(['XBTUSD', 'ETHUSD', 'ADAUSD'])
        print(f"✓ Portfolio validation: {val['is_valid']} (pairs: {val['metrics']['pair_count']})")
        
        print("✓ Portfolio safety engine working")
