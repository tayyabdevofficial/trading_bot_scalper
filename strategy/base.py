import pandas as pd
import numpy as np
from abc import ABC, abstractmethod

class BaseStrategy(ABC):
    def __init__(self, name="BaseStrategy"):
        self.name = name

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame) -> str:
        """
        Processes a dataframe of OHLCV candles and returns a trading signal.
        
        Parameters:
            df (pd.DataFrame): DataFrame containing klines with cols [open, high, low, close, volume]
            
        Returns:
            str: "BUY" (Long), "SELL" (Short), or "HOLD" (No Action)
        """
        pass

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate Average True Range (ATR)."""
        if len(df) < period + 1:
            return pd.Series([0.0] * len(df), index=df.index)
        high = df['high'].values
        low = df['low'].values
        close = df['close'].values
        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]
        tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
        atr = pd.Series(tr, index=df.index).rolling(window=period).mean()
        return atr.fillna(0.0)

    @staticmethod
    def get_atr_ratio(df: pd.DataFrame, short_period: int = 14, long_period: int = 50) -> float:
        """
        Returns ATR Ratio (Short ATR / Long ATR).
        > 1.3 indicates High Volatility expansion, < 0.8 indicates Low Volatility contraction.
        """
        atr_s = BaseStrategy.calculate_atr(df, short_period)
        atr_l = BaseStrategy.calculate_atr(df, long_period)
        if len(atr_l) == 0 or atr_l.iloc[-1] == 0:
            return 1.0
        return float(atr_s.iloc[-1] / atr_l.iloc[-1])

    @staticmethod
    def calculate_rsi(close_series: pd.Series, period: int = 14) -> pd.Series:
        """Calculate Relative Strength Index (RSI)."""
        delta = close_series.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
        avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0.0, 1e-10)
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50.0)

    @staticmethod
    def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate Average Directional Index (ADX) trend strength."""
        if len(df) < period * 2:
            return pd.Series([20.0] * len(df), index=df.index)
        
        high = df['high']
        low = df['low']
        close = df['close']
        
        up_move = high.diff()
        down_move = -low.diff()
        
        pos_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        neg_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
        
        high_v = high.values
        low_v = low.values
        close_v = close.values
        prev_close_v = np.roll(close_v, 1)
        prev_close_v[0] = close_v[0]
        tr = pd.Series(np.maximum(high_v - low_v, np.maximum(np.abs(high_v - prev_close_v), np.abs(low_v - prev_close_v))), index=df.index)
        
        tr_smooth = tr.ewm(alpha=1/period, adjust=False).mean()
        pos_dm_smooth = pos_dm.ewm(alpha=1/period, adjust=False).mean()
        neg_dm_smooth = neg_dm.ewm(alpha=1/period, adjust=False).mean()
        
        pos_di = 100 * (pos_dm_smooth / tr_smooth.replace(0, 1e-10))
        neg_di = 100 * (neg_dm_smooth / tr_smooth.replace(0, 1e-10))
        
        dx = 100 * (pos_di - neg_di).abs() / (pos_di + neg_di).replace(0, 1e-10)
        adx = dx.ewm(alpha=1/period, adjust=False).mean()
        return adx.fillna(20.0)

    @staticmethod
    def calculate_fisher_transform(df: pd.DataFrame, period: int = 9) -> pd.Series:
        """Calculate Ehlers Fisher Transform indicator."""
        high = df['high']
        low = df['low']
        price = (high + low) / 2.0
        
        min_p = low.rolling(window=period).min()
        max_p = high.rolling(window=period).max()
        
        # Normalize price between -0.999 and +0.999
        value = 0.66 * ((price - min_p) / (max_p - min_p).replace(0, 1e-10) - 0.5)
        value = value.clip(-0.999, 0.999)
        
        # Apply Fisher transform: 0.5 * ln((1 + value) / (1 - value))
        import numpy as np
        fisher = 0.5 * np.log((1 + value) / (1 - value).replace(0, 1e-10))
        return fisher.fillna(0.0)

    @staticmethod
    def calculate_kdj(df: pd.DataFrame, n_period: int = 9, k_period: int = 3, d_period: int = 3):
        """Calculate KDJ Oscillator (%K, %D, %J)."""
        low_min = df['low'].rolling(window=n_period).min()
        high_max = df['high'].rolling(window=n_period).max()
        
        rsv = 100 * ((df['close'] - low_min) / (high_max - low_min).replace(0, 1e-10))
        k = rsv.ewm(com=k_period - 1, adjust=False).mean()
        d = k.ewm(com=d_period - 1, adjust=False).mean()
        j = 3 * k - 2 * d
        return k.fillna(50.0), d.fillna(50.0), j.fillna(50.0)

    @staticmethod
    def calculate_trix(close_series: pd.Series, period: int = 9) -> pd.Series:
        """Calculate TRIX (Triple Exponential Derivative)."""
        ema1 = close_series.ewm(span=period, adjust=False).mean()
        ema2 = ema1.ewm(span=period, adjust=False).mean()
        ema3 = ema2.ewm(span=period, adjust=False).mean()
        trix = (ema3 - ema3.shift(1)) / ema3.shift(1).replace(0, 1e-10) * 10000
        return trix.fillna(0.0)

    @staticmethod
    def calculate_aroon(df: pd.DataFrame, period: int = 25):
        """Calculate Aroon Up and Aroon Down."""
        high = df['high']
        low = df['low']
        
        aroon_up = high.rolling(window=period + 1).apply(lambda x: float(x.argmax()) / period * 100, raw=True)
        aroon_down = low.rolling(window=period + 1).apply(lambda x: float(x.argmin()) / period * 100, raw=True)
        return aroon_up.fillna(50.0), aroon_down.fillna(50.0)

    @staticmethod
    def calculate_kama(close_series: pd.Series, er_period: int = 10, fast_ema: int = 2, slow_ema: int = 30) -> pd.Series:
        """Calculate Kaufman Adaptive Moving Average (KAMA)."""
        change = (close_series - close_series.shift(er_period)).abs()
        volatility = close_series.diff().abs().rolling(window=er_period).sum().replace(0, 1e-10)
        
        er = change / volatility # Efficiency Ratio
        fast_sc = 2 / (fast_ema + 1)
        slow_sc = 2 / (slow_ema + 1)
        sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
        
        kama = close_series.copy()
        for i in range(er_period, len(close_series)):
            kama.iloc[i] = kama.iloc[i-1] + sc.iloc[i] * (close_series.iloc[i] - kama.iloc[i-1])
        return kama.fillna(close_series)



