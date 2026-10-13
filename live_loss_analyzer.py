"""
LiveLossAnalyzer — Phase 3: Detect Regressions, Feed Back to Shadow
Compares live losses against shadow predictions, adjusts shadow stress factor.
"""

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class LiveLossAnalyzer:
    """
    Bidirectional feedback loop: live losses → shadow recalibration.
    
    Problem it solves:
    - Shadow predicts strategy X will win 70%
    - Paper validates: yes, 65% WR
    - Live deploys strategy X
    - Live loses 2+ times in a row on strategy X
    → Shadow was under-stressed (simulated prices too forgiving)
    
    Solution:
    - Detect regression in live (2+ consecutive losses)
    - Backtest: Would shadow have made this trade with same signal?
    - If yes: Shadow was optimistic → increase shadow stress factor
    - If no: Market condition changed → adjust shadow features
    """
    
    def __init__(self, 
                 trades_db: str = "/home/claude/kraken-bot/trades.db",
                 shadow_stress_path: str = "/dev/shm/cephalobot/shadow_stress_factor.json"):
        """
        Initialize loss analyzer.
        
        Args:
            trades_db: Path to trades database
            shadow_stress_path: Path to shadow stress config
        """
        self.trades_db = trades_db
        self.shadow_stress_path = shadow_stress_path
        self.regressions_path = "/dev/shm/cephalobot/live_regressions.json"
        self.load_stress_config()
        self.load_regressions()
    
    def load_stress_config(self) -> None:
        """Load shadow stress factor config."""
        import os
        if os.path.exists(self.shadow_stress_path):
            try:
                with open(self.shadow_stress_path, 'r') as f:
                    self.stress_config = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.stress_config = {
                    "global_factor": 1.0,
                    "per_pair": {},
                    "last_updated": datetime.utcnow().isoformat()
                }
        else:
            self.stress_config = {
                "global_factor": 1.0,
                "per_pair": {},
                "last_updated": datetime.utcnow().isoformat()
            }
    
    def save_stress_config(self) -> None:
        """Persist stress factor config."""
        import os
        os.makedirs(os.path.dirname(self.shadow_stress_path), exist_ok=True)
        self.stress_config['last_updated'] = datetime.utcnow().isoformat()
        with open(self.shadow_stress_path, 'w') as f:
            json.dump(self.stress_config, f, indent=2)
    
    def load_regressions(self) -> None:
        """Load existing regression records."""
        import os
        if os.path.exists(self.regressions_path):
            try:
                with open(self.regressions_path, 'r') as f:
                    self.regressions = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.regressions = []
        else:
            self.regressions = []
    
    def save_regressions(self) -> None:
        """Persist regression records."""
        import os
        os.makedirs(os.path.dirname(self.regressions_path), exist_ok=True)
        with open(self.regressions_path, 'w') as f:
            json.dump(self.regressions, f, indent=2)
    
    def detect_regressions(self, days: int = 3) -> List[Dict]:
        """
        Find 2+ consecutive losses in live trading over past N days.
        
        Returns:
            List of regression events
        """
        try:
            conn = sqlite3.connect(self.trades_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            
            cursor.execute(f"""
                SELECT 
                    id,
                    pair,
                    entry_signal,
                    entry_confidence,
                    exit_reason,
                    net_pnl_usd,
                    net_pnl_pct,
                    entry_time,
                    exit_time
                FROM trades
                WHERE tier = 'live'
                    AND status = 'closed'
                    AND entry_time >= ?
                ORDER BY entry_time DESC
            """, (cutoff,))
            
            trades = cursor.fetchall()
            conn.close()
            
            regressions = []
            consecutive_losses = []
            
            for trade in trades:
                if trade['net_pnl_usd'] < 0:
                    consecutive_losses.append(trade)
                else:
                    if len(consecutive_losses) >= 2:
                        regression = {
                            "detection_time": datetime.utcnow().isoformat(),
                            "loss_count": len(consecutive_losses),
                            "trades": [dict(t) for t in consecutive_losses],
                            "total_loss_usd": sum(t['net_pnl_usd'] for t in consecutive_losses),
                            "signal_type": consecutive_losses[0]['entry_signal'],
                            "pair": consecutive_losses[0]['pair']
                        }
                        regressions.append(regression)
                        logger.warning(f"REGRESSION: {consecutive_losses[0]['entry_signal']} on {consecutive_losses[0]['pair']} ({len(consecutive_losses)} losses)")
                    
                    consecutive_losses = []
            
            # Handle final sequence if it ends with loss
            if len(consecutive_losses) >= 2:
                regression = {
                    "detection_time": datetime.utcnow().isoformat(),
                    "loss_count": len(consecutive_losses),
                    "trades": [dict(t) for t in consecutive_losses],
                    "total_loss_usd": sum(t['net_pnl_usd'] for t in consecutive_losses),
                    "signal_type": consecutive_losses[0]['entry_signal'],
                    "pair": consecutive_losses[0]['pair']
                }
                regressions.append(regression)
            
            return regressions
        
        except Exception as e:
            logger.error(f"Failed to detect regressions: {e}")
            return []
    
    def backtest_shadow_match(self, regression: Dict) -> bool:
        """
        For a live loss, check: Did shadow predict this trade would win?
        
        Returns:
            True = shadow was optimistic, False = market condition changed
        """
        try:
            conn = sqlite3.connect(self.trades_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            signal = regression['signal_type']
            pair = regression['pair']
            loss_time = regression['trades'][0]['entry_time']
            
            # Look at shadow trades from same period
            cursor.execute(f"""
                SELECT 
                    COUNT(*) as shadow_trades,
                    SUM(CASE WHEN net_pnl_usd > 0 THEN 1 ELSE 0 END) as shadow_wins,
                    ROUND(100.0 * SUM(CASE WHEN net_pnl_usd > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) as shadow_win_rate
                FROM trades
                WHERE tier = 'shadow'
                    AND signal_type = ?
                    AND pair = ?
                    AND entry_time >= DATE(?, '-30 days')
                    AND entry_time <= ?
            """, (signal, pair, loss_time, loss_time))
            
            row = cursor.fetchone()
            conn.close()
            
            if row['shadow_trades'] < 5:
                logger.warning(f"Not enough shadow backtest data for {signal} on {pair}")
                return False
            
            shadow_wr = row['shadow_win_rate'] / 100.0 if row['shadow_win_rate'] else 0
            
            # If shadow had >65% WR but live lost → regression is real
            if shadow_wr > 0.65:
                logger.warning(f"REGRESSION CONFIRMED: {signal}/{pair} (shadow: {row['shadow_win_rate']}%, live: loss)")
                return True
            else:
                logger.info(f"Regression rejected: {signal}/{pair} (shadow: {row['shadow_win_rate']}%, low confidence)")
                return False
        
        except Exception as e:
            logger.error(f"Failed to backtest shadow match: {e}")
            return False
    
    def increase_shadow_stress(self, regression: Dict, stress_increase: float = 0.1) -> None:
        """
        When shadow is overly optimistic, increase stress factor.
        
        Stress factor multiplies slippage + spreads in shadow simulation:
        - 1.0 = baseline
        - 1.1 = 10% more slippage
        - 1.2 = 20% more slippage
        
        Args:
            regression: Regression event
            stress_increase: How much to increase (0.1 = +10%)
        """
        pair = regression['pair']
        
        # Global increase
        old_global = self.stress_config['global_factor']
        new_global = min(2.0, old_global + stress_increase)  # Cap at 2.0x
        self.stress_config['global_factor'] = new_global
        
        # Per-pair increase
        if pair not in self.stress_config['per_pair']:
            self.stress_config['per_pair'][pair] = 1.0
        
        old_pair = self.stress_config['per_pair'][pair]
        new_pair = min(2.0, old_pair + stress_increase)
        self.stress_config['per_pair'][pair] = new_pair
        
        self.save_stress_config()
        
        logger.info(f"Shadow stress increased: global {old_global:.2f}→{new_global:.2f}, {pair} {old_pair:.2f}→{new_pair:.2f}")
    
    def run_loss_analysis(self) -> Dict:
        """
        Main entry point: detect regressions, confirm via backtest, adjust shadow.
        Called every 12 hours by systemd timer.
        """
        logger.info("=== Starting live loss analysis ===")
        
        # Detect regressions in past 3 days
        regressions = self.detect_regressions(days=3)
        logger.info(f"Found {len(regressions)} regression events")
        
        confirmed = 0
        for regression in regressions:
            # Check if already processed
            if any(r['detection_time'] == regression['detection_time'] for r in self.regressions):
                logger.info(f"Regression already processed: {regression['pair']}")
                continue
            
            # Verify via shadow backtest
            if self.backtest_shadow_match(regression):
                self.increase_shadow_stress(regression, stress_increase=0.1)
                confirmed += 1
                regression['status'] = 'confirmed_shadow_optimistic'
            else:
                regression['status'] = 'rejected_market_change'
            
            # Store record
            self.regressions.append(regression)
        
        self.save_regressions()
        
        result = {
            "analysis_time": datetime.utcnow().isoformat(),
            "regressions_detected": len(regressions),
            "confirmed_shadow_issues": confirmed,
            "current_global_stress": self.stress_config['global_factor']
        }
        
        logger.info(f"Loss analysis complete: {confirmed} shadow stress increases applied")
        return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    analyzer = LiveLossAnalyzer()
    result = analyzer.run_loss_analysis()
    print(json.dumps(result, indent=2))
