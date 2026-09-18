import pandas as pd
from strategy.base import BaseStrategy

class MACDZeroCrossScalpStrategy(BaseStrategy):
    """
    Fast MACD Zero-Line Crossover Scalp Strategy.
    Uses ultra-fast MACD settings (3, 10, 16) to capture rapid momentum acceleration
    when the MACD line crosses the zero equilibrium threshold.
    """
    def __init__(self, fast=3, slow=10, signal=16, **kwargs):
        super().__init__(name="MACD_ZeroCross_Scalp")
        self.fast = int(fast)
        self.slow = int(slow)
        self.signal = int(signal)

    def generate_signal(self, df: pd.DataFrame) -> str:
        max_p = max(self.fast, self.slow, self.signal)
        if len(df) < max_p + 3:
            return "HOLD"

        close = df['close']
        
        ema_fast = close.ewm(span=self.fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        
        curr_macd = macd_line.iloc[-1]
        prev_macd = macd_line.iloc[-2]
        
        atr_ratio = self.get_atr_ratio(df)
        
        # Block signals during extreme low volatility consolidation
        if atr_ratio < 0.6:
            return "HOLD"
            
        # Bullish zero cross: MACD line crosses above 0
        bullish_zerocross = (prev_macd <= 0) and (curr_macd > 0)
        # Bearish zero cross: MACD line crosses below 0
        bearish_zerocross = (prev_macd >= 0) and (curr_macd < 0)
        
        if bullish_zerocross:
            return "BUY"
        elif bearish_zerocross:
            return "SELL"
            
        return "HOLD"
