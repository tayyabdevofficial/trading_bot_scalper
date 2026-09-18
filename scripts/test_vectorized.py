import sys, os
sys.path.append(os.getcwd())
import time
import numpy as np
import pandas as pd
from backtest.data_loader import KlineDataLoader
from strategy.base import BaseStrategy
from strategy.ema_ribbon import EMARibbonScalpStrategy

df = KlineDataLoader.load_klines('BTCUSDT', '15m', days=30)
strat = EMARibbonScalpStrategy()

# Method A: Bar by bar
t0 = time.time()
signals_bar = []
for i in range(50, len(df)):
    sub = df.iloc[max(0, i - 120):i + 1]
    signals_bar.append(strat.generate_signal(sub))
time_bar = time.time() - t0

# Method B: Vectorized series
t1 = time.time()
close = df['close']
ema_fast = close.ewm(span=strat.fast_period, adjust=False).mean()
ema_med = close.ewm(span=strat.med_period, adjust=False).mean()
ema_slow = close.ewm(span=strat.slow_period, adjust=False).mean()
ema_base = close.ewm(span=strat.base_period, adjust=False).mean()

bullish = (ema_fast > ema_med) & (ema_med > ema_slow) & (ema_slow > ema_base)
bearish = (ema_fast < ema_med) & (ema_med < ema_slow) & (ema_slow < ema_base)
curr_spread = (ema_fast - ema_base).abs()
prev_spread = curr_spread.shift(1)
expanding = curr_spread > prev_spread

atr_s = BaseStrategy.calculate_atr(df, 14)
atr_l = BaseStrategy.calculate_atr(df, 50)
atr_ratio = atr_s / atr_l.replace(0, 1e-9)

sig_series = pd.Series("HOLD", index=df.index)
buy_mask = bullish & expanding & ~((atr_ratio > 1.5) & (ema_fast > ema_base * 1.05))
sell_mask = bearish & expanding & ~((atr_ratio > 1.5) & (ema_fast < ema_base * 0.95))
sig_series[buy_mask] = "BUY"
sig_series[sell_mask] = "SELL"
signals_vec = sig_series.iloc[50:].tolist()
time_vec = time.time() - t1

match_count = sum(1 for a, b in zip(signals_bar, signals_vec) if a == b)
print(f"Bar-by-bar time: {time_bar:.3f}s")
print(f"Vectorized time: {time_vec:.4f}s  ({time_bar / time_vec:.0f}x FASTER!)")
print(f"Match rate: {match_count}/{len(signals_bar)} ({match_count / len(signals_bar) * 100:.2f}%)")
