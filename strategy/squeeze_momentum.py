import pandas as pd
import numpy as np
from strategy.base import BaseStrategy

class SqueezeMomentumScalpStrategy(BaseStrategy):
    """
    LazyBear Squeeze Momentum Scalp Strategy.
    Detects Volatility Squeeze (Bollinger Bands contract inside Keltner Channel).
    Fires LONG when Squeeze releases with positive momentum expansion.
    Fires SHORT when Squeeze releases with negative momentum expansion.
    """
    def __init__(self, bb_period=20, bb_std=2.0, kc_period=20, kc_mult=1.5, mom_period=12, **kwargs):
        super().__init__(name="Squeeze_Momentum_Scalp")
        self.bb_period = int(bb_period)
        self.bb_std = float(bb_std)
        self.kc_period = int(kc_period)
        self.kc_mult = float(kc_mult)
        self.mom_period = int(mom_period)

    def generate_signal(self, df: pd.DataFrame) -> str:
        needed_len = max(self.bb_period, self.kc_period, self.mom_period) + 15
        if len(df) < needed_len:
            return "HOLD"
            
        close = df['close']
        high = df['high']
        low = df['low']
        
        # 1. Bollinger Bands
        sma = close.rolling(window=self.bb_period).mean()
        std = close.rolling(window=self.bb_period).std()
        bb_upper = sma + (std * self.bb_std)
        bb_lower = sma - (std * self.bb_std)
        
        # 2. Keltner Channel
        kc_sma = close.rolling(window=self.kc_period).mean()
        atr = self.calculate_atr(df, period=self.kc_period)
        kc_upper = kc_sma + (atr * self.kc_mult)
        kc_lower = kc_sma - (atr * self.kc_mult)
        
        # Squeeze On = BB inside KC
        squeeze_on = (bb_upper < kc_upper) & (bb_lower > kc_lower)
        squeeze_off = ~squeeze_on
        
        # 3. Linear Regression Momentum
        highest = high.rolling(window=self.kc_period).max()
        lowest = low.rolling(window=self.kc_period).min()
        midline = (highest + lowest) / 2.0
        val = close - (midline + kc_sma) / 2.0
        
        curr_squeeze_off = squeeze_off.iloc[-1]
        prev_squeeze_off = squeeze_off.iloc[-2]
        
        curr_mom = val.iloc[-1]
        prev_mom = val.iloc[-2]
        
        # Signal condition: Squeeze just released or momentum expanding outside squeeze
        if curr_mom > 0 and curr_mom > prev_mom and curr_squeeze_off:
            return "BUY"
        elif curr_mom < 0 and curr_mom < prev_mom and curr_squeeze_off:
            return "SELL"
            
        return "HOLD"
