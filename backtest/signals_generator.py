import numpy as np
import pandas as pd
from strategy.base import BaseStrategy

def generate_signals_vectorized(strategy_name: str, params: dict, df: pd.DataFrame) -> pd.Series:
    """
    High-speed vectorized signal precomputation for backtesting.
    Returns a pandas Series of "BUY", "SELL", or "HOLD" aligned with df.index.
    """
    n = len(df)
    signals = pd.Series("HOLD", index=df.index)
    if n < 50:
        return signals

    close = df['close']
    high = df['high']
    low = df['low']
    open_p = df['open']
    volume = df['volume']

    # Precompute common indicators
    atr14 = BaseStrategy.calculate_atr(df, 14)
    atr50 = BaseStrategy.calculate_atr(df, 50)
    atr_ratio = (atr14 / atr50.replace(0, 1e-9)).fillna(1.0)
    adx14 = BaseStrategy.calculate_adx(df, 14)

    name = strategy_name.lower()

    if "ema_ribbon" in name:
        fast_p = int(params.get("fast_period", 5))
        med_p = int(params.get("med_period", 8))
        slow_p = int(params.get("slow_period", 13))
        base_p = int(params.get("base_period", 21))

        ema_f = close.ewm(span=fast_p, adjust=False).mean()
        ema_m = close.ewm(span=med_p, adjust=False).mean()
        ema_s = close.ewm(span=slow_p, adjust=False).mean()
        ema_b = close.ewm(span=base_p, adjust=False).mean()

        bullish = (ema_f > ema_m) & (ema_m > ema_s) & (ema_s > ema_b)
        bearish = (ema_f < ema_m) & (ema_m < ema_s) & (ema_s < ema_b)

        spread = (ema_f - ema_b).abs()
        expanding = spread > spread.shift(1)

        buy_mask = bullish & expanding & ~((atr_ratio > 1.5) & (ema_f > ema_b * 1.05))
        sell_mask = bearish & expanding & ~((atr_ratio > 1.5) & (ema_f < ema_b * 0.95))
        signals[buy_mask] = "BUY"
        signals[sell_mask] = "SELL"

    elif "squeeze_momentum" in name:
        bb_p = int(params.get("bb_period", 20))
        bb_std = float(params.get("bb_std", 2.0))
        kc_p = int(params.get("kc_period", 20))
        kc_mult = float(params.get("kc_mult", 1.5))

        bb_mid = close.rolling(bb_p).mean()
        bb_dev = close.rolling(bb_p).std()
        bb_up = bb_mid + bb_std * bb_dev
        bb_dn = bb_mid - bb_std * bb_dev

        kc_mid = close.rolling(kc_p).mean()
        kc_atr = BaseStrategy.calculate_atr(df, kc_p)
        kc_up = kc_mid + kc_mult * kc_atr
        kc_dn = kc_mid - kc_mult * kc_atr

        squeeze_on = (bb_up < kc_up) & (bb_dn > kc_dn)
        squeeze_off = ~squeeze_on

        highest = high.rolling(kc_p).max()
        lowest = low.rolling(kc_p).min()
        midline = (highest + lowest) / 2.0
        mom = close - (midline + kc_mid) / 2.0

        mom_prev = mom.shift(1)
        buy_mask = (mom > 0) & (mom > mom_prev) & squeeze_off
        sell_mask = (mom < 0) & (mom < mom_prev) & squeeze_off
        signals[buy_mask] = "BUY"
        signals[sell_mask] = "SELL"

    elif "volume_delta" in name:
        vol_p = int(params.get("vol_period", 20))
        vol_mult = float(params.get("vol_mult", 2.0))
        rsi_p = int(params.get("rsi_period", 14))

        vol_ma = volume.rolling(vol_p).mean()
        eff_mult = np.where(atr_ratio > 1.3, vol_mult * 1.2, vol_mult)
        vol_surge = volume >= (vol_ma * eff_mult)

        rsi = BaseStrategy.calculate_rsi(close, rsi_p)
        buy_mask = vol_surge & (close > open_p) & (rsi > 50)
        sell_mask = vol_surge & (close < open_p) & (rsi < 50)
        signals[buy_mask] = "BUY"
        signals[sell_mask] = "SELL"

    elif "engulfing_volume" in name:
        vol_p = int(params.get("vol_period", 20))
        vol_mult = float(params.get("vol_mult", 1.5))
        body_mult = float(params.get("body_mult", 1.2))

        vol_ma = volume.rolling(vol_p).mean()
        eff_mult = np.where(atr_ratio > 1.3, vol_mult * 1.2, vol_mult)
        has_vol = volume >= (vol_ma * eff_mult)

        body_size = (close - open_p).abs()
        avg_body = body_size.rolling(vol_p).mean()
        has_body = body_size >= (avg_body * body_mult)

        prev_close = close.shift(1)
        prev_open = open_p.shift(1)
        prev_red = prev_close < prev_open
        curr_green = close > open_p
        bullish_engulf = prev_red & curr_green & (close >= prev_open) & (open_p <= prev_close)

        prev_green = prev_close > prev_open
        curr_red = close < open_p
        bearish_engulf = prev_green & curr_red & (close <= prev_open) & (open_p >= prev_close)

        signals[has_vol & has_body & bullish_engulf] = "BUY"
        signals[has_vol & has_body & bearish_engulf] = "SELL"

    elif "rsi_divergence" in name:
        lookback = int(params.get("lookback", 25))
        rsi_p = int(params.get("rsi_period", 14))
        ema_p = int(params.get("ema_trend", 50))

        rsi_vals = BaseStrategy.calculate_rsi(close, rsi_p).values
        ema_vals = close.ewm(span=ema_p, adjust=False).mean().values
        low_vals = low.values
        high_vals = high.values
        close_vals = close.values
        adx_vals = adx14.values
        atr_r_vals = atr_ratio.values

        # Precompute fractal swing pivots across array
        is_piv_l = np.zeros(n, dtype=bool)
        is_piv_h = np.zeros(n, dtype=bool)
        for idx in range(2, n - 2):
            if low_vals[idx] <= low_vals[idx-1] and low_vals[idx] <= low_vals[idx-2] and low_vals[idx] <= low_vals[idx+1] and low_vals[idx] <= low_vals[idx+2]:
                is_piv_l[idx] = True
            if high_vals[idx] >= high_vals[idx-1] and high_vals[idx] >= high_vals[idx-2] and high_vals[idx] >= high_vals[idx+1] and high_vals[idx] >= high_vals[idx+2]:
                is_piv_h[idx] = True

        for idx in range(50, n):
            cur_c = close_vals[idx]
            cur_e = ema_vals[idx]
            is_high_vol = (atr_r_vals[idx] > 1.3) or (adx_vals[idx] > 30.0)

            # Swing lows for Bullish divergence
            s_lows = [p for p in range(max(0, idx - lookback - 1), idx - 1) if is_piv_l[p]]
            if len(s_lows) >= 2:
                rec, old = s_lows[-1], s_lows[-2]
                reg_bull = (low_vals[rec] < low_vals[old]) and (rsi_vals[rec] > rsi_vals[old])
                hid_bull = (low_vals[rec] > low_vals[old]) and (rsi_vals[rec] < rsi_vals[old])
                if (not is_high_vol and reg_bull and cur_c > cur_e) or (is_high_vol and hid_bull and cur_c > cur_e):
                    signals.iloc[idx] = "BUY"
                    continue

            # Swing highs for Bearish divergence
            s_highs = [p for p in range(max(0, idx - lookback - 1), idx - 1) if is_piv_h[p]]
            if len(s_highs) >= 2:
                rec, old = s_highs[-1], s_highs[-2]
                reg_bear = (high_vals[rec] > high_vals[old]) and (rsi_vals[rec] < rsi_vals[old])
                hid_bear = (high_vals[rec] < high_vals[old]) and (rsi_vals[rec] > rsi_vals[old])
                if (not is_high_vol and reg_bear and cur_c < cur_e) or (is_high_vol and hid_bear and cur_c < cur_e):
                    signals.iloc[idx] = "SELL"
                    continue

    elif "bollinger_bands" in name:
        period = int(params.get("period", 20))
        num_std = float(params.get("num_std", 2.0))
        rsi_p = int(params.get("rsi_period", 14))
        max_adx = float(params.get("max_adx", 28.0))

        sma = close.rolling(period).mean()
        std = close.rolling(period).std()
        upper = sma + num_std * std
        lower = sma - num_std * std
        rsi = BaseStrategy.calculate_rsi(close, rsi_p)

        buy_mask = (low <= lower) & (rsi <= 32) & (close >= open_p) & (adx14 <= max_adx)
        sell_mask = (high >= upper) & (rsi >= 68) & (close <= open_p) & (adx14 <= max_adx)
        signals[buy_mask] = "BUY"
        signals[sell_mask] = "SELL"

    elif "macd_zerocross" in name:
        fast_p = int(params.get("fast", 3))
        slow_p = int(params.get("slow", 10))
        sig_p = int(params.get("signal", 16))

        ema_f = close.ewm(span=fast_p, adjust=False).mean()
        ema_s = close.ewm(span=slow_p, adjust=False).mean()
        macd = ema_f - ema_s
        sig_line = macd.ewm(span=sig_p, adjust=False).mean()
        hist = macd - sig_line

        macd_prev = macd.shift(1)
        cross_above_zero = (macd_prev <= 0) & (macd > 0)
        cross_below_zero = (macd_prev >= 0) & (macd < 0)

        signals[cross_above_zero & (hist > 0)] = "BUY"
        signals[cross_below_zero & (hist < 0)] = "SELL"

    elif "stochastic_rsi" in name:
        rsi_p = int(params.get("rsi_period", 14))
        stoch_p = int(params.get("stoch_period", 14))
        k_p = int(params.get("k_period", 3))
        d_p = int(params.get("d_period", 3))
        buy_thresh = float(params.get("buy_threshold", 20.0))
        sell_thresh = float(params.get("sell_threshold", 80.0))
        max_adx = float(params.get("max_adx", 28.0))

        rsi = BaseStrategy.calculate_rsi(close, rsi_p)
        min_rsi = rsi.rolling(stoch_p).min()
        max_rsi = rsi.rolling(stoch_p).max()
        stoch = 100 * (rsi - min_rsi) / (max_rsi - min_rsi).replace(0, 1e-9)

        k_line = stoch.rolling(k_p).mean()
        d_line = k_line.rolling(d_p).mean()

        k_prev = k_line.shift(1)
        d_prev = d_line.shift(1)

        bullish_hook = (k_prev <= d_prev) & (k_line > d_line) & (k_prev <= buy_thresh) & (adx14 <= max_adx)
        bearish_hook = (k_prev >= d_prev) & (k_line < d_line) & (k_prev >= sell_thresh) & (adx14 <= max_adx)

        signals[bullish_hook] = "BUY"
        signals[bearish_hook] = "SELL"

    elif "supertrend" in name:
        period = int(params.get("period", 10))
        multiplier = float(params.get("multiplier", 3.0))

        atr = BaseStrategy.calculate_atr(df, period)
        hl2 = (high + low) / 2.0
        upper_basic = hl2 + (multiplier * atr)
        lower_basic = hl2 - (multiplier * atr)

        # SuperTrend iterative calculation
        trend = np.ones(n)
        upper_band = upper_basic.values.copy()
        lower_band = lower_basic.values.copy()
        c_vals = close.values

        for i in range(1, n):
            if lower_basic.iloc[i] > lower_band[i-1] or c_vals[i-1] < lower_band[i-1]:
                lower_band[i] = lower_basic.iloc[i]
            else:
                lower_band[i] = lower_band[i-1]

            if upper_basic.iloc[i] < upper_band[i-1] or c_vals[i-1] > upper_band[i-1]:
                upper_band[i] = upper_basic.iloc[i]
            else:
                upper_band[i] = upper_band[i-1]

            if trend[i-1] == 1 and c_vals[i] < lower_band[i]:
                trend[i] = -1
            elif trend[i-1] == -1 and c_vals[i] > upper_band[i]:
                trend[i] = 1
            else:
                trend[i] = trend[i-1]

        trend_series = pd.Series(trend, index=df.index)
        trend_prev = trend_series.shift(1)

        signals[(trend_prev == -1) & (trend_series == 1)] = "BUY"
        signals[(trend_prev == 1) & (trend_series == -1)] = "SELL"

    elif "ema_rsi" in name:
        ema_s = int(params.get("ema_short", 9))
        ema_l = int(params.get("ema_long", 21))
        rsi_p = int(params.get("rsi_period", 14))

        short_ma = close.ewm(span=ema_s, adjust=False).mean()
        long_ma = close.ewm(span=ema_l, adjust=False).mean()
        rsi = BaseStrategy.calculate_rsi(close, rsi_p)

        ma_diff = short_ma - long_ma
        ma_diff_prev = ma_diff.shift(1)

        cross_above = (ma_diff_prev <= 0) & (ma_diff > 0)
        cross_below = (ma_diff_prev >= 0) & (ma_diff < 0)

        signals[cross_above & (rsi > 50) & (rsi < 70)] = "BUY"
        signals[cross_below & (rsi < 50) & (rsi > 30)] = "SELL"

    elif "keltner_channel" in name:
        ema_p = int(params.get("ema_period", 20))
        atr_p = int(params.get("atr_period", 14))
        atr_m = float(params.get("atr_mult", 2.0))
        min_adx = float(params.get("min_adx", 22.0))

        mid = close.ewm(span=ema_p, adjust=False).mean()
        atr = BaseStrategy.calculate_atr(df, atr_p)
        upper = mid + (atr_m * atr)
        lower = mid - (atr_m * atr)

        close_prev = close.shift(1)
        breakout_up = (close_prev <= upper.shift(1)) & (close > upper)
        breakout_dn = (close_prev >= lower.shift(1)) & (close < lower)

        signals[breakout_up & (adx14 >= min_adx)] = "BUY"
        signals[breakout_dn & (adx14 >= min_adx)] = "SELL"

    elif "hull_ma" in name:
        period = int(params.get("period", 9))
        rsi_p = int(params.get("rsi_period", 14))

        def wma(s, p):
            weights = np.arange(1, p + 1)
            return s.rolling(p).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)

        half_wma = 2.0 * wma(close, period // 2)
        full_wma = wma(close, period)
        diff = half_wma - full_wma
        sqrt_p = int(np.sqrt(period))
        hma = wma(diff, sqrt_p)

        hma_diff = hma - hma.shift(1)
        hma_diff_prev = hma_diff.shift(1)
        rsi = BaseStrategy.calculate_rsi(close, rsi_p)

        signals[(hma_diff_prev <= 0) & (hma_diff > 0) & (rsi > 50)] = "BUY"
        signals[(hma_diff_prev >= 0) & (hma_diff < 0) & (rsi < 50)] = "SELL"

    else:
        # Fallback to strategy class
        from strategy import STRATEGY_MAP
        if strategy_name in STRATEGY_MAP:
            strat = STRATEGY_MAP[strategy_name](**params)
            for i in range(50, n):
                sub = df.iloc[max(0, i - 120):i + 1]
                signals.iloc[i] = strat.generate_signal(sub)

    return signals
