from strategy.base import BaseStrategy
from strategy.ema_rsi import EMARSIStrategy
from strategy.bollinger_bands import BollingerBandsStrategy
from strategy.stoch_rsi import StochasticRSIStrategy
from strategy.supertrend import SuperTrendStrategy
from strategy.volume_delta import VolumeDeltaScalpStrategy
from strategy.ema_ribbon import EMARibbonScalpStrategy
from strategy.keltner_channel import KeltnerChannelBreakoutStrategy
from strategy.hull_ma import HullMAScalpStrategy
from strategy.rsi_divergence import RSIDivergenceScalpStrategy
from strategy.engulfing_volume import EngulfingVolumeScalpStrategy
from strategy.macd_zerocross import MACDZeroCrossScalpStrategy
from strategy.squeeze_momentum import SqueezeMomentumScalpStrategy

STRATEGY_MAP = {
    "EMA_Ribbon_Scalp": EMARibbonScalpStrategy,
    "Squeeze_Momentum_Scalp": SqueezeMomentumScalpStrategy,
    "Volume_Delta_Scalp": VolumeDeltaScalpStrategy,
    "Engulfing_Volume_Scalp": EngulfingVolumeScalpStrategy,
    "RSI_Divergence_Scalp": RSIDivergenceScalpStrategy,
    "Bollinger_Bands": BollingerBandsStrategy,
    "MACD_ZeroCross_Scalp": MACDZeroCrossScalpStrategy,
    "Stochastic_RSI": StochasticRSIStrategy,
    "SuperTrend": SuperTrendStrategy,
    "EMA_RSI_Crossover": EMARSIStrategy,
    "Keltner_Channel_Breakout": KeltnerChannelBreakoutStrategy,
    "Hull_MA_Scalp": HullMAScalpStrategy
}

def get_strategy(strategy_name: str, parameters: dict = None) -> BaseStrategy:
    """Strategy factory to return strategy instances."""
    if strategy_name not in STRATEGY_MAP:
        raise ValueError(f"Strategy {strategy_name} is not registered.")
        
    strategy_class = STRATEGY_MAP[strategy_name]
    params = parameters or {}
    
    # Filter parameters to only pass what the constructor expects
    import inspect
    sig = inspect.signature(strategy_class.__init__)
    valid_keys = [p for p in sig.parameters.keys() if p != 'self']
    filtered_params = {k: v for k, v in params.items() if k in valid_keys}
    
    return strategy_class(**filtered_params)
