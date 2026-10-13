"""
Stress Factor Decay Engine — Kraken Bot v12 Days 4-5
Decays shadow stress factor by 5%/week if no regressions detected.
Maintains version history with atomic writes for recovery.
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class StressFactorVersion:
    """A historical snapshot of stress factor state."""
    
    def __init__(self,
                 version_id: int,
                 global_factor: float,
                 per_pair: Dict[str, float],
                 decay_applied: bool,
                 regressions_detected: int,
                 reason: str = ""):
        self.version_id = version_id
        self.global_factor = global_factor
        self.per_pair = per_pair.copy()
        self.decay_applied = decay_applied
        self.regressions_detected = regressions_detected
        self.reason = reason
        self.timestamp = datetime.utcnow().isoformat()
    
    def to_dict(self) -> Dict:
        return {
            "version_id": self.version_id,
            "global_factor": self.global_factor,
            "per_pair": self.per_pair,
            "decay_applied": self.decay_applied,
            "regressions_detected": regressions_detected,
            "reason": self.reason,
            "timestamp": self.timestamp
        }


class StressFactorDecay:
    """
    Manage shadow stress factor with weekly decay.
    
    Stress factor multiplies slippage/spreads in shadow simulation.
    - Starts at 1.0 (baseline)
    - Increases when regressions detected (shadow was optimistic)
    - Decays 5% per week if no regressions (converges toward 1.0)
    
    History tracks all versions for rollback and analysis.
    """
    
    DECAY_RATE_WEEKLY = 0.05  # 5% per week
    MIN_STRESS_FACTOR = 1.0
    MAX_STRESS_FACTOR = 2.0
    HISTORY_RETENTION = 52  # 1 year of weekly versions
    
    def __init__(self,
                 stress_path: str = "/dev/shm/cephalobot/shadow_stress_factor.json",
                 history_path: str = "/dev/shm/cephalobot/stress_factor_history.json"):
        """
        Initialize stress factor decay engine.
        
        Args:
            stress_path: Current stress factor config
            history_path: Version history with all snapshots
        """
        self.stress_path = stress_path
        self.history_path = history_path
        self.load_state()
    
    def load_state(self) -> None:
        """Load current state and history."""
        # Load current stress config
        if os.path.exists(self.stress_path):
            try:
                with open(self.stress_path, 'r') as f:
                    self.current = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.current = self._default_stress_config()
        else:
            self.current = self._default_stress_config()
        
        # Load history
        if os.path.exists(self.history_path):
            try:
                with open(self.history_path, 'r') as f:
                    self.history = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.history = {"versions": []}
        else:
            self.history = {"versions": []}
    
    def _default_stress_config(self) -> Dict:
        """Create default stress config."""
        return {
            "global_factor": 1.0,
            "per_pair": {},
            "last_updated": datetime.utcnow().isoformat(),
            "last_decay": datetime.utcnow().isoformat()
        }
    
    def save_state(self) -> bool:
        """
        Persist current state atomically.
        Returns True if successful.
        """
        try:
            # Create parent directories
            Path(self.stress_path).parent.mkdir(parents=True, exist_ok=True)
            Path(self.history_path).parent.mkdir(parents=True, exist_ok=True)
            
            # Write stress config (atomic: temp + rename)
            temp_stress = f"{self.stress_path}.tmp"
            with open(temp_stress, 'w') as f:
                json.dump(self.current, f, indent=2)
            
            # Validate
            with open(temp_stress, 'r') as f:
                json.load(f)
            
            os.rename(temp_stress, self.stress_path)
            
            # Write history (atomic: temp + rename)
            temp_history = f"{self.history_path}.tmp"
            with open(temp_history, 'w') as f:
                json.dump(self.history, f, indent=2)
            
            # Validate
            with open(temp_history, 'r') as f:
                json.load(f)
            
            os.rename(temp_history, self.history_path)
            
            logger.info(f"Saved stress state: global={self.current['global_factor']:.2f}")
            return True
        
        except Exception as e:
            logger.error(f"Failed to save stress state: {e}")
            return False
    
    def record_version(self,
                      decay_applied: bool,
                      regressions_detected: int,
                      reason: str = "") -> int:
        """
        Record current state as a historical version.
        
        Args:
            decay_applied: Whether decay was applied this cycle
            regressions_detected: Number of regressions found
            reason: Human-readable reason for this snapshot
        
        Returns:
            New version ID
        """
        version_id = len(self.history['versions'])
        
        version = {
            "version_id": version_id,
            "global_factor": self.current['global_factor'],
            "per_pair": self.current['per_pair'].copy(),
            "decay_applied": decay_applied,
            "regressions_detected": regressions_detected,
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        self.history['versions'].append(version)
        
        # Keep only last N versions
        if len(self.history['versions']) > self.HISTORY_RETENTION:
            self.history['versions'] = self.history['versions'][-self.HISTORY_RETENTION:]
        
        logger.info(f"Recorded version {version_id}: {reason}")
        return version_id
    
    def get_days_since_last_decay(self) -> float:
        """Calculate days since last decay operation."""
        last_decay_str = self.current.get('last_decay', self.current.get('last_updated'))
        last_decay = datetime.fromisoformat(last_decay_str)
        days_elapsed = (datetime.utcnow() - last_decay).total_seconds() / 86400
        return days_elapsed
    
    def apply_decay(self, regressions_detected: int = 0) -> bool:
        """
        Apply weekly decay to stress factor if conditions met.
        
        Decay is applied if:
        - At least 6 days have passed since last decay
        - No regressions detected in the period
        
        Decay formula: new_factor = old_factor - (old_factor - 1.0) * DECAY_RATE
        This moves stress_factor 5% of the way toward 1.0 each week.
        
        Args:
            regressions_detected: Number of regressions in this period
        
        Returns:
            True if decay was applied, False otherwise
        """
        days_elapsed = self.get_days_since_last_decay()
        
        if days_elapsed < 6.0:
            logger.info(f"Decay not ready: only {days_elapsed:.1f} days elapsed (need 6)")
            return False
        
        if regressions_detected > 0:
            logger.info(f"Decay skipped: {regressions_detected} regressions detected")
            self.current['last_decay'] = datetime.utcnow().isoformat()
            return False
        
        # Apply decay: move 5% of the way toward 1.0
        old_global = self.current['global_factor']
        distance_from_baseline = old_global - self.MIN_STRESS_FACTOR
        decay_amount = distance_from_baseline * self.DECAY_RATE_WEEKLY
        new_global = max(self.MIN_STRESS_FACTOR, old_global - decay_amount)
        
        self.current['global_factor'] = new_global
        
        # Per-pair decay
        for pair in self.current['per_pair']:
            old_pair = self.current['per_pair'][pair]
            distance = old_pair - self.MIN_STRESS_FACTOR
            decay = distance * self.DECAY_RATE_WEEKLY
            self.current['per_pair'][pair] = max(self.MIN_STRESS_FACTOR, old_pair - decay)
        
        self.current['last_decay'] = datetime.utcnow().isoformat()
        self.current['last_updated'] = datetime.utcnow().isoformat()
        
        # Record version
        reason = f"Weekly decay: {days_elapsed:.1f}d elapsed, 0 regressions"
        self.record_version(
            decay_applied=True,
            regressions_detected=0,
            reason=reason
        )
        
        logger.info(f"Decay applied: global {old_global:.3f}→{new_global:.3f}, per-pair updated")
        return True
    
    def increase_stress(self,
                       pair: Optional[str] = None,
                       increase_amount: float = 0.1) -> None:
        """
        Increase stress factor when regression detected.
        
        Args:
            pair: If specified, increase per-pair factor; else global
            increase_amount: How much to increase (0.1 = +10%)
        """
        if pair is None:
            # Global increase
            old = self.current['global_factor']
            new = min(self.MAX_STRESS_FACTOR, old + increase_amount)
            self.current['global_factor'] = new
            logger.warning(f"Global stress increased: {old:.3f}→{new:.3f}")
            
            reason = f"Regression detected: global stress +{increase_amount:.1%}"
            self.record_version(
                decay_applied=False,
                regressions_detected=1,
                reason=reason
            )
        else:
            # Per-pair increase
            if pair not in self.current['per_pair']:
                self.current['per_pair'][pair] = self.MIN_STRESS_FACTOR
            
            old = self.current['per_pair'][pair]
            new = min(self.MAX_STRESS_FACTOR, old + increase_amount)
            self.current['per_pair'][pair] = new
            logger.warning(f"Stress increased for {pair}: {old:.3f}→{new:.3f}")
            
            reason = f"Regression on {pair}: per-pair stress +{increase_amount:.1%}"
            self.record_version(
                decay_applied=False,
                regressions_detected=1,
                reason=reason
            )
        
        self.current['last_updated'] = datetime.utcnow().isoformat()
    
    def reset_to_version(self, version_id: int) -> bool:
        """
        Rollback to a previous version (for error recovery).
        
        Args:
            version_id: Version to restore
        
        Returns:
            True if successful
        """
        matching = [v for v in self.history['versions'] if v['version_id'] == version_id]
        
        if not matching:
            logger.error(f"Version {version_id} not found in history")
            return False
        
        version = matching[0]
        
        self.current['global_factor'] = version['global_factor']
        self.current['per_pair'] = version['per_pair'].copy()
        self.current['last_updated'] = datetime.utcnow().isoformat()
        
        logger.warning(f"Rolled back to version {version_id}: {version['reason']}")
        return True
    
    def get_history_summary(self, limit: int = 10) -> List[Dict]:
        """
        Get recent version history for inspection.
        
        Args:
            limit: Max versions to return
        
        Returns:
            List of recent versions
        """
        recent = self.history['versions'][-limit:]
        return [{
            "version_id": v['version_id'],
            "global_factor": v['global_factor'],
            "decay_applied": v['decay_applied'],
            "regressions_detected": v['regressions_detected'],
            "reason": v['reason'],
            "timestamp": v['timestamp']
        } for v in recent]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    decay = StressFactorDecay()
    
    # Example: apply decay with no regressions
    decay_applied = decay.apply_decay(regressions_detected=0)
    
    print(f"Decay applied: {decay_applied}")
    print(f"Current stress: {decay.current['global_factor']:.3f}")
    print("\nRecent history:")
    for v in decay.get_history_summary():
        print(f"  v{v['version_id']}: {v['global_factor']:.3f} - {v['reason']}")
    
    # Save state
    decay.save_state()
    print("\n✓ Stress factor state saved")
