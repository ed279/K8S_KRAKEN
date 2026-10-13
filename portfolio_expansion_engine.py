"""
Portfolio Expansion Engine v2
- Atomic file writes
- Multi-instance dedup (require 2+ instances to agree)
- 70%+ win rate threshold for pair promotion
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Set
from datetime import datetime
from collections import defaultdict

from atomic_file_utils import AtomicFileWriter, FileLocker

logger = logging.getLogger(__name__)


class PortfolioExpansionEngine:
    """
    Discover new profitable pairs from shadow trading.
    
    Multi-instance coordination:
    - Each shadow instance votes on which pairs are winners
    - Pair must be flagged by 2+ instances to be promoted
    - Tracks discovered_by_instances for audit trail
    """
    
    def __init__(self, db_path: str, instance_id: str, tier: str):
        self.db_path = db_path
        self.instance_id = instance_id
        self.tier = tier
        self.base_dir = Path(db_path).parent
        self.candidates_file = str(self.base_dir / "expansion_candidates.json")
        self.writer = AtomicFileWriter()
    
    def get_pair_stats(self, pair: str) -> Dict:
        """Get win/loss stats for a pair from database."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
                    AVG(pnl) as avg_pnl,
                    AVG(CASE WHEN pnl > 0 THEN pnl ELSE NULL END) as avg_win,
                    AVG(CASE WHEN pnl < 0 THEN pnl ELSE NULL END) as avg_loss
                FROM trades
                WHERE pair = ? AND tier = ?
            """, (pair, self.tier))
            
            row = cursor.fetchone()
            conn.close()
            
            if not row or row[0] == 0:
                return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0}
            
            total, wins, losses, avg_pnl, avg_win, avg_loss = row
            win_rate = wins / total if total > 0 else 0
            
            return {
                "pair": pair,
                "total": total,
                "wins": wins or 0,
                "losses": losses or 0,
                "win_rate": win_rate,
                "avg_pnl": avg_pnl or 0,
                "avg_win": avg_win or 0,
                "avg_loss": avg_loss or 0
            }
        
        except Exception as e:
            logger.error(f"Failed to get stats for {pair}: {e}")
            return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0}
    
    def get_all_pairs_in_shadow(self) -> Set[str]:
        """Get all pairs traded in shadow tier."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT pair FROM trades WHERE tier = 'shadow'")
            pairs = {row[0] for row in cursor.fetchall()}
            conn.close()
            return pairs
        
        except Exception as e:
            logger.error(f"Failed to get pairs: {e}")
            return set()
    
    def scan_for_winners(self, min_trades: int = 30, 
                        min_win_rate: float = 0.70) -> Dict:
        """
        Scan shadow tier for winning pairs.
        
        Returns dict of {pair: stats} for pairs exceeding thresholds
        """
        all_pairs = self.get_all_pairs_in_shadow()
        winners = {}
        
        for pair in all_pairs:
            stats = self.get_pair_stats(pair)
            
            # Only consider if enough data
            if stats["total"] < min_trades:
                continue
            
            # Only flag if win rate exceeds threshold
            if stats["win_rate"] >= min_win_rate:
                winners[pair] = stats
                logger.info(f"Found winner: {pair} WR={stats['win_rate']:.1%} ({stats['wins']}/{stats['total']})")
        
        return winners
    
    def load_candidates(self) -> Dict:
        """Load expansion candidates file."""
        return self.writer.read_json_safe(self.candidates_file, {})
    
    def save_candidates(self, candidates: Dict) -> bool:
        """Save candidates atomically."""
        return self.writer.write_json(self.candidates_file, candidates)
    
    def run_expansion_scan(self, min_trades: int = 30) -> Dict:
        """
        Main scan method.
        
        1. Find winners in this instance's shadow data
        2. Vote for them in candidates file
        3. Require 2+ instances to agree before flagging for promotion
        
        Returns: {"newly_promoted": [...], "in_progress": [...], "summary": {...}}
        """
        winners = self.scan_for_winners(min_trades=min_trades)
        
        try:
            with FileLocker(self.candidates_file, timeout=10.0):
                candidates = self.load_candidates()
                
                # Vote for each winner
                for pair, stats in winners.items():
                    if pair not in candidates:
                        candidates[pair] = {
                            "status": "voting",
                            "discovered_by": [],
                            "votes": 0,
                            "stats_history": [],
                            "promoted_at": None
                        }
                    
                    # Record this instance's vote
                    if self.instance_id not in candidates[pair]["discovered_by"]:
                        candidates[pair]["discovered_by"].append(self.instance_id)
                        candidates[pair]["votes"] = len(candidates[pair]["discovered_by"])
                        logger.info(f"Vote for {pair}: {candidates[pair]['votes']} instances agree")
                    
                    # Store stats
                    candidates[pair]["stats_history"].append({
                        "timestamp": datetime.utcnow().isoformat(),
                        "instance": self.instance_id,
                        "stats": stats
                    })
                    
                    # Promote to paper if 2+ instances agree
                    if candidates[pair]["votes"] >= 2 and candidates[pair]["status"] != "promoted":
                        candidates[pair]["status"] = "promoted"
                        candidates[pair]["promoted_at"] = datetime.utcnow().isoformat()
                        logger.info(f"PROMOTED {pair} to paper tier (2+ instances agree)")
                
                self.save_candidates(candidates)
                
                # Return summary
                promoted = [p for p, c in candidates.items() if c["status"] == "promoted"]
                in_progress = [p for p, c in candidates.items() if c["status"] == "voting" and c["votes"] == 1]
                
                return {
                    "newly_promoted": promoted,
                    "in_progress": in_progress,
                    "summary": {
                        "total_candidates": len(candidates),
                        "promoted_count": len(promoted),
                        "in_progress_count": len(in_progress),
                        "instance_id": self.instance_id,
                        "winners_found": len(winners)
                    }
                }
        
        except TimeoutError:
            logger.error(f"Could not acquire lock for {self.candidates_file}")
            return {"error": "timeout"}
        except Exception as e:
            logger.error(f"Expansion scan failed: {e}")
            return {"error": str(e)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    engine = PortfolioExpansionEngine("/tmp/trades.db", "shadow-1", "shadow")
    result = engine.run_expansion_scan()
    print(f"Expansion scan: {result['summary']}")
