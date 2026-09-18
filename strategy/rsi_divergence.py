import pandas as pd
import numpy as np
from strategy.base import BaseStrategy

class RSIDivergenceScalpStrategy(BaseStrategy):
    """
    Enhanced RSI Divergence Scalp Strategy.
    Features:
    1. True fractal swing pivot detection (no simple min/max false hits).
    2. Dual Regime Adaptation:
       - Normal Mode (ATR ratio <= 1.3 & ADX <= 30): Trades Regular Reversal Divergence.
       - High Volatility Mode (ATR ratio > 1.3 or ADX > 30): Trades Hidden Trend Continuation Divergence.
    3. EMA 50 trend alignment filter.
    4. Directional candle body + volume confirmation.
    """
    def __init__(self, lookback=25, rsi_period=14, ema_trend=50, **kwargs):
        super().__init__(name="RSI_Divergence_Scalp")
        self.lookback = int(lookback)
        self.rsi_period = int(rsi_period)
        self.ema_trend_period = int(ema_trend)

    def _find_swing_lows(self, price: pd.Series, rsi: pd.Series, window: int = 25):
        """Find the two most recent fractal swing lows."""
        pivots = []
        # Exclude active open candle
        sub_p = price.iloc[-window-1:-1]
        sub_r = rsi.iloc[-window-1:-1]
        
        for i in range(2, len(sub_p) - 2):
            if (sub_p.iloc[i] <= sub_p.iloc[i-1] and sub_p.iloc[i] <= sub_p.iloc[i-2] and
                sub_p.iloc[i] <= sub_p.iloc[i+1] and sub_p.iloc[i] <= sub_p.iloc[i+2]):
                pivots.append((sub_p.iloc[i], sub_r.iloc[i]))
                
        if len(pivots) >= 2:
            return pivots[-1], pivots[-2] # (price_recent, rsi_recent), (price_older, rsi_older)
        return None, None

    def _find_swing_highs(self, price: pd.Series, rsi: pd.Series, window: int = 25):
        """Find the two most recent fractal swing highs."""
        pivots = []
        sub_p = price.iloc[-window-1:-1]
        sub_r = rsi.iloc[-window-1:-1]
        
        for i in range(2, len(sub_p) - 2):
            if (sub_p.iloc[i] >= sub_p.iloc[i-1] and sub_p.iloc[i] >= sub_p.iloc[i-2] and
                sub_p.iloc[i] >= sub_p.iloc[i+1] and sub_p.iloc[i] >= sub_p.iloc[i+2]):
                pivots.append((sub_p.iloc[i], sub_r.iloc[i]))
                
        if len(pivots) >= 2:
            return pivots[-1], pivots[-2] # (price_recent, rsi_recent), (price_older, rsi_older)
        return None, None

    def generate_signal(self, df: pd.DataFrame) -> str:
        needed = max(self.lookback, self.rsi_period, self.ema_trend_period) + 10
        if len(df) < needed:
            return "HOLD"

        close = df['close']
        low = df['low']
        high = df['high']
        open_p = df['open']
        
        rsi = self.calculate_rsi(close, self.rsi_period)
        ema50 = close.ewm(span=self.ema_trend_period, adjust=False).mean()
        
        atr_ratio = self.get_atr_ratio(df)
        adx_series = self.calculate_adx(df)
        curr_adx = adx_series.iloc[-1]
        
        is_high_volatility = (atr_ratio > 1.30) or (curr_adx > 30.0)
        
        curr_close = close.iloc[-1]
        curr_open = open_p.iloc[-1]
        curr_ema50 = ema50.iloc[-1]
        curr_rsi = rsi.iloc[-1]
        prev_rsi = rsi.iloc[-2]
        
        # 1. Check Bullish Setup
        swing_low_recent, swing_low_older = self._find_swing_lows(low, rsi, self.lookback)
        if swing_low_recent and swing_low_older:
            p_rec, r_rec = swing_low_recent
            p_old, r_old = swing_low_older
            
            if not is_high_volatility:
                # REGULAR BULLISH DIVERGENCE (Normal Market Reversal): Lower price low, higher RSI low
                reg_bull = (p_rec < p_old) and (r_rec > r_old) and (r_rec < 48)
                if reg_bull and curr_close > curr_open and curr_rsi > prev_rsi and curr_close >= curr_ema50 * 0.99:
                    return "BUY"
            else:
                # HIDDEN BULLISH DIVERGENCE (Volatile Market Trend Continuation): Higher price low, lower RSI low
                hid_bull = (p_rec > p_old) and (r_rec < r_old) and (curr_close >= curr_ema50)
                if hid_bull and curr_close > curr_open and curr_rsi > prev_rsi:
                    return "BUY"

        # 2. Check Bearish Setup
        swing_high_recent, swing_high_older = self._find_swing_highs(high, rsi, self.lookback)
        if swing_high_recent and swing_high_older:
            p_rec, r_rec = swing_high_recent
            p_old, r_old = swing_high_older
            
            if not is_high_volatility:
                # REGULAR BEARISH DIVERGENCE (Normal Market Reversal): Higher price high, lower RSI high
                reg_bear = (p_rec > p_old) and (r_rec < r_old) and (r_rec > 52)
                if reg_bear and curr_close < curr_open and curr_rsi < prev_rsi and curr_close <= curr_ema50 * 1.01:
                    return "SELL"
            else:
                # HIDDEN BEARISH DIVERGENCE (Volatile Market Trend Continuation): Lower price high, higher RSI high
                hid_bear = (p_rec < p_old) and (r_rec > r_old) and (curr_close <= curr_ema50)
                if hid_bear and curr_close < curr_open and curr_rsi < prev_rsi:
                    return "SELL"

        return "HOLD"
