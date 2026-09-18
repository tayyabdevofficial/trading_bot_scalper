import pandas as pd
import numpy as np
from strategy.base import BaseStrategy

class SuperTrendStrategy(BaseStrategy):
    def __init__(self, period=10, multiplier=3.0):
        super().__init__(name="SuperTrend")
        self.period = period
        self.multiplier = multiplier

    def generate_signal(self, df: pd.DataFrame) -> str:
        if len(df) < self.period + 5:
            return "HOLD"
            
        df = df.copy()
        high = df['high']
        low = df['low']
        close = df['close']
        
        # Calculate ATR (Average True Range)
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1/self.period, adjust=False).mean()
        
        hl2 = (high + low) / 2
        basic_ub = hl2 + self.multiplier * atr
        basic_lb = hl2 - self.multiplier * atr
        
        final_ub = basic_ub.copy()
        final_lb = basic_lb.copy()
        supertrend = np.zeros(len(df))
        
        for i in range(1, len(df)):
            # Upper Band logic
            if basic_ub.iloc[i] < final_ub.iloc[i-1] or close.iloc[i-1] > final_ub.iloc[i-1]:
                final_ub.iloc[i] = basic_ub.iloc[i]
            else:
                final_ub.iloc[i] = final_ub.iloc[i-1]
                
            # Lower Band logic
            if basic_lb.iloc[i] > final_lb.iloc[i-1] or close.iloc[i-1] < final_lb.iloc[i-1]:
                final_lb.iloc[i] = basic_lb.iloc[i]
            else:
                final_lb.iloc[i] = final_lb.iloc[i-1]
                
            # Supertrend value assignment
            if i == 1:
                supertrend[i] = final_ub.iloc[i]
            else:
                if supertrend[i-1] == final_ub.iloc[i-1]:
                    supertrend[i] = final_ub.iloc[i] if close.iloc[i] <= final_ub.iloc[i] else final_lb.iloc[i]
                else:
                    supertrend[i] = final_lb.iloc[i] if close.iloc[i] >= final_lb.iloc[i] else final_ub.iloc[i]
                    
        # Check signal
        prev_close = close.iloc[-2]
        curr_close = close.iloc[-1]
        prev_st = supertrend[-2]
        curr_st = supertrend[-1]
        
        # Bullish flip (crosses above supertrend line)
        if prev_close <= prev_st and curr_close > curr_st:
            return "BUY"
        # Bearish flip (crosses below supertrend line)
        elif prev_close >= prev_st and curr_close < curr_st:
            return "SELL"
            
        return "HOLD"
