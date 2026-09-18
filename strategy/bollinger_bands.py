import pandas as pd
from strategy.base import BaseStrategy

class BollingerBandsStrategy(BaseStrategy):
    """
    Bollinger Bands Mean Reversion Strategy (Enhanced).
    Features:
    1. Mean reversion trades on upper/lower band bounces.
    2. ADX & ATR Volatility Guard: Disables counter-trend fading if ADX > 28 or ATR ratio > 1.35
       to prevent trading against strong breakout trends.
    3. Candlestick direction & RSI confirmation.
    """
    def __init__(self, period=20, num_std=2.0, rsi_period=14, max_adx=28.0, **kwargs):
        super().__init__(name="Bollinger_Bands")
        self.period = int(period)
        self.num_std = float(num_std)
        self.rsi_period = int(rsi_period)
        self.max_adx = float(max_adx)

    def generate_signal(self, df: pd.DataFrame) -> str:
        if len(df) < max(self.period, self.rsi_period) + 15:
            return "HOLD"

        # Volatility Guard: Disable mean-reversion during strong trend explosions
        adx_series = self.calculate_adx(df)
        curr_adx = adx_series.iloc[-1]
        atr_ratio = self.get_atr_ratio(df)
        
        if curr_adx > self.max_adx or atr_ratio > 1.35:
            return "HOLD"
            
        close = df['close']
        open_p = df['open']
        
        # Calculate Bollinger Bands
        sma = close.rolling(window=self.period).mean()
        rstd = close.rolling(window=self.period).std()
        upper_band = sma + (self.num_std * rstd)
        lower_band = sma - (self.num_std * rstd)
        
        # Calculate RSI
        rsi = self.calculate_rsi(close, self.rsi_period)

        curr_close = close.iloc[-1]
        curr_open = open_p.iloc[-1]
        curr_rsi = rsi.iloc[-1]
        
        curr_upper = upper_band.iloc[-1]
        curr_lower = lower_band.iloc[-1]
        
        # Mean Reversion signals with candle confirmation
        if curr_close < curr_lower and curr_rsi < 35 and curr_close > curr_open:
            return "BUY"
        elif curr_close > curr_upper and curr_rsi > 65 and curr_close < curr_open:
            return "SELL"
            
        return "HOLD"
