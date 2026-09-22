import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, Any, List, Optional
from data_engine import DataEngine
from backtest.signals_generator import generate_signals_vectorized

class BacktestEngine:
    """
    High-fidelity scalper backtest simulation with:
    - DCA Position Averaging: repeated signals in the same direction add to the active position
      and recalculate volume-weighted average entry price.
    - TP-Only Exits: Zero Stop Loss exits during backtest; trades close when Take Profit target is hit.
    - Opposite Signal Independence: Opposite signals run independently.
    - Strict Directional Realized PnL:
      * Long: (exit_price - avg_entry) * qty  --> Negative if avg_entry > TP
      * Short: (avg_entry - exit_price) * qty --> Negative if avg_entry < TP
    - End-of-Period Position Close: Any remaining open position at the final candle is closed at
      the final close price, realizing its PnL into the total Released PnL.
    - Separate Long & Short Trade Counting:
      * total_long_trades: Every long signal (initial entry + all subsequent DCA long additions)
      * total_short_trades: Every short signal (initial entry + all subsequent DCA short additions)
      * total_trades = total_long_trades + total_short_trades
    - Rich Trade & Chart Marker Logging for interactive candlestick visualization.
    """

    @staticmethod
    def run_backtest(
        strategy,
        df: pd.DataFrame,
        stop_loss_pct: float = 0.0,
        take_profit_pct: float = 2.0,
        leverage: int = 20,
        trade_amount_usd: float = 20.0,
        use_chop_filter: bool = False,
        use_mtf_filter: bool = False,
        strategy_type: str = "TREND"
    ) -> Dict[str, Any]:
        if df.empty or len(df) < 55:
            return {
                "total_trades": 0,
                "total_long_trades": 0,
                "total_short_trades": 0,
                "closed_trades": 0,
                "closed_long_trades": 0,
                "closed_short_trades": 0,
                "closed_positions": 0,
                "open_trades_count": 0,
                "open_positions_count": 0,
                "win_trades": 0,
                "loss_trades": 0,
                "win_rate": 0.0,
                "released_pnl": 0.0,
                "unreleased_pnl": 0.0,
                "total_pnl": 0.0,
                "profit_factor": 0.0,
                "max_drawdown": 0.0,
                "trades": [],
                "trade_events": [],
                "chart_markers": []
            }

        # 1. Precompute Signals Vectorially
        params = {k: v for k, v in strategy.__dict__.items() if not k.startswith('_')}
        signals = generate_signals_vectorized(strategy.name, params, df)

        # 2. Precompute Filters
        chop_series = None
        if use_chop_filter:
            try:
                chop_series = DataEngine.calculate_chop(df)
            except Exception:
                chop_series = None

        ema50_series = None
        if use_mtf_filter:
            try:
                ema50_series = df["close"].ewm(span=50, adjust=False).mean()
            except Exception:
                ema50_series = None

        # Convert to numpy arrays for high-speed iteration
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        
        # Precompute UNIX timestamps in seconds for chart markers and ISO strings for table
        dt_series = pd.to_datetime(df["timestamp"])
        unix_timestamps = (dt_series.astype('int64') // 10**9).values
        time_strings = dt_series.dt.strftime('%Y-%m-%d %H:%M:%S').values
        
        sig_vals = signals.values
        chop_vals = chop_series.values if chop_series is not None else None
        ema_vals = ema50_series.values if ema50_series is not None else None

        trades = []
        trade_events = []
        chart_markers = []

        total_long_trades = 0
        total_short_trades = 0
        closed_long_trades = 0
        closed_short_trades = 0

        long_pos = None   # dict: avg_entry, qty, tp_price, entry_time, entry_unix, entries_count
        short_pos = None  # dict: avg_entry, qty, tp_price, entry_time, entry_unix, entries_count

        warmup = 50
        n = len(df)

        for i in range(warmup, n):
            cur_close = float(closes[i])
            cur_high = float(highs[i])
            cur_low = float(lows[i])
            cur_time = str(time_strings[i])
            cur_unix = int(unix_timestamps[i])
            signal = sig_vals[i]

            # -------------------------------------------------------------
            # A. Check TP Exits on Active Positions (Intra-candle High/Low)
            # -------------------------------------------------------------
            # 1. Check LONG position TP exit
            if long_pos is not None:
                tp_p = long_pos["tp_price"]
                if cur_high >= tp_p:
                    # TP hit!
                    exit_price = tp_p
                    avg_e = long_pos["avg_entry"]
                    qty = long_pos["qty"]
                    entries_cnt = long_pos["entries_count"]
                    pnl = (exit_price - avg_e) * qty
                    
                    trades.append({
                        "entry_time": long_pos["entry_time"],
                        "exit_time": cur_time,
                        "side": "BUY",
                        "entry_price": avg_e,
                        "exit_price": exit_price,
                        "qty": qty,
                        "pnl": pnl,
                        "entries_count": entries_cnt,
                        "exit_reason": "TAKE_PROFIT"
                    })
                    closed_long_trades += entries_cnt
                    
                    trade_events.append({
                        "time": cur_unix,
                        "time_str": cur_time,
                        "type": "TP_LONG",
                        "side": "BUY",
                        "exit_price": round(exit_price, 6),
                        "avg_entry": round(avg_e, 6),
                        "qty": round(qty, 6),
                        "pnl": round(pnl, 2),
                        "entries_closed": entries_cnt,
                        "exit_reason": "TAKE_PROFIT"
                    })
                    chart_markers.append({
                        "time": cur_unix,
                        "position": "aboveBar",
                        "color": "#38bdf8",
                        "shape": "circle",
                        "text": f"TP Long: {('+$' if pnl >= 0 else '-$')}{abs(pnl):.2f}"
                    })
                    long_pos = None
                elif long_pos is not None:
                    # Check Max Loss Exit ($150 if DCA count > 5, otherwise $100)
                    avg_e = long_pos["avg_entry"]
                    qty = long_pos["qty"]
                    entries_cnt = long_pos["entries_count"]
                    dca_cnt = max(0, entries_cnt - 1)
                    max_loss_threshold = 150.0 if dca_cnt > 5 else 100.0
                    unrealized_pnl = (cur_low - avg_e) * qty
                    if unrealized_pnl <= -max_loss_threshold:
                        exit_price = cur_low
                        pnl = unrealized_pnl
                        trades.append({
                            "entry_time": long_pos["entry_time"],
                            "exit_time": cur_time,
                            "side": "BUY",
                            "entry_price": avg_e,
                            "exit_price": exit_price,
                            "qty": qty,
                            "pnl": pnl,
                            "entries_count": entries_cnt,
                            "exit_reason": "MAX_LOSS_EXIT"
                        })
                        closed_long_trades += entries_cnt
                        trade_events.append({
                            "time": cur_unix,
                            "time_str": cur_time,
                            "type": "SL_LONG",
                            "side": "BUY",
                            "exit_price": round(exit_price, 6),
                            "avg_entry": round(avg_e, 6),
                            "qty": round(qty, 6),
                            "pnl": round(pnl, 2),
                            "entries_closed": entries_cnt,
                            "exit_reason": "MAX_LOSS_EXIT"
                        })
                        chart_markers.append({
                            "time": cur_unix,
                            "position": "aboveBar",
                            "color": "#ef4444",
                            "shape": "circle",
                            "text": f"Max SL Long: -${abs(pnl):.2f}"
                        })
                        long_pos = None

            # 2. Check SHORT position TP exit
            if short_pos is not None:
                tp_p = short_pos["tp_price"]
                if cur_low <= tp_p:
                    # TP hit!
                    exit_price = tp_p
                    avg_e = short_pos["avg_entry"]
                    qty = short_pos["qty"]
                    entries_cnt = short_pos["entries_count"]
                    pnl = (avg_e - exit_price) * qty
                    
                    trades.append({
                        "entry_time": short_pos["entry_time"],
                        "exit_time": cur_time,
                        "side": "SELL",
                        "entry_price": avg_e,
                        "exit_price": exit_price,
                        "qty": qty,
                        "pnl": pnl,
                        "entries_count": entries_cnt,
                        "exit_reason": "TAKE_PROFIT"
                    })
                    closed_short_trades += entries_cnt
                    
                    trade_events.append({
                        "time": cur_unix,
                        "time_str": cur_time,
                        "type": "TP_SHORT",
                        "side": "SELL",
                        "exit_price": round(exit_price, 6),
                        "avg_entry": round(avg_e, 6),
                        "qty": round(qty, 6),
                        "pnl": round(pnl, 2),
                        "entries_closed": entries_cnt,
                        "exit_reason": "TAKE_PROFIT"
                    })
                    chart_markers.append({
                        "time": cur_unix,
                        "position": "belowBar",
                        "color": "#38bdf8",
                        "shape": "circle",
                        "text": f"TP Short: {('+$' if pnl >= 0 else '-$')}{abs(pnl):.2f}"
                    })
                    short_pos = None
                elif short_pos is not None:
                    # Check Max Loss Exit ($150 if DCA count > 5, otherwise $100)
                    avg_e = short_pos["avg_entry"]
                    qty = short_pos["qty"]
                    entries_cnt = short_pos["entries_count"]
                    dca_cnt = max(0, entries_cnt - 1)
                    max_loss_threshold = 150.0 if dca_cnt > 5 else 100.0
                    unrealized_pnl = (avg_e - cur_high) * qty
                    if unrealized_pnl <= -max_loss_threshold:
                        exit_price = cur_high
                        pnl = unrealized_pnl
                        trades.append({
                            "entry_time": short_pos["entry_time"],
                            "exit_time": cur_time,
                            "side": "SELL",
                            "entry_price": avg_e,
                            "exit_price": exit_price,
                            "qty": qty,
                            "pnl": pnl,
                            "entries_count": entries_cnt,
                            "exit_reason": "MAX_LOSS_EXIT"
                        })
                        closed_short_trades += entries_cnt
                        trade_events.append({
                            "time": cur_unix,
                            "time_str": cur_time,
                            "type": "SL_SHORT",
                            "side": "SELL",
                            "exit_price": round(exit_price, 6),
                            "avg_entry": round(avg_e, 6),
                            "qty": round(qty, 6),
                            "pnl": round(pnl, 2),
                            "entries_closed": entries_cnt,
                            "exit_reason": "MAX_LOSS_EXIT"
                        })
                        chart_markers.append({
                            "time": cur_unix,
                            "position": "belowBar",
                            "color": "#ef4444",
                            "shape": "circle",
                            "text": f"Max SL Short: -${abs(pnl):.2f}"
                        })
                        short_pos = None

            # -------------------------------------------------------------
            # B. Process Signals (Candle Close) & Position Averaging (DCA)
            # -------------------------------------------------------------
            if signal == "BUY":
                # Filter checks
                pass_chop = True
                if use_chop_filter and chop_vals is not None:
                    c_val = float(chop_vals[i])
                    if (strategy_type == "TREND" and c_val > 60) or (strategy_type == "REVERSION" and c_val < 40):
                        pass_chop = False

                pass_mtf = True
                if use_mtf_filter and ema_vals is not None:
                    if cur_close < float(ema_vals[i]):
                        pass_mtf = False

                if pass_chop and pass_mtf:
                    total_long_trades += 1
                    add_qty = (trade_amount_usd * leverage) / cur_close
                    if long_pos is None:
                        # Open new Long position
                        tp_p = cur_close * (1.0 + take_profit_pct / 100.0)
                        long_pos = {
                            "avg_entry": cur_close,
                            "qty": add_qty,
                            "tp_price": tp_p,
                            "entry_time": cur_time,
                            "entry_unix": cur_unix,
                            "entries_count": 1
                        }
                        entry_num = 1
                    else:
                        # Adjust active Long position: recalculate weighted average entry price
                        prev_qty = long_pos["qty"]
                        prev_avg = long_pos["avg_entry"]
                        new_qty = prev_qty + add_qty
                        new_avg = ((prev_qty * prev_avg) + (add_qty * cur_close)) / new_qty
                        long_pos["qty"] = new_qty
                        long_pos["avg_entry"] = new_avg
                        long_pos["entries_count"] += 1
                        entry_num = long_pos["entries_count"]

                    trade_events.append({
                        "time": cur_unix,
                        "time_str": cur_time,
                        "type": "LONG_ENTRY" if entry_num == 1 else "LONG_DCA",
                        "side": "BUY",
                        "price": round(cur_close, 6),
                        "qty": round(add_qty, 6),
                        "entry_num": entry_num,
                        "avg_entry": round(long_pos["avg_entry"], 6)
                    })
                    chart_markers.append({
                        "time": cur_unix,
                        "position": "belowBar",
                        "color": "#10b981",
                        "shape": "arrowUp",
                        "text": f"BUY #{entry_num} @ {cur_close:.4f}"
                    })

            elif signal == "SELL":
                # Filter checks
                pass_chop = True
                if use_chop_filter and chop_vals is not None:
                    c_val = float(chop_vals[i])
                    if (strategy_type == "TREND" and c_val > 60) or (strategy_type == "REVERSION" and c_val < 40):
                        pass_chop = False

                pass_mtf = True
                if use_mtf_filter and ema_vals is not None:
                    if cur_close > float(ema_vals[i]):
                        pass_mtf = False

                if pass_chop and pass_mtf:
                    total_short_trades += 1
                    add_qty = (trade_amount_usd * leverage) / cur_close
                    if short_pos is None:
                        # Open new Short position
                        tp_p = cur_close * (1.0 - take_profit_pct / 100.0)
                        short_pos = {
                            "avg_entry": cur_close,
                            "qty": add_qty,
                            "tp_price": tp_p,
                            "entry_time": cur_time,
                            "entry_unix": cur_unix,
                            "entries_count": 1
                        }
                        entry_num = 1
                    else:
                        # Adjust active Short position: recalculate weighted average entry price
                        prev_qty = short_pos["qty"]
                        prev_avg = short_pos["avg_entry"]
                        new_qty = prev_qty + add_qty
                        new_avg = ((prev_qty * prev_avg) + (add_qty * cur_close)) / new_qty
                        short_pos["qty"] = new_qty
                        short_pos["avg_entry"] = new_avg
                        short_pos["entries_count"] += 1
                        entry_num = short_pos["entries_count"]

                    trade_events.append({
                        "time": cur_unix,
                        "time_str": cur_time,
                        "type": "SHORT_ENTRY" if entry_num == 1 else "SHORT_DCA",
                        "side": "SELL",
                        "price": round(cur_close, 6),
                        "qty": round(add_qty, 6),
                        "entry_num": entry_num,
                        "avg_entry": round(short_pos["avg_entry"], 6)
                    })
                    chart_markers.append({
                        "time": cur_unix,
                        "position": "aboveBar",
                        "color": "#f43f5e",
                        "shape": "arrowDown",
                        "text": f"SELL #{entry_num} @ {cur_close:.4f}"
                    })

        # -------------------------------------------------------------
        # C. Close All Remaining Active Positions at End of Period (EOP)
        # -------------------------------------------------------------
        last_close = float(closes[-1])
        last_time = str(time_strings[-1])
        last_unix = int(unix_timestamps[-1])

        # Close remaining open LONG
        if long_pos is not None:
            exit_price = last_close
            avg_e = long_pos["avg_entry"]
            qty = long_pos["qty"]
            entries_cnt = long_pos["entries_count"]
            pnl = (exit_price - avg_e) * qty
            
            trades.append({
                "entry_time": long_pos["entry_time"],
                "exit_time": last_time,
                "side": "BUY",
                "entry_price": avg_e,
                "exit_price": exit_price,
                "qty": qty,
                "pnl": pnl,
                "entries_count": entries_cnt,
                "exit_reason": "END_OF_PERIOD"
            })
            closed_long_trades += entries_cnt
            
            trade_events.append({
                "time": last_unix,
                "time_str": last_time,
                "type": "CLOSE_EOP_LONG",
                "side": "BUY",
                "exit_price": round(exit_price, 6),
                "avg_entry": round(avg_e, 6),
                "qty": round(qty, 6),
                "pnl": round(pnl, 2),
                "entries_closed": entries_cnt,
                "exit_reason": "END_OF_PERIOD"
            })
            chart_markers.append({
                "time": last_unix,
                "position": "aboveBar",
                "color": "#e2e8f0",
                "shape": "square",
                "text": f"EOP Close Long: {('+$' if pnl >= 0 else '-$')}{abs(pnl):.2f}"
            })
            long_pos = None

        # Close remaining open SHORT
        if short_pos is not None:
            exit_price = last_close
            avg_e = short_pos["avg_entry"]
            qty = short_pos["qty"]
            entries_cnt = short_pos["entries_count"]
            pnl = (avg_e - exit_price) * qty
            
            trades.append({
                "entry_time": short_pos["entry_time"],
                "exit_time": last_time,
                "side": "SELL",
                "entry_price": avg_e,
                "exit_price": exit_price,
                "qty": qty,
                "pnl": pnl,
                "entries_count": entries_cnt,
                "exit_reason": "END_OF_PERIOD"
            })
            closed_short_trades += entries_cnt
            
            trade_events.append({
                "time": last_unix,
                "time_str": last_time,
                "type": "CLOSE_EOP_SHORT",
                "side": "SELL",
                "exit_price": round(exit_price, 6),
                "avg_entry": round(avg_e, 6),
                "qty": round(qty, 6),
                "pnl": round(pnl, 2),
                "entries_closed": entries_cnt,
                "exit_reason": "END_OF_PERIOD"
            })
            chart_markers.append({
                "time": last_unix,
                "position": "belowBar",
                "color": "#e2e8f0",
                "shape": "square",
                "text": f"EOP Close Short: {('+$' if pnl >= 0 else '-$')}{abs(pnl):.2f}"
            })
            short_pos = None

        # -------------------------------------------------------------
        # D. Performance Statistics Aggregation
        # -------------------------------------------------------------
        total_trades_opened = total_long_trades + total_short_trades
        closed_entries = closed_long_trades + closed_short_trades
        
        # All positions are closed at end of period -> released_pnl is final realized PnL
        released_pnl = sum(t["pnl"] for t in trades)
        unreleased_pnl = 0.0
        total_pnl = released_pnl

        total_closed_positions = len(trades)
        win_trades = [t for t in trades if t["pnl"] > 0]
        loss_trades = [t for t in trades if t["pnl"] <= 0]

        win_count = len(win_trades)
        loss_count = len(loss_trades)
        win_rate = (win_count / total_closed_positions * 100.0) if total_closed_positions > 0 else 0.0

        gross_profit = sum(t["pnl"] for t in win_trades)
        gross_loss = abs(sum(t["pnl"] for t in loss_trades))

        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        elif gross_profit > 0:
            profit_factor = 99.0
        else:
            profit_factor = 0.0

        # Drawdown calculation on released equity curve
        cum_pnl = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in trades:
            cum_pnl += t["pnl"]
            if cum_pnl > peak:
                peak = cum_pnl
            dd = peak - cum_pnl
            if dd > max_dd:
                max_dd = dd

        # Sort chart markers chronologically for Lightweight Charts
        chart_markers.sort(key=lambda m: m["time"])

        return {
            "total_trades": total_trades_opened,
            "total_long_trades": total_long_trades,
            "total_short_trades": total_short_trades,
            "closed_trades": closed_entries,
            "closed_long_trades": closed_long_trades,
            "closed_short_trades": closed_short_trades,
            "closed_positions": total_closed_positions,
            "open_trades_count": 0,
            "open_positions_count": 0,
            "win_trades": win_count,
            "loss_trades": loss_count,
            "win_rate": round(win_rate, 2),
            "released_pnl": round(released_pnl, 2),
            "unreleased_pnl": 0.0,
            "total_pnl": round(total_pnl, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown": round(max_dd, 2),
            "trades": trades,
            "trade_events": trade_events,
            "chart_markers": chart_markers
        }
