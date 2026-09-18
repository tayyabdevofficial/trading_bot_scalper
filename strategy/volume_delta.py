import pandas as pd
from strategy.base import BaseStrategy

class VolumeDeltaScalpStrategy(BaseStrategy):
    """
    Volume & Order Flow Scalp Strategy.
    Fires signals when volume surges beyond moving average with candle body directional momentum.
    Adapts volume multiplier dynamically in high volatility regimes.
    """
    def __init__(self, vol_mult=2.0, vol_period=20, rsi_period=14, **kwargs):
        super().__init__(name="Volume_Delta_Scalp")
        self.vol_mult = float(vol_mult)
        self.vol_period = int(vol_period)
        self.rsi_period = int(rsi_period)

    def generate_signal(self, df: pd.DataFrame) -> str:
        if len(df) < max(self.vol_period, self.rsi_period) + 2:
            return "HOLD"

        df = df.copy()
        volume = df['volume']
        close = df['close']
        open_p = df['open']
        
        # Volume Moving Average
        vol_ma = volume.rolling(window=self.vol_period).mean()
        curr_vol = volume.iloc[-1]
        curr_vol_ma = vol_ma.iloc[-1]
        
        if curr_vol_ma <= 0:
            return "HOLD"
            
        # Dynamic Volatility adaptation
        atr_ratio = self.get_atr_ratio(df)
        effective_mult = self.vol_mult * 1.2 if atr_ratio > 1.3 else self.vol_mult
        
        # RSI direction filter
        rsi = self.calculate_rsi(close, self.rsi_period)
        curr_rsi = rsi.iloc[-1]
        
        curr_close = close.iloc[-1]
        curr_open = open_p.iloc[-1]
        
        is_vol_surge = curr_vol >= (curr_vol_ma * effective_mult)
        
        if is_vol_surge:
            # Bullish surge
            if curr_close > curr_open and curr_rsi > 50:
                return "BUY"
            # Bearish surge
            elif curr_close < curr_open and curr_rsi < 50:
                return "SELL"
                
        return "HOLD"
