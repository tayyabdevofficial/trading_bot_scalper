import pandas as pd
from strategy.base import BaseStrategy

class KeltnerChannelBreakoutStrategy(BaseStrategy):
    """
    Keltner Channel Volatility Envelope Breakout Strategy (Enhanced).
    Requires ADX trend strength confirmation (ADX >= 22) and volume/ATR expansion
    to eliminate fake-out breakouts during low-momentum consolidation.
    """
    def __init__(self, ema_period=20, atr_period=14, atr_mult=2.0, min_adx=22.0, **kwargs):
        super().__init__(name="Keltner_Channel_Breakout")
        self.ema_period = int(ema_period)
        self.atr_period = int(atr_period)
        self.atr_mult = float(atr_mult)
        self.min_adx = float(min_adx)

    def generate_signal(self, df: pd.DataFrame) -> str:
        if len(df) < max(self.ema_period, self.atr_period) + 10:
            return "HOLD"

        # Check ADX trend momentum
        adx_series = self.calculate_adx(df)
        curr_adx = adx_series.iloc[-1]
        
        if curr_adx < self.min_adx:
            return "HOLD"

        close = df['close']
        volume = df['volume']
        
        ema = close.ewm(span=self.ema_period, adjust=False).mean()
        atr = self.calculate_atr(df, self.atr_period)
        atr_ratio = self.get_atr_ratio(df)
        
        effective_mult = self.atr_mult * 1.2 if atr_ratio > 1.3 else self.atr_mult
        
        upper_channel = ema + (effective_mult * atr)
        lower_channel = ema - (effective_mult * atr)
        
        curr_close = close.iloc[-1]
        prev_close = close.iloc[-2]
        
        curr_upper = upper_channel.iloc[-1]
        prev_upper = upper_channel.iloc[-2]
        
        curr_lower = lower_channel.iloc[-1]
        prev_lower = lower_channel.iloc[-2]
        
        # Volume expansion filter
        vol_ma = volume.rolling(window=20).mean().iloc[-1]
        curr_vol = volume.iloc[-1]
        has_volume = curr_vol >= (vol_ma * 1.1)
        
        # Breakout piercing logic with volume backing
        if prev_close <= prev_upper and curr_close > curr_upper and has_volume:
            return "BUY"
        elif prev_close >= prev_lower and curr_close < curr_lower and has_volume:
            return "SELL"
            
        return "HOLD"
