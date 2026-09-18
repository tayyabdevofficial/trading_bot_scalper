import pandas as pd
from strategy.base import BaseStrategy

class EngulfingVolumeScalpStrategy(BaseStrategy):
    """
    Engulfing Candlestick + Volume Scalp Strategy.
    Identifies high-probability Bullish/Bearish Engulfing price action setups validated by
    volume surges and candle body size expansion.
    """
    def __init__(self, vol_mult=1.5, vol_period=20, body_mult=1.2, **kwargs):
        super().__init__(name="Engulfing_Volume_Scalp")
        self.vol_mult = float(vol_mult)
        self.vol_period = int(vol_period)
        self.body_mult = float(body_mult)

    def generate_signal(self, df: pd.DataFrame) -> str:
        if len(df) < self.vol_period + 3:
            return "HOLD"

        close = df['close']
        open_p = df['open']
        volume = df['volume']
        
        vol_ma = volume.rolling(window=self.vol_period).mean()
        body_size = (close - open_p).abs()
        avg_body = body_size.rolling(window=self.vol_period).mean()
        
        curr_close, prev_close = close.iloc[-1], close.iloc[-2]
        curr_open, prev_open = open_p.iloc[-1], open_p.iloc[-2]
        curr_vol = volume.iloc[-1]
        curr_vol_ma = vol_ma.iloc[-1]
        curr_body = body_size.iloc[-1]
        curr_avg_body = avg_body.iloc[-1]
        
        atr_ratio = self.get_atr_ratio(df)
        effective_vol_mult = self.vol_mult * 1.2 if atr_ratio > 1.3 else self.vol_mult
        
        has_volume = curr_vol >= (curr_vol_ma * effective_vol_mult)
        has_body_expansion = curr_body >= (curr_avg_body * self.body_mult)
        
        if not (has_volume and has_body_expansion):
            return "HOLD"
            
        # Bullish Engulfing: Prev red, current green engulfing prev body
        is_prev_red = prev_close < prev_open
        is_curr_green = curr_close > curr_open
        bullish_engulfing = is_prev_red and is_curr_green and (curr_close >= prev_open) and (curr_open <= prev_close)
        
        # Bearish Engulfing: Prev green, current red engulfing prev body
        is_prev_green = prev_close > prev_open
        is_curr_red = curr_close < curr_open
        bearish_engulfing = is_prev_green and is_curr_red and (curr_close <= prev_open) and (curr_open >= prev_close)
        
        if bullish_engulfing:
            return "BUY"
        elif bearish_engulfing:
            return "SELL"
            
        return "HOLD"
