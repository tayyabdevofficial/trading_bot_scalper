import numpy as np
import pandas as pd
from strategy.base import BaseStrategy

class HullMAScalpStrategy(BaseStrategy):
    """
    Hull Moving Average (HMA) Ultra-Low Lag Scalp Strategy.
    HMA eliminates lag while enhancing smoothness.
    Enters on HMA direction flips confirmed by RSI momentum.
    """
    def __init__(self, period=9, rsi_period=14, **kwargs):
        super().__init__(name="Hull_MA_Scalp")
        self.period = int(period)
        self.rsi_period = int(rsi_period)

    @staticmethod
    def _wma(series: pd.Series, period: int) -> pd.Series:
        weights = np.arange(1, period + 1)
        return series.rolling(period).apply(lambda s: np.dot(s, weights) / weights.sum(), raw=True)

    def calculate_hma(self, series: pd.Series, period: int) -> pd.Series:
        half_period = int(period / 2)
        sqrt_period = int(np.sqrt(period))
        
        wma_half = self._wma(series, half_period)
        wma_full = self._wma(series, period)
        
        diff = 2 * wma_half - wma_full
        hma = self._wma(diff, sqrt_period)
        return hma

    def generate_signal(self, df: pd.DataFrame) -> str:
        needed_lines = self.period + int(np.sqrt(self.period)) + 5
        if len(df) < needed_lines:
            return "HOLD"

        close = df['close']
        hma = self.calculate_hma(close, self.period)
        rsi = self.calculate_rsi(close, self.rsi_period)
        
        curr_hma = hma.iloc[-1]
        prev_hma = hma.iloc[-2]
        prev2_hma = hma.iloc[-3]
        
        curr_rsi = rsi.iloc[-1]
        
        # Bullish flip: HMA was sloping down or flat, now sloping up
        bullish_flip = (prev_hma <= prev2_hma) and (curr_hma > prev_hma)
        # Bearish flip: HMA was sloping up or flat, now sloping down
        bearish_flip = (prev_hma >= prev2_hma) and (curr_hma < prev_hma)
        
        atr_ratio = self.get_atr_ratio(df)
        
        if bullish_flip and curr_rsi > 48:
            if atr_ratio > 1.6 and curr_rsi > 70:  # Avoid chasing extreme overbought in volatile market
                return "HOLD"
            return "BUY"
        elif bearish_flip and curr_rsi < 52:
            if atr_ratio > 1.6 and curr_rsi < 30:  # Avoid chasing extreme oversold in volatile market
                return "HOLD"
            return "SELL"
            
        return "HOLD"
