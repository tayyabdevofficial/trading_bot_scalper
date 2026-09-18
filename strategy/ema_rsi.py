import pandas as pd
import logging
from strategy.base import BaseStrategy

logger = logging.getLogger("EMARSIStrategy")

class EMARSIStrategy(BaseStrategy):
    def __init__(self, ema_short=9, ema_long=21, rsi_period=14, rsi_overbought=70, rsi_oversold=30):
        super().__init__(name="EMA_RSI_Crossover")
        self.ema_short = ema_short
        self.ema_long = ema_long
        self.rsi_period = rsi_period
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate technical indicators in-place."""
        df = df.copy()
        
        # Calculate EMAs
        df[f'ema_{self.ema_short}'] = df['close'].ewm(span=self.ema_short, adjust=False).mean()
        df[f'ema_{self.ema_long}'] = df['close'].ewm(span=self.ema_long, adjust=False).mean()
        
        # Calculate RSI
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        
        avg_gain = gain.ewm(com=self.rsi_period - 1, adjust=False).mean()
        avg_loss = loss.ewm(com=self.rsi_period - 1, adjust=False).mean()
        
        # Avoid divide by zero
        rs = avg_gain / avg_loss.replace(0.0, 1e-10)
        df[f'rsi_{self.rsi_period}'] = 100 - (100 / (1 + rs))
        
        return df

    def generate_signal(self, df: pd.DataFrame) -> str:
        """
        Signals are generated when:
        - BUY (Long): Short EMA crosses above Long EMA and RSI is below Overbought (not overbought).
        - SELL (Short): Short EMA crosses below Long EMA and RSI is above Oversold (not oversold).
        """
        if len(df) < max(self.ema_long, self.rsi_period) + 2:
            return "HOLD"
            
        df_indicators = self.calculate_indicators(df)
        
        # Get last two rows
        curr = df_indicators.iloc[-1]
        prev = df_indicators.iloc[-2]
        
        ema_s_curr = curr[f'ema_{self.ema_short}']
        ema_l_curr = curr[f'ema_{self.ema_long}']
        
        ema_s_prev = prev[f'ema_{self.ema_short}']
        ema_l_prev = prev[f'ema_{self.ema_long}']
        
        rsi_curr = curr[f'rsi_{self.rsi_period}']
        
        # Crossover checking
        bullish_crossover = (ema_s_prev <= ema_l_prev) and (ema_s_curr > ema_l_curr)
        bearish_crossover = (ema_s_prev >= ema_l_prev) and (ema_s_curr < ema_l_curr)
        
        logger.debug(f"EMA Short: {ema_s_curr:.2f}, EMA Long: {ema_l_curr:.2f}, RSI: {rsi_curr:.2f}")
        
        if bullish_crossover and rsi_curr < self.rsi_overbought:
            return "BUY"
        elif bearish_crossover and rsi_curr > self.rsi_oversold:
            return "SELL"
            
        return "HOLD"
