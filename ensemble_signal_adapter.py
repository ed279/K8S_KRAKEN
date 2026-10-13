"""
Ensemble Signal Adapter v2
- Atomic file writes (no race conditions)
- Real signal generation integration
- Per-GPU model specialization tracking
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime

from atomic_file_utils import AtomicFileWriter, FileLocker
from signal_engine import SignalGenerator, Candle, SignalType

logger = logging.getLogger(__name__)


@dataclass
class ModelPerformance:
    """Per-model tracking."""
    model_name: str
    gpu: str
    wins: int = 0
    losses: int = 0
    draws: int = 0
    
    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses + self.draws
        return self.wins / total if total > 0 else 0.0
    
    @property
    def is_bottom_performer(self, threshold: int = 10) -> bool:
        """Model in bottom 2 after 10 consecutive losses."""
        return self.losses >= threshold


class EnsembleSignalAdapter:
    """
    Adapt signals based on GPU model alignment.
    
    Key changes from v1:
    - Atomic file writes prevent corruption
    - Per-GPU specialization tracking
    - Confidence boost/penalize based on alignment
    - Real signal engine integration
    """
    
    def __init__(self, instance_id: str, db_path: str):
        self.instance_id = instance_id
        self.db_path = db_path
        self.base_dir = Path(db_path).parent
        self.perf_file = str(self.base_dir / "model_performance.json")
        self.spec_file = str(self.base_dir / "gpu_specialization.json")
        self.writer = AtomicFileWriter()
        self.signal_gen = SignalGenerator()
    
    def load_model_performance(self) -> Dict[str, ModelPerformance]:
        """Load model performance (with fallback to backup)."""
        data = self.writer.read_json_safe(self.perf_file, {})
        
        result = {}
        for model_name, perf_dict in data.items():
            result[model_name] = ModelPerformance(
                model_name=model_name,
                gpu=perf_dict.get("gpu", "unknown"),
                wins=perf_dict.get("wins", 0),
                losses=perf_dict.get("losses", 0),
                draws=perf_dict.get("draws", 0)
            )
        
        return result
    
    def save_model_performance(self, models: Dict[str, ModelPerformance]) -> bool:
        """Save model performance atomically."""
        data = {
            name: {
                "gpu": m.gpu,
                "wins": m.wins,
                "losses": m.losses,
                "draws": m.draws
            }
            for name, m in models.items()
        }
        
        return self.writer.write_json(self.perf_file, data)
    
    def record_trade_result(self, model_name: str, gpu: str, 
                          signal_type: str, won: bool) -> None:
        """Record a trade result for model tracking."""
        try:
            with FileLocker(self.perf_file, timeout=5.0):
                models = self.load_model_performance()
                
                if model_name not in models:
                    models[model_name] = ModelPerformance(model_name, gpu)
                
                if won:
                    models[model_name].wins += 1
                else:
                    models[model_name].losses += 1
                
                self.save_model_performance(models)
                logger.info(f"Recorded {model_name} on {gpu}: W={models[model_name].wins} L={models[model_name].losses}")
        
        except TimeoutError:
            logger.error(f"Could not acquire lock for {self.perf_file}")
        except Exception as e:
            logger.error(f"Failed to record trade result: {e}")
    
    def calculate_gpu_alignment(self, gpu: str) -> Tuple[float, int]:
        """
        Calculate alignment score for GPU (0-1).
        
        Returns: (alignment_score, total_trades)
        - score > 0.65: good agreement among models
        - score < 0.55: models diverging
        """
        models = self.load_model_performance()
        gpu_models = [m for m in models.values() if m.gpu == gpu]
        
        if not gpu_models:
            return 0.5, 0
        
        total_trades = sum(m.wins + m.losses for m in gpu_models)
        if total_trades == 0:
            return 0.5, 0
        
        # High win rate means good agreement
        avg_win_rate = sum(m.win_rate for m in gpu_models) / len(gpu_models)
        
        return avg_win_rate, total_trades
    
    def adjust_signal_confidence(self, base_confidence: float, 
                                gpu: str, signal_type: str) -> float:
        """
        Adjust confidence based on GPU model alignment.
        
        - alignment > 0.65: boost +10-15%
        - alignment < 0.55: penalize -10-20%
        - 0.55-0.65: neutral
        """
        alignment, total_trades = self.calculate_gpu_alignment(gpu)
        
        adjusted = base_confidence
        
        if alignment > 0.65:
            boost = 0.10 + (alignment - 0.65) * 0.10
            adjusted = min(0.95, base_confidence + boost)
            logger.debug(f"Boosted {signal_type} from {base_confidence:.2%} to {adjusted:.2%} (alignment={alignment:.2%})")
        
        elif alignment < 0.55:
            penalty = 0.10 + (0.55 - alignment) * 0.10
            adjusted = max(0.25, base_confidence - penalty)
            logger.debug(f"Penalized {signal_type} from {base_confidence:.2%} to {adjusted:.2%} (alignment={alignment:.2%})")
        
        return adjusted
    
    def get_signal_for_pair(self, pair: str, candles: List[Candle], 
                           gpu: str) -> Optional[Dict]:
        """
        Get latest signal for pair (real signal engine).
        
        Returns dict with:
        - signal_type
        - base_confidence
        - adjusted_confidence
        - rsi, atr, fast_sma, slow_sma
        - timestamp
        """
        try:
            signal = self.signal_gen.get_latest_signal(candles)
            if not signal:
                return None
            
            adjusted_conf = self.adjust_signal_confidence(
                signal.base_confidence, gpu, signal.signal_type.value
            )
            
            return {
                "pair": pair,
                "signal_type": signal.signal_type.value,
                "base_confidence": signal.base_confidence,
                "adjusted_confidence": adjusted_conf,
                "price": signal.price,
                "rsi": signal.rsi,
                "atr": signal.atr,
                "fast_sma": signal.fast_sma,
                "slow_sma": signal.slow_sma,
                "timestamp": signal.timestamp,
                "instance_id": self.instance_id
            }
        
        except Exception as e:
            logger.error(f"Failed to get signal for {pair}: {e}")
            return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Test
    adapter = EnsembleSignalAdapter("test-1", "/tmp/trades.db")
    adapter.record_trade_result("model_a", "gpu_0", "RSI_OVERSOLD", True)
    adapter.record_trade_result("model_a", "gpu_0", "RSI_OVERSOLD", False)
    
    alignment, total = adapter.calculate_gpu_alignment("gpu_0")
    print(f"GPU alignment: {alignment:.2%} ({total} trades)")
