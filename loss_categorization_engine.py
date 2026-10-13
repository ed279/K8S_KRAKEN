"""
Loss Categorization Engine — Kraken Bot v12 Days 4-5
Categorizes each loss into: slippage, whipsaw, black-swan, regime-change.
Tracks per-loss-type metrics for pattern analysis and signal tuning.
"""

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from enum import Enum
import logging
import os

logger = logging.getLogger(__name__)


class LossCategory(Enum):
    """Loss categorization types."""
    SLIPPAGE = "slippage"
    WHIPSAW = "whipsaw"
    BLACK_SWAN = "black_swan"
    REGIME_CHANGE = "regime_change"
    TECHNICAL_ERROR = "technical_error"
    UNKNOWN = "unknown"


class LossCategorization:
    """Attribution for a single loss."""
    
    def __init__(self, 
                 trade_id: int,
                 category: LossCategory,
                 confidence: float,
                 loss_amount_usd: float,
                 contributing_factors: Dict[str, float],
                 notes: str = ""):
        self.trade_id = trade_id
        self.category = category
        self.confidence = confidence  # 0.0-1.0
        self.loss_amount_usd = loss_amount_usd
        self.contributing_factors = contributing_factors  # e.g., {"spread": 0.8, "slippage": 0.2}
        self.notes = notes
        self.timestamp = datetime.utcnow().isoformat()
    
    def to_dict(self) -> Dict:
        return {
            "trade_id": self.trade_id,
            "category": self.category.value,
            "confidence": self.confidence,
            "loss_amount_usd": self.loss_amount_usd,
            "contributing_factors": self.contributing_factors,
            "notes": self.notes,
            "timestamp": self.timestamp
        }


class LossCategorizer:
    """
    Analyze each loss and attribute it to one of 5 categories.
    
    Categories:
    1. SLIPPAGE: Expected movement during execution (0-2% typical)
    2. WHIPSAW: Quick reversal after entry (reversed <5min)
    3. BLACK_SWAN: Sudden volatility spike or news event
    4. REGIME_CHANGE: Signal valid in old regime, invalid in new one
    5. TECHNICAL_ERROR: Bugs, data issues, misconfiguration
    """
    
    def __init__(self,
                 trades_db: str = "/home/claude/kraken-bot/trades.db",
                 categories_path: str = "/dev/shm/cephalobot/loss_categories.json",
                 hourly_volatility_threshold: float = 2.5):
        """
        Initialize loss categorizer.
        
        Args:
            trades_db: SQLite trades database
            categories_path: Where to persist categorization records
            hourly_volatility_threshold: % change in 1h = black swan marker
        """
        self.trades_db = trades_db
        self.categories_path = categories_path
        self.hourly_volatility_threshold = hourly_volatility_threshold
        self.load_categories()
    
    def load_categories(self) -> None:
        """Load existing categorizations."""
        if os.path.exists(self.categories_path):
            try:
                with open(self.categories_path, 'r') as f:
                    self.categories = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.categories = {"categorizations": [], "summary": {}}
        else:
            self.categories = {"categorizations": [], "summary": {}}
    
    def save_categories(self) -> None:
        """Persist categorization records."""
        os.makedirs(os.path.dirname(self.categories_path), exist_ok=True)
        with open(self.categories_path, 'w') as f:
            json.dump(self.categories, f, indent=2)
    
    def get_price_volatility(self, pair: str, 
                            before_time: str, 
                            after_time: str) -> Tuple[float, float]:
        """
        Get price and volatility metrics for a pair during a time window.
        
        Returns:
            (price_change_pct, hourly_volatility_pct)
        """
        try:
            conn = sqlite3.connect(self.trades_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Query market data around trade time (if available in schema)
            cursor.execute("""
                SELECT 
                    MAX(high_price) as high,
                    MIN(low_price) as low,
                    close_price as close
                FROM market_snapshots
                WHERE pair = ? 
                    AND snapshot_time >= ?
                    AND snapshot_time <= ?
                ORDER BY snapshot_time
                LIMIT 60
            """, (pair, before_time, after_time))
            
            rows = cursor.fetchall()
            conn.close()
            
            if not rows or len(rows) < 2:
                return 0.0, 0.0
            
            first = rows[0]
            last = rows[-1]
            
            range_pct = ((first['high'] - first['low']) / first['low'] * 100) if first['low'] > 0 else 0
            close_change = ((last['close'] - first['close']) / first['close'] * 100) if first['close'] > 0 else 0
            
            return close_change, range_pct
        
        except Exception as e:
            logger.debug(f"Could not fetch volatility for {pair}: {e}")
            return 0.0, 0.0
    
    def analyze_entry_signal_quality(self, trade: Dict) -> float:
        """
        Assess signal quality: was the entry signal strong?
        
        Returns:
            Quality score 0.0-1.0 (higher = better signal)
        """
        confidence = trade.get('entry_confidence', 0.5)
        
        # Confidence is the primary signal quality metric
        return min(1.0, confidence)
    
    def categorize_loss(self, trade: Dict) -> LossCategorization:
        """
        Categorize a single loss trade.
        
        Args:
            trade: Trade record from database
        
        Returns:
            LossCategorization with category, confidence, factors
        """
        trade_id = trade['id']
        loss_amount = abs(trade['net_pnl_usd'])
        loss_pct = abs(trade['net_pnl_pct'])
        
        entry_price = trade['entry_price']
        exit_price = trade['exit_price']
        entry_time = trade['entry_time']
        exit_time = trade['exit_time']
        
        pair = trade['pair']
        exit_reason = trade.get('exit_reason', 'unknown')
        
        factors = {}
        
        # === Check for technical errors ===
        if trade.get('has_error_flag', False):
            factors['error_flag'] = 1.0
            return LossCategorization(
                trade_id, LossCategory.TECHNICAL_ERROR, 0.95, loss_amount,
                factors, "Marked with error flag"
            )
        
        # Detect whipsaw: quick reversal (exited within 5 minutes)
        entry_dt = datetime.fromisoformat(entry_time)
        exit_dt = datetime.fromisoformat(exit_time)
        hold_minutes = (exit_dt - entry_dt).total_seconds() / 60
        
        if hold_minutes < 5 and exit_reason in ['stop_loss', 'take_profit_failed']:
            factors['quick_reversal'] = 0.6
            factors['short_hold_time'] = (5 - hold_minutes) / 5  # Normalized 0-1
            
            # Check signal quality
            signal_quality = self.analyze_entry_signal_quality(trade)
            if signal_quality > 0.7:
                return LossCategorization(
                    trade_id, LossCategory.WHIPSAW, 0.85, loss_amount,
                    factors, f"Reversed in {hold_minutes:.1f}min despite high-confidence signal"
                )
        
        # === Check for slippage (small, expected loss) ===
        if loss_pct < 0.5:
            factors['loss_pct_small'] = 1.0 - (loss_pct / 0.5)
            expected_spread = trade.get('spread_bps', 2) / 10000  # Convert basis points to %
            if loss_pct <= expected_spread * 3:
                factors['within_expected_spread'] = 1.0
                return LossCategorization(
                    trade_id, LossCategory.SLIPPAGE, 0.88, loss_amount,
                    factors, f"Loss within expected spread (expected ~{expected_spread:.2%})"
                )
        
        # === Check for black swan (high volatility event) ===
        vol_price_change, vol_range = self.get_price_volatility(
            pair, entry_time, exit_time
        )
        
        if abs(vol_price_change) > 3.0 or vol_range > self.hourly_volatility_threshold:
            factors['price_change_pct'] = abs(vol_price_change)
            factors['volatility_range'] = vol_range
            return LossCategorization(
                trade_id, LossCategory.BLACK_SWAN, 0.82, loss_amount,
                factors, f"High volatility detected ({vol_range:.2f}% range, {vol_price_change:.2f}% move)"
            )
        
        # === Check for regime change ===
        # Look at recent performance of same signal
        try:
            conn = sqlite3.connect(self.trades_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            signal = trade.get('entry_signal', 'unknown')
            
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN net_pnl_usd > 0 THEN 1 ELSE 0 END) as winning_trades,
                    ROUND(100.0 * SUM(CASE WHEN net_pnl_usd > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) as win_rate,
                    entry_time
                FROM trades
                WHERE entry_signal = ?
                    AND tier = 'live'
                    AND status = 'closed'
                    AND entry_time >= DATE('now', '-7 days')
                ORDER BY entry_time DESC
                LIMIT 20
            """, (signal,))
            
            recent = cursor.fetchall()
            conn.close()
            
            if recent and len(recent) >= 10:
                recent_wr = recent[0]['win_rate'] / 100.0 if recent[0]['win_rate'] else 0
                
                # If signal used to work but now failing, likely regime change
                if recent_wr < 0.3:
                    factors['recent_win_rate_collapse'] = 1.0 - recent_wr
                    factors['signal_degradation'] = 0.7
                    return LossCategorization(
                        trade_id, LossCategory.REGIME_CHANGE, 0.78, loss_amount,
                        factors, f"Signal {signal} WR collapsed to {recent_wr:.1%} (7-day)"
                    )
        
        except Exception as e:
            logger.debug(f"Failed to check regime for {signal}: {e}")
        
        # === Default: Unknown ===
        factors['no_clear_category'] = 1.0
        return LossCategorization(
            trade_id, LossCategory.UNKNOWN, 0.50, loss_amount,
            factors, "Could not determine clear category"
        )
    
    def categorize_all_uncategorized_losses(self) -> Dict:
        """
        Find all losing trades that haven't been categorized yet.
        Categorize them and update summary stats.
        
        Returns:
            Summary of categorization run
        """
        try:
            conn = sqlite3.connect(self.trades_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Get all losses from live trading in past 7 days
            cursor.execute("""
                SELECT *
                FROM trades
                WHERE tier = 'live'
                    AND status = 'closed'
                    AND net_pnl_usd < 0
                    AND entry_time >= DATE('now', '-7 days')
                ORDER BY entry_time DESC
            """)
            
            losing_trades = cursor.fetchall()
            conn.close()
            
            # Categorize each loss
            new_categorizations = 0
            categorized_loss_usd = 0.0
            
            for trade in losing_trades:
                trade_dict = dict(trade)
                trade_id = trade['id']
                
                # Skip if already categorized
                if any(c['trade_id'] == trade_id for c in self.categories['categorizations']):
                    logger.debug(f"Trade {trade_id} already categorized")
                    continue
                
                categorization = self.categorize_loss(trade_dict)
                self.categories['categorizations'].append(categorization.to_dict())
                
                new_categorizations += 1
                categorized_loss_usd += categorization.loss_amount_usd
                
                logger.info(f"Categorized trade {trade_id}: {categorization.category.value} (${categorization.loss_amount_usd:.2f})")
            
            # Update summary
            self.update_summary()
            self.save_categories()
            
            result = {
                "timestamp": datetime.utcnow().isoformat(),
                "new_categorizations": new_categorizations,
                "total_loss_categorized_usd": categorized_loss_usd,
                "category_breakdown": self.categories['summary']
            }
            
            logger.info(f"Categorization complete: {new_categorizations} new, ${categorized_loss_usd:.2f} total")
            return result
        
        except Exception as e:
            logger.error(f"Categorization failed: {e}")
            return {"error": str(e), "timestamp": datetime.utcnow().isoformat()}
    
    def update_summary(self) -> None:
        """Recalculate summary statistics from categorizations."""
        summary = {}
        total_loss = 0.0
        
        for cat in self.categories['categorizations']:
            category = cat['category']
            loss = cat['loss_amount_usd']
            
            if category not in summary:
                summary[category] = {
                    "count": 0,
                    "total_loss_usd": 0.0,
                    "avg_loss_usd": 0.0,
                    "avg_confidence": 0.0,
                    "confidences": []
                }
            
            summary[category]['count'] += 1
            summary[category]['total_loss_usd'] += loss
            summary[category]['confidences'].append(cat['confidence'])
            total_loss += loss
        
        # Calculate averages
        for category in summary:
            if summary[category]['count'] > 0:
                summary[category]['avg_loss_usd'] = (
                    summary[category]['total_loss_usd'] / summary[category]['count']
                )
                summary[category]['avg_confidence'] = (
                    sum(summary[category]['confidences']) / len(summary[category]['confidences'])
                )
            del summary[category]['confidences']
        
        self.categories['summary'] = summary
        logger.info(f"Summary updated: {len(self.categories['categorizations'])} total losses, ${total_loss:.2f}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    categorizer = LossCategorizer()
    result = categorizer.categorize_all_uncategorized_losses()
    print(json.dumps(result, indent=2))
