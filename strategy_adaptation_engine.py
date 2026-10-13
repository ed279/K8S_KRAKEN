"""
StrategyAdaptationEngine — Phase 2: Shadow Patterns → Live Adjustments
Scans shadow for winning strategies, validates in paper, deploys to live with gradual ramps.
"""

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class StrategyAdaptationEngine:
    """
    Closes the feedback loop between shadow, paper, and live tiers.
    
    Workflow:
    1. Scan shadow every 6 hours for winning strategy patterns (signal type + exit reason)
    2. Find clusters with >70% WR, >10 trades
    3. Test same pattern in paper: must maintain ≥60% WR to be "trusted"
    4. Deploy to live with conservative gates: gradually adjust confidence 60% → 65% (5-day ramp)
    5. Log all parameter changes to config_audit table
    """
    
    def __init__(self, 
                 trades_db: str = "/home/claude/kraken-bot/trades.db",
                 config_path: str = "/dev/shm/cephalobot/kraken_v12_config.json"):
        """
        Initialize strategy adaptation engine.
        
        Args:
            trades_db: Path to trades database (contains shadow/paper/live trades)
            config_path: Path to live config (for reading/writing parameter adjustments)
        """
        self.trades_db = trades_db
        self.config_path = config_path
        self.strategies_path = "/dev/shm/cephalobot/candidate_strategies.json"
        self.config_audit_path = "/dev/shm/cephalobot/config_audit.json"
        self.load_config()
        self.load_strategies()
    
    def load_config(self) -> None:
        """Load live trading config."""
        import os
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    self.config = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.config = {"ENTRY_CONFIDENCE_THRESHOLD": 60}
        else:
            self.config = {"ENTRY_CONFIDENCE_THRESHOLD": 60}
    
    def save_config(self) -> None:
        """Persist live config."""
        import os
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, 'w') as f:
            json.dump(self.config, f, indent=2)
    
    def load_strategies(self) -> None:
        """Load existing strategy candidates."""
        import os
        if os.path.exists(self.strategies_path):
            try:
                with open(self.strategies_path, 'r') as f:
                    self.strategies = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.strategies = {}
        else:
            self.strategies = {}
    
    def save_strategies(self) -> None:
        """Persist strategy candidates."""
        import os
        os.makedirs(os.path.dirname(self.strategies_path), exist_ok=True)
        with open(self.strategies_path, 'w') as f:
            json.dump(self.strategies, f, indent=2)
    
    def scan_shadow_for_patterns(self, days: int = 14) -> List[Dict]:
        """
        Scan shadow tier for winning strategy patterns.
        
        Group by: signal_type, confidence_band, exit_reason
        Find clusters with >70% WR and >10 trades
        
        Returns:
            List of candidate strategies
        """
        try:
            conn = sqlite3.connect(self.trades_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            
            cursor.execute(f"""
                SELECT 
                    signal_type,
                    CASE 
                        WHEN entry_confidence >= 70 THEN '70-79'
                        WHEN entry_confidence >= 60 THEN '60-69'
                        ELSE '50-59'
                    END as confidence_band,
                    exit_reason,
                    COUNT(*) as trade_count,
                    SUM(CASE WHEN net_pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
                    ROUND(100.0 * SUM(CASE WHEN net_pnl_usd > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) as win_rate_pct,
                    ROUND(AVG(net_pnl_pct), 3) as avg_profit_pct
                FROM trades
                WHERE tier = 'shadow'
                    AND status = 'closed'
                    AND entry_time >= ?
                GROUP BY signal_type, confidence_band, exit_reason
                HAVING COUNT(*) >= 10
                    AND win_rate_pct >= 70.0
                ORDER BY win_rate_pct DESC
            """, (cutoff,))
            
            rows = cursor.fetchall()
            conn.close()
            
            candidates = []
            for row in rows:
                candidate = {
                    "pattern_id": f"{row['signal_type']}_{row['confidence_band']}_{row['exit_reason']}",
                    "signal_type": row['signal_type'],
                    "confidence_band": row['confidence_band'],
                    "exit_reason": row['exit_reason'],
                    "shadow_trades": row['trade_count'],
                    "shadow_wins": row['wins'],
                    "shadow_win_rate": row['win_rate_pct'] / 100.0,
                    "shadow_avg_profit_pct": row['avg_profit_pct'],
                    "discovered_at": datetime.utcnow().isoformat()
                }
                candidates.append(candidate)
                logger.info(f"Pattern: {candidate['pattern_id']} → {row['win_rate_pct']}% WR ({row['trade_count']} trades)")
            
            return candidates
        
        except Exception as e:
            logger.error(f"Failed to scan shadow: {e}")
            return []
    
    def validate_pattern_in_paper(self, pattern: Dict) -> Optional[Dict]:
        """
        Test same strategy pattern in paper tier (real prices, simulated execution).
        
        Must maintain ≥60% WR over at least 10 trades to be "trusted".
        """
        try:
            conn = sqlite3.connect(self.trades_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()  # Past 7 days of paper trades
            
            cursor.execute(f"""
                SELECT 
                    COUNT(*) as trade_count,
                    SUM(CASE WHEN net_pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
                    ROUND(100.0 * SUM(CASE WHEN net_pnl_usd > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) as win_rate_pct,
                    ROUND(AVG(net_pnl_pct), 3) as avg_profit_pct
                FROM trades
                WHERE tier = 'paper'
                    AND status = 'closed'
                    AND signal_type = ?
                    AND exit_reason = ?
                    AND entry_confidence >= (CAST(? as REAL) * 0.9)  -- Allow ±10% confidence band
                    AND entry_time >= ?
            """, (pattern['signal_type'], pattern['exit_reason'], int(pattern['confidence_band'].split('-')[0]), cutoff))
            
            row = cursor.fetchone()
            conn.close()
            
            if not row or row['trade_count'] < 10:
                logger.warning(f"Pattern {pattern['pattern_id']}: Insufficient paper trades ({row['trade_count'] if row else 0}/10)")
                return None
            
            win_rate = row['win_rate_pct'] / 100.0
            if win_rate < 0.60:
                logger.warning(f"Pattern {pattern['pattern_id']}: Paper validation FAILED ({row['win_rate_pct']}% < 60%)")
                return None
            
            logger.info(f"Pattern {pattern['pattern_id']}: Paper validation PASSED ({row['win_rate_pct']}% WR)")
            
            return {
                "paper_trades": row['trade_count'],
                "paper_wins": row['wins'],
                "paper_win_rate": win_rate,
                "paper_avg_profit_pct": row['avg_profit_pct'],
                "validated_at": datetime.utcnow().isoformat()
            }
        
        except Exception as e:
            logger.error(f"Failed to validate pattern in paper: {e}")
            return None
    
    def promote_strategy_to_live(self, pattern: Dict, paper_stats: Dict) -> bool:
        """
        Promote validated pattern to live with conservative gates.
        
        Adjusts ENTRY_CONFIDENCE_THRESHOLD gradually:
        - Start: 60% (baseline)
        - Target: 65% (based on shadow signal strength)
        - Ramp: +1 per day over 5 days (2026 v12 conservative tuning)
        """
        pattern_id = pattern['pattern_id']
        
        # Store strategy in database
        if pattern_id not in self.strategies:
            self.strategies[pattern_id] = {
                "pattern_id": pattern_id,
                "shadow_stats": {k: v for k, v in pattern.items() if k != 'pattern_id'},
                "paper_stats": paper_stats,
                "status": "live",
                "promoted_at": datetime.utcnow().isoformat(),
                "live_confidence_start": 60,
                "live_confidence_target": 65,
                "live_confidence_current": 60,
                "ramp_days_remaining": 5
            }
        else:
            self.strategies[pattern_id]['paper_stats'] = paper_stats
            self.strategies[pattern_id]['status'] = 'live'
            self.strategies[pattern_id]['promoted_at'] = datetime.utcnow().isoformat()
        
        self.save_strategies()
        logger.info(f"Pattern {pattern_id} promoted to live (60% → 65% over 5 days)")
        return True
    
    def apply_daily_confidence_ramp(self) -> Optional[Dict]:
        """
        Called daily by systemd timer.
        Gradually tightens entry confidence gate by +1 per day (max +5 over 5 days).
        
        Returns:
            Change summary if confidence was adjusted
        """
        current_confidence = self.config.get("ENTRY_CONFIDENCE_THRESHOLD", 60)
        new_confidence = current_confidence
        
        # Check if any live strategy needs ramping
        for strategy_id, strategy in self.strategies.items():
            if strategy.get('status') != 'live':
                continue
            
            target = strategy.get('live_confidence_target', 65)
            days_remaining = strategy.get('ramp_days_remaining', 0)
            
            if days_remaining > 0 and current_confidence < target:
                new_confidence = min(target, current_confidence + 1)
                strategy['ramp_days_remaining'] = days_remaining - 1
                strategy['live_confidence_current'] = new_confidence
        
        if new_confidence != current_confidence:
            self.config["ENTRY_CONFIDENCE_THRESHOLD"] = new_confidence
            self.save_config()
            self.save_strategies()
            
            change = {
                "timestamp": datetime.utcnow().isoformat(),
                "param": "ENTRY_CONFIDENCE_THRESHOLD",
                "old_value": current_confidence,
                "new_value": new_confidence,
                "reason": "Daily strategy adaptation ramp",
                "triggered_by": "auto"
            }
            
            logger.info(f"✓ Config adjusted: {current_confidence}% → {new_confidence}%")
            return change
        
        return None
    
    def run_adaptation_scan(self) -> Dict:
        """
        Main entry point: scan shadow, validate paper, promote to live.
        Called every 6 hours by systemd timer.
        """
        logger.info("=== Starting strategy adaptation scan ===")
        
        # Scan shadow for winners
        candidates = self.scan_shadow_for_patterns(days=14)
        logger.info(f"Found {len(candidates)} candidate patterns in shadow")
        
        promoted = 0
        for candidate in candidates:
            pattern_id = candidate['pattern_id']
            
            # Skip if already live
            if pattern_id in self.strategies and self.strategies[pattern_id]['status'] == 'live':
                logger.info(f"Pattern {pattern_id} already live, skipping")
                continue
            
            # Validate in paper
            paper_stats = self.validate_pattern_in_paper(candidate)
            if paper_stats:
                self.promote_strategy_to_live(candidate, paper_stats)
                promoted += 1
        
        result = {
            "scan_time": datetime.utcnow().isoformat(),
            "candidates_found": len(candidates),
            "promoted_to_live": promoted,
            "current_confidence": self.config.get("ENTRY_CONFIDENCE_THRESHOLD", 60),
            "strategies_active": len([s for s in self.strategies.values() if s.get('status') == 'live'])
        }
        
        logger.info(f"Adaptation scan complete: {promoted} patterns promoted")
        return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    engine = StrategyAdaptationEngine()
    result = engine.run_adaptation_scan()
    print(json.dumps(result, indent=2))
