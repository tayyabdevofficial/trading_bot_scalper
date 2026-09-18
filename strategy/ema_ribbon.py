import pandas as pd
from strategy.base import BaseStrategy

class EMARibbonScalpStrategy(BaseStrategy):
    """
    EMA Ribbon Momentum Scalp Strategy.
    Uses 4 fast EMAs (5, 8, 13, 21). Signals aggressive trend entries when EMAs align sequentially
    in order of magnitude with expanding ribbon spread.
    """
    def __init__(self, fast_ema=5, med_ema=8, slow_ema=13, base_ema=21, **kwargs):
        super().__init__(name="EMA_Ribbon_Scalp")
        self.fast_period = int(fast_ema)
        self.med_period = int(med_ema)
        self.slow_period = int(slow_ema)
        self.base_period = int(base_ema)

    def generate_signal(self, df: pd.DataFrame) -> str:
        max_period = max(self.fast_period, self.med_period, self.slow_period, self.base_period)
        if len(df) < max_period + 3:
            return "HOLD"

        close = df['close']
        
        ema_fast = close.ewm(span=self.fast_period, adjust=False).mean()
        ema_med = close.ewm(span=self.med_period, adjust=False).mean()
        ema_slow = close.ewm(span=self.slow_period, adjust=False).mean()
        ema_base = close.ewm(span=self.base_period, adjust=False).mean()
        
        curr_f, prev_f = ema_fast.iloc[-1], ema_fast.iloc[-2]
        curr_m, prev_m = ema_med.iloc[-1], ema_med.iloc[-2]
        curr_s, prev_s = ema_slow.iloc[-1], ema_slow.iloc[-2]
        curr_b, prev_b = ema_base.iloc[-1], ema_base.iloc[-2]
        
        # Bullish alignment: fast > med > slow > base
        bullish_alignment = (curr_f > curr_m > curr_s > curr_b)
        # Bearish alignment: fast < med < slow < base
        bearish_alignment = (curr_f < curr_m < curr_s < curr_b)
        
        # Ribbon spread expansion (current vs previous bar)
        curr_spread = abs(curr_f - curr_b)
        prev_spread = abs(prev_f - prev_b)
        is_expanding = curr_spread > prev_spread
        
        # Volatility check
        atr_ratio = self.get_atr_ratio(df)
        
        if bullish_alignment and is_expanding:
            # Avoid buying if extreme overextension in high volatility
            if atr_ratio > 1.5 and curr_f > curr_b * 1.05:
                return "HOLD"
            return "BUY"
            
        elif bearish_alignment and is_expanding:
            if atr_ratio > 1.5 and curr_f < curr_b * 0.95:
                return "HOLD"
            return "SELL"
            
        return "HOLD"
