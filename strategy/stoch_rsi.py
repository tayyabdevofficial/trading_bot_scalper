import pandas as pd
import numpy as np
from strategy.base import BaseStrategy

class StochasticRSIStrategy(BaseStrategy):
    """
    Stochastic RSI Strategy (Enhanced).
    Features:
    1. Oversold/Overbought %K and %D crossovers.
    2. ADX Volatility Guard: Disables counter-trend entries when ADX > 28 to prevent losses in steep trends.
    3. EMA 50 trend alignment filter.
    """
    def __init__(self, rsi_period=14, stoch_period=14, k_period=3, d_period=3, buy_threshold=20, sell_threshold=80, max_adx=28.0, **kwargs):
        super().__init__(name="Stochastic_RSI")
        self.rsi_period = int(rsi_period)
        self.stoch_period = int(stoch_period)
        self.k_period = int(k_period)
        self.d_period = int(d_period)
        self.buy_threshold = float(buy_threshold)
        self.sell_threshold = float(sell_threshold)
        self.max_adx = float(max_adx)

    def generate_signal(self, df: pd.DataFrame) -> str:
        needed_len = self.rsi_period + self.stoch_period + max(self.k_period, self.d_period) + 15
        if len(df) < needed_len:
            return "HOLD"
            
        # Volatility Guard: Disable mean reversion in strong trend explosions
        adx_series = self.calculate_adx(df)
        curr_adx = adx_series.iloc[-1]
        atr_ratio = self.get_atr_ratio(df)
        
        if curr_adx > self.max_adx or atr_ratio > 1.35:
            return "HOLD"
            
        close = df['close']
        ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
        curr_close = close.iloc[-1]
        
        # 1. Calculate RSI
        rsi = self.calculate_rsi(close, self.rsi_period)
        
        # 2. Calculate Stoch RSI
        min_rsi = rsi.rolling(window=self.stoch_period).min()
        max_rsi = rsi.rolling(window=self.stoch_period).max()
        stoch_rsi = (rsi - min_rsi) / (max_rsi - min_rsi).replace(0.0, 1e-10)
        
        # 3. Calculate %K and %D
        k = stoch_rsi.rolling(window=self.k_period).mean() * 100
        d = k.rolling(window=self.d_period).mean()
        
        curr_k, prev_k = k.iloc[-1], k.iloc[-2]
        curr_d, prev_d = d.iloc[-1], d.iloc[-2]
        
        # Buy on crossover in oversold territory near/above EMA 50
        bullish_cross = (prev_k <= prev_d) and (curr_k > curr_d) and (curr_k < self.buy_threshold) and (curr_close >= ema50 * 0.99)
        # Sell on crossunder in overbought territory near/below EMA 50
        bearish_cross = (prev_k >= prev_d) and (curr_k < curr_d) and (curr_k > self.sell_threshold) and (curr_close <= ema50 * 1.01)
        
        if bullish_cross:
            return "BUY"
        elif bearish_cross:
            return "SELL"
            
        return "HOLD"
