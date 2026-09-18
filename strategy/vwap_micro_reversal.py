import numpy as np
import pandas as pd
from strategy.base import BaseStrategy

class VWAPMicroReversalStrategy(BaseStrategy):
    """
    VWAP Micro Reversal Scalp Strategy (Enhanced).
    Features:
    1. Mean reversion beyond 2.5 Std Dev from VWAP.
    2. ADX & ATR Volatility Guard: Automatically BLOCKS fading when ADX > 30 or ATR ratio > 1.4
       to prevent getting run over by strong trends.
    3. Candlestick Pinbar / Wick Rejection pattern confirmation.
    """
    def __init__(self, num_std=2.5, rsi_period=14, adx_max=30.0, **kwargs):
        super().__init__(name="VWAP_Micro_Reversal")
        self.num_std = float(num_std)
        self.rsi_period = int(rsi_period)
        self.adx_max = float(adx_max)

    def generate_signal(self, df: pd.DataFrame) -> str:
        if len(df) < 35:
            return "HOLD"

        # Volatility Guard: Block mean reversion during strong trend explosions
        adx_series = self.calculate_adx(df)
        curr_adx = adx_series.iloc[-1]
        atr_ratio = self.get_atr_ratio(df)
        
        if curr_adx > self.adx_max or atr_ratio > 1.40:
            return "HOLD"

        high = df['high']
        low = df['low']
        close = df['close']
        open_p = df['open']
        volume = df['volume']
        
        # Calculate VWAP
        typical_price = (high + low + close) / 3.0
        tp_vol = typical_price * volume
        cum_tp_vol = tp_vol.cumsum()
        cum_vol = volume.cumsum().replace(0, 1e-10)
        vwap = cum_tp_vol / cum_vol
        
        # Rolling Std Dev from VWAP
        dev = (close - vwap) ** 2
        rolling_std = np.sqrt(dev.rolling(window=30).mean())
        
        upper_band = vwap + (self.num_std * rolling_std)
        lower_band = vwap - (self.num_std * rolling_std)
        
        curr_high = high.iloc[-1]
        curr_low = low.iloc[-1]
        curr_close = close.iloc[-1]
        curr_open = open_p.iloc[-1]
        
        curr_upper = upper_band.iloc[-1]
        curr_lower = lower_band.iloc[-1]
        
        candle_range = max(curr_high - curr_low, 1e-8)
        upper_wick = curr_high - max(curr_close, curr_open)
        lower_wick = min(curr_close, curr_open) - curr_low
        
        rsi = self.calculate_rsi(close, self.rsi_period)
        curr_rsi = rsi.iloc[-1]
        
        # Bullish Reversal: Low pierces lower band & lower wick is >= 35% of candle & RSI < 38 & Green close
        is_bullish_pinbar = (curr_low <= curr_lower) and (lower_wick / candle_range >= 0.35) and (curr_rsi < 38) and (curr_close >= curr_open)
        
        # Bearish Reversal: High pierces upper band & upper wick is >= 35% of candle & RSI > 62 & Red close
        is_bearish_pinbar = (curr_high >= curr_upper) and (upper_wick / candle_range >= 0.35) and (curr_rsi > 62) and (curr_close <= curr_open)
        
        if is_bullish_pinbar:
            return "BUY"
        elif is_bearish_pinbar:
            return "SELL"
            
        return "HOLD"
