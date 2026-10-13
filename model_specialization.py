"""
Per-Pair Model Specialization Engine
Track which models excel on which trading pairs
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from collections import defaultdict

from atomic_file_utils import AtomicFileWriter, FileLocker

logger = logging.getLogger(__name__)


@dataclass
class PairModelStats:
    """Per-model stats for a specific pair."""
    pair: str
    model_name: str
    gpu: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    total_pnl: float = 0.0
    confidence_avg: float = 0.0
    signal_types: Dict[str, int] = None  # {signal_type: count}
    
    def __post_init__(self):
        if self.signal_types is None:
            self.signal_types = {}


class ModelSpecializationEngine:
    """
    Track per-pair model performance.
    
    Goals:
    - Identify which models excel on which pairs
    - Route signals to best-fit models
    - Avoid poor model + pair combinations
    """
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.base_dir = Path(db_path).parent
        self.spec_file = str(self.base_dir / "model_specialization.json")
        self.writer = AtomicFileWriter()
    
    def calculate_pair_model_stats(self, pair: str, model_name: str, 
                                   gpu: str, tier: str = "shadow") -> PairModelStats:
        """
        Calculate specialization stats for a pair+model combo.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get trades for this pair+model (via trade records)
            cursor.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
                    AVG(CASE WHEN pnl > 0 THEN pnl ELSE NULL END) as avg_win,
                    AVG(CASE WHEN pnl < 0 THEN pnl ELSE NULL END) as avg_loss,
                    SUM(pnl) as total_pnl,
                    AVG(signal_confidence) as avg_confidence,
                    signal_type
                FROM trades
                WHERE pair = ? AND tier = ? AND signal_type != ''
                GROUP BY signal_type
            """, (pair, tier))
            
            rows = cursor.fetchall()
            conn.close()
            
            if not rows:
                return PairModelStats(
                    pair=pair,
                    model_name=model_name,
                    gpu=gpu,
                    signal_types={}
                )
            
            # Aggregate across all signal types
            total_trades = sum(row[0] for row in rows)
            wins = sum(row[1] or 0 for row in rows)
            losses = sum(row[2] or 0 for row in rows)
            
            avg_wins = [row[3] for row in rows if row[3] is not None]
            avg_losses = [row[4] for row in rows if row[4] is not None]
            
            total_pnl = sum(row[5] or 0 for row in rows)
            avg_confidence = sum(row[6] or 0 for row in rows) / len(rows) if rows else 0
            
            win_rate = wins / total_trades if total_trades > 0 else 0
            avg_win_val = sum(avg_wins) / len(avg_wins) if avg_wins else 0
            avg_loss_val = sum(avg_losses) / len(avg_losses) if avg_losses else 0
            
            # Build signal type distribution
            signal_types = {row[7]: row[0] for row in rows}
            
            return PairModelStats(
                pair=pair,
                model_name=model_name,
                gpu=gpu,
                total_trades=total_trades,
                wins=wins,
                losses=losses,
                win_rate=win_rate,
                avg_win=avg_win_val,
                avg_loss=avg_loss_val,
                total_pnl=total_pnl,
                confidence_avg=avg_confidence,
                signal_types=signal_types
            )
        
        except Exception as e:
            logger.error(f"Failed to calculate stats for {pair}+{model_name}: {e}")
            return PairModelStats(pair=pair, model_name=model_name, gpu=gpu)
    
    def load_specialization(self) -> Dict:
        """Load specialization file."""
        return self.writer.read_json_safe(self.spec_file, {})
    
    def save_specialization(self, spec: Dict) -> bool:
        """Save specialization atomically."""
        return self.writer.write_json(self.spec_file, spec)
    
    def scan_pair_model_combinations(self, tier: str = "shadow") -> Dict:
        """
        Scan all pair+model combinations and identify specialists.
        
        Returns:
        {
          "pairs": {
            "BTC/USD": {
              "specialists": ["model_a (gpu_0): 75% WR"],
              "avoid": ["model_b: 30% WR"],
              "balanced": ["model_c: 55% WR"]
            }
          },
          "models": {
            "model_a": {
              "strong_pairs": ["BTC/USD", "ETH/USD"],
              "weak_pairs": ["XRP/USD"]
            }
          }
        }
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get unique pairs and signal_types in tier
            cursor.execute("""
                SELECT DISTINCT pair FROM trades WHERE tier = ?
            """, (tier,))
            
            pairs = {row[0] for row in cursor.fetchall()}
            conn.close()
            
            if not pairs:
                logger.warning(f"No trades found for tier {tier}")
                return {"pairs": {}, "models": {}, "summary": {}}
            
            # Analyze each pair
            pair_specialists = {}
            model_specialists = defaultdict(lambda: {"strong": [], "weak": []})
            
            try:
                with FileLocker(self.spec_file, timeout=5.0):
                    spec = self.load_specialization()
                    
                    for pair in pairs:
                        # Placeholder: get stats for representative models
                        # In production, would iterate over actual model pool
                        
                        stats = self.calculate_pair_model_stats(pair, "placeholder", "gpu_0", tier)
                        
                        specialists = []
                        weak = []
                        
                        if stats.total_trades >= 20:
                            if stats.win_rate >= 0.65:
                                specialists.append(f"{pair}: {stats.win_rate:.1%} WR ({stats.wins}/{stats.total_trades})")
                                model_specialists["placeholder"]["strong"].append(pair)
                            elif stats.win_rate < 0.45:
                                weak.append(f"{pair}: {stats.win_rate:.1%} WR")
                                model_specialists["placeholder"]["weak"].append(pair)
                        
                        pair_specialists[pair] = {
                            "specialists": specialists,
                            "weak": weak,
                            "total_trades": stats.total_trades,
                            "win_rate": stats.win_rate
                        }
                    
                    # Store in specialization file
                    spec["analysis_date"] = __import__('datetime').datetime.utcnow().isoformat()
                    spec["pair_specialists"] = pair_specialists
                    spec["model_specialists"] = dict(model_specialists)
                    
                    self.save_specialization(spec)
                    
                    return {
                        "pairs": pair_specialists,
                        "models": dict(model_specialists),
                        "summary": {
                            "pairs_analyzed": len(pairs),
                            "specialists_found": sum(1 for p in pair_specialists.values() if p["specialists"])
                        }
                    }
            
            except TimeoutError:
                logger.error("Could not acquire lock for specialization file")
                return {"error": "timeout"}
        
        except Exception as e:
            logger.error(f"Specialization scan failed: {e}")
            return {"error": str(e)}
    
    def get_best_model_for_pair(self, pair: str, candidates: List[str]) -> Optional[str]:
        """
        Get best model for a specific pair from candidates.
        
        Returns model name or None
        """
        try:
            spec = self.load_specialization()
            
            pair_data = spec.get("pair_specialists", {}).get(pair, {})
            if not pair_data:
                return None
            
            specialists = pair_data.get("specialists", [])
            for candidate in candidates:
                if any(candidate in s for s in specialists):
                    return candidate
            
            return None
        
        except Exception as e:
            logger.error(f"Failed to get best model for {pair}: {e}")
            return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    engine = ModelSpecializationEngine("/tmp/trades.db")
    result = engine.scan_pair_model_combinations()
    
    print(f"Specialization scan: {result.get('summary', {})}")
