"""
Timeframe-Aware Gating System
Different rules for 5m, 1h, 4h, 1d timeframes
"""

import logging
from enum import Enum
from typing import Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class Timeframe(Enum):
    """Supported timeframes."""
    M5 = "5m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


@dataclass
class TimeframeConfig:
    """Configuration for a timeframe."""
    timeframe: Timeframe
    
    # Confidence gates (must be >= these values)
    rsi_gate: float  # RSI_OVERSOLD/OVERBOUGHT gate
    crossover_gate: float  # SMA crossover gate
    breakout_gate: float  # Breakout gate
    
    # Position management
    max_position_size: float  # % of portfolio
    max_hold_minutes: int  # Max trade duration
    take_profit_percent: float  # TP % from entry
    stop_loss_percent: float  # SL % from entry
    
    # Signal requirements
    min_historical_trades: int  # Min sample size
    min_win_rate: float  # Min required win rate
    
    # Volatility adjustment
    apply_volatility_scaling: bool  # Scale position size by ATR
    volatility_threshold: float  # ATR threshold for position scaling


class TimeframeGater:
    """
    Apply timeframe-specific rules.
    
    Rationale:
    - 5m: High-frequency, needs high confidence + tight SL
    - 1h: Sweet spot, balanced confidence + medium SL
    - 4h: Trend-following, lower confidence needed + wider SL
    - 1d: Swing trading, very wide SL + large position size
    """
    
    # Predefined configurations
    CONFIGS = {
        Timeframe.M5: TimeframeConfig(
            timeframe=Timeframe.M5,
            rsi_gate=0.65,  # High confidence for fast trades
            crossover_gate=0.62,
            breakout_gate=0.60,
            max_position_size=0.02,  # 2% per trade (micro)
            max_hold_minutes=30,
            take_profit_percent=0.015,  # 1.5% TP
            stop_loss_percent=0.010,  # 1% SL
            min_historical_trades=50,
            min_win_rate=0.55,
            apply_volatility_scaling=True,
            volatility_threshold=0.02
        ),
        Timeframe.H1: TimeframeConfig(
            timeframe=Timeframe.H1,
            rsi_gate=0.58,  # Moderate confidence
            crossover_gate=0.55,
            breakout_gate=0.52,
            max_position_size=0.05,  # 5% per trade
            max_hold_minutes=120,
            take_profit_percent=0.025,  # 2.5% TP
            stop_loss_percent=0.015,  # 1.5% SL
            min_historical_trades=30,
            min_win_rate=0.52,
            apply_volatility_scaling=True,
            volatility_threshold=0.03
        ),
        Timeframe.H4: TimeframeConfig(
            timeframe=Timeframe.H4,
            rsi_gate=0.52,  # Lower confidence OK for trend
            crossover_gate=0.50,
            breakout_gate=0.48,
            max_position_size=0.10,  # 10% per trade
            max_hold_minutes=480,  # 8 hours
            take_profit_percent=0.040,  # 4% TP
            stop_loss_percent=0.025,  # 2.5% SL
            min_historical_trades=20,
            min_win_rate=0.50,
            apply_volatility_scaling=True,
            volatility_threshold=0.04
        ),
        Timeframe.D1: TimeframeConfig(
            timeframe=Timeframe.D1,
            rsi_gate=0.50,  # Even lower, swing patterns matter
            crossover_gate=0.48,
            breakout_gate=0.45,
            max_position_size=0.20,  # 20% per trade
            max_hold_minutes=1440,  # 24 hours
            take_profit_percent=0.060,  # 6% TP
            stop_loss_percent=0.035,  # 3.5% SL
            min_historical_trades=15,
            min_win_rate=0.50,
            apply_volatility_scaling=False,  # Less sensitive to daily ATR
            volatility_threshold=0.05
        )
    }
    
    def __init__(self):
        pass
    
    def get_config(self, timeframe: Timeframe) -> TimeframeConfig:
        """Get configuration for timeframe."""
        return self.CONFIGS.get(timeframe)
    
    def should_gate_signal(self, signal_type: str, confidence: float,
                          timeframe: Timeframe, signal_win_rate: Optional[float] = None) -> bool:
        """
        Apply timeframe gate to signal.
        
        Returns: True if signal passes, False if gated
        """
        config = self.get_config(timeframe)
        if not config:
            logger.error(f"Unknown timeframe: {timeframe}")
            return False
        
        # Get appropriate confidence gate
        if signal_type in ["RSI_OVERSOLD", "RSI_OVERBOUGHT"]:
            required_conf = config.rsi_gate
        elif signal_type in ["SMA_CROSSOVER_BULLISH", "SMA_CROSSOVER_BEARISH"]:
            required_conf = config.crossover_gate
        elif signal_type in ["BREAKOUT_HIGH", "BREAKOUT_LOW"]:
            required_conf = config.breakout_gate
        else:
            required_conf = config.rsi_gate  # Default
        
        # Check confidence gate
        if confidence < required_conf:
            logger.debug(f"{signal_type} gated: {confidence:.2%} < {required_conf:.2%} ({timeframe.value})")
            return False
        
        # Check historical win rate gate
        if signal_win_rate and signal_win_rate < config.min_win_rate:
            logger.debug(f"{signal_type} gated: {signal_win_rate:.1%} WR < {config.min_win_rate:.1%}")
            return False
        
        logger.debug(f"{signal_type} passed gate ({timeframe.value})")
        return True
    
    def calculate_position_size(self, portfolio_size: float, timeframe: Timeframe,
                               atr: Optional[float] = None, 
                               current_atr_avg: Optional[float] = None) -> float:
        """
        Calculate position size based on timeframe and volatility.
        
        Args:
            portfolio_size: Total portfolio in USD
            timeframe: Trading timeframe
            atr: Current ATR value
            current_atr_avg: Average ATR (for scaling)
        
        Returns: Position size in USD
        """
        config = self.get_config(timeframe)
        if not config:
            return 0.0
        
        base_size = portfolio_size * config.max_position_size
        
        # Apply volatility scaling if enabled
        if config.apply_volatility_scaling and atr and current_atr_avg:
            volatility_ratio = atr / current_atr_avg if current_atr_avg > 0 else 1.0
            
            # Scale down position size in high volatility
            if volatility_ratio > config.volatility_threshold:
                scale = config.volatility_threshold / volatility_ratio
                base_size = base_size * scale
                logger.info(f"Volatility scaling: {volatility_ratio:.2f}x ATR, size={base_size:.0f}")
        
        return base_size
    
    def get_trade_parameters(self, timeframe: Timeframe, entry_price: float) -> Dict:
        """
        Get trade parameters (TP/SL) for a timeframe.
        
        Args:
            timeframe: Trading timeframe
            entry_price: Entry price
        
        Returns:
        {
          "take_profit": 41000,
          "stop_loss": 39600,
          "max_hold_minutes": 120
        }
        """
        config = self.get_config(timeframe)
        if not config:
            return {}
        
        take_profit = entry_price * (1 + config.take_profit_percent)
        stop_loss = entry_price * (1 - config.stop_loss_percent)
        
        return {
            "take_profit": round(take_profit, 2),
            "stop_loss": round(stop_loss, 2),
            "max_hold_minutes": config.max_hold_minutes,
            "timeframe": timeframe.value
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    gater = TimeframeGater()
    
    # Test 1h signal with 0.60 confidence
    fire = gater.should_gate_signal("RSI_OVERSOLD", 0.60, Timeframe.H1)
    print(f"1h RSI_OVERSOLD @ 0.60: {fire}")
    
    # Test position sizing
    size = gater.calculate_position_size(10000, Timeframe.H1, atr=0.05, current_atr_avg=0.04)
    print(f"Position size (1h): ${size:.0f}")
    
    # Test trade parameters
    params = gater.get_trade_parameters(Timeframe.H1, 40000)
    print(f"Trade params: TP={params['take_profit']}, SL={params['stop_loss']}")
