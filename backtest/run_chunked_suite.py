import os
import sys
import time
import json
import sqlite3
import pandas as pd
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor

# Ensure project root is in sys.path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from database import Database
from backtest.data_loader import KlineDataLoader
from backtest.engine import BacktestEngine
from strategy import STRATEGY_MAP

# 13 Elite Scalping Strategies Base Configurations
STRATEGY_TEMPLATES = [
    {
        "name": "Stochastic_RSI",
        "strategy_type": "REVERSION",
        "use_chop_filter": False,
        "use_mtf_filter": False,
        "tp_5m": 1.5,
        "tp_15m": 2.0,
        "params": {"rsi_period": 14, "stoch_period": 14, "k_period": 3, "d_period": 3, "buy_threshold": 20.0, "sell_threshold": 80.0, "max_adx": 28.0}
    },
    {
        "name": "Keltner_Channel_Breakout",
        "strategy_type": "TREND",
        "use_chop_filter": False,
        "use_mtf_filter": False,
        "tp_5m": 2.0,
        "tp_15m": 3.0,
        "params": {"ema_period": 20, "atr_period": 14, "atr_mult": 2.0, "min_adx": 22.0}
    },
    {
        "name": "RSI_Divergence_Scalp",
        "strategy_type": "REVERSION",
        "use_chop_filter": False,
        "use_mtf_filter": False,
        "tp_5m": 1.5,
        "tp_15m": 2.5,
        "params": {"lookback": 25, "rsi_period": 14, "ema_trend": 50}
    },
    {
        "name": "Bollinger_Bands",
        "strategy_type": "REVERSION",
        "use_chop_filter": False,
        "use_mtf_filter": False,
        "tp_5m": 1.5,
        "tp_15m": 2.0,
        "params": {"period": 20, "num_std": 2.0, "rsi_period": 14, "max_adx": 28.0}
    },
    {
        "name": "Engulfing_Volume_Scalp",
        "strategy_type": "TREND",
        "use_chop_filter": False,
        "use_mtf_filter": False,
        "tp_5m": 1.5,
        "tp_15m": 2.5,
        "params": {"vol_mult": 1.5, "vol_period": 20, "body_mult": 1.2}
    },
    {
        "name": "EMA_RSI_Crossover",
        "strategy_type": "TREND",
        "use_chop_filter": False,
        "use_mtf_filter": False,
        "tp_5m": 1.5,
        "tp_15m": 2.5,
        "params": {"ema_short": 9, "ema_long": 21, "rsi_period": 14, "rsi_overbought": 70, "rsi_oversold": 30}
    },
    {
        "name": "SuperTrend",
        "strategy_type": "TREND",
        "use_chop_filter": False,
        "use_mtf_filter": False,
        "tp_5m": 2.0,
        "tp_15m": 3.0,
        "params": {"period": 10, "multiplier": 3.0}
    },
    {
        "name": "Hull_MA_Scalp",
        "strategy_type": "TREND",
        "use_chop_filter": False,
        "use_mtf_filter": False,
        "tp_5m": 1.5,
        "tp_15m": 2.5,
        "params": {"period": 9, "rsi_period": 14}
    },
    {
        "name": "EMA_Ribbon_Scalp",
        "strategy_type": "TREND",
        "use_chop_filter": False,
        "use_mtf_filter": False,
        "tp_5m": 1.5,
        "tp_15m": 2.5,
        "params": {"fast_period": 5, "med_period": 8, "slow_period": 13, "base_period": 21}
    },
    {
        "name": "Volume_Delta_Scalp",
        "strategy_type": "TREND",
        "use_chop_filter": False,
        "use_mtf_filter": False,
        "tp_5m": 1.5,
        "tp_15m": 2.5,
        "params": {"vol_mult": 2.0, "vol_period": 20, "rsi_period": 14}
    },
    {
        "name": "MACD_ZeroCross_Scalp",
        "strategy_type": "TREND",
        "use_chop_filter": False,
        "use_mtf_filter": False,
        "tp_5m": 1.5,
        "tp_15m": 2.5,
        "params": {"fast": 3, "slow": 10, "signal": 16}
    },
    {
        "name": "Squeeze_Momentum_Scalp",
        "strategy_type": "TREND",
        "use_chop_filter": False,
        "use_mtf_filter": False,
        "tp_5m": 2.0,
        "tp_15m": 3.0,
        "params": {"bb_period": 20, "bb_std": 2.0, "kc_period": 20, "kc_mult": 1.5, "mom_period": 12}
    }
]

TIMEFRAMES = ["5m", "15m"]

def get_all_symbols(db: Database) -> List[str]:
    """Retrieve all symbols from symbols_config sorted alphabetically."""
    rows = db.get_symbols_config()
    symbols = sorted([r["symbol"].upper() for r in rows])
    return symbols

def run_chunked_backtest(chunk_size: int = 10, days: int = 30):
    db = Database(os.path.join(os.path.dirname(os.path.dirname(__file__)), "trading_bot.db"))
    all_symbols = get_all_symbols(db)

    # Generate all test tasks: 13 strategies * 2 timeframes = 26 suites
    test_suites = []
    for tpl in STRATEGY_TEMPLATES:
        for tf in TIMEFRAMES:
            tp_pct = tpl["tp_5m"] if tf == "5m" else tpl["tp_15m"]
            test_suites.append({
                "name": tpl["name"],
                "timeframe": tf,
                "take_profit_pct": tp_pct,
                "strategy_type": tpl["strategy_type"],
                "use_chop_filter": tpl["use_chop_filter"],
                "use_mtf_filter": tpl["use_mtf_filter"],
                "params": tpl["params"]
            })

    print("=" * 110)
    print(f"DUAL-TIMEFRAME (5m & 15m) DCA & TP-ONLY HISTORICAL BACKTESTING ENGINE")
    print(f"Total Symbols: {len(all_symbols)} | Chunk Size: {chunk_size} | Backtest Period: {days} Days")
    print(f"Strategies: {len(STRATEGY_TEMPLATES)} | Timeframes: {TIMEFRAMES} | Total Suites: {len(test_suites)}")
    print(f"Total Combinations to Test: {len(all_symbols) * len(test_suites):,}")
    print("=" * 110)

    # Chunk the symbols
    chunks = [all_symbols[i:i + chunk_size] for i in range(0, len(all_symbols), chunk_size)]
    total_chunks = len(chunks)

    overall_results_summary = []

    for suite_idx, cfg in enumerate(test_suites, 1):
        strat_name = cfg["name"]
        tf = cfg["timeframe"]
        tp_pct = cfg["take_profit_pct"]
        stype = cfg["strategy_type"]
        chop_f = cfg["use_chop_filter"]
        mtf_f = cfg["use_mtf_filter"]
        params = cfg["params"]

        print("\n" + "#" * 110)
        print(f"SUITE {suite_idx}/{len(test_suites)}: {strat_name} [{tf.upper()}] (TP: {tp_pct}%, No SL, DCA Averaging, Independent Long/Short)")
        print("#" * 110)

        strat_cls = STRATEGY_MAP.get(strat_name)
        if not strat_cls:
            print(f"ERROR: Strategy {strat_name} not found in STRATEGY_MAP! Skipping.")
            continue

        strat_instance = strat_cls(**params)

        suite_released_pnl = 0.0
        suite_unreleased_pnl = 0.0
        suite_total_trades = 0
        suite_win_trades = 0
        suite_loss_trades = 0
        processed_symbols = 0
        suite_symbol_results = []

        for chunk_idx, chunk in enumerate(chunks, 1):
            chunk_start_sym = chunk[0]
            chunk_end_sym = chunk[-1]
            print(f"\n>>> [{strat_name} - {tf}] Chunk {chunk_idx}/{total_chunks} ({chunk_start_sym} -> {chunk_end_sym})")
            print(f"{'Symbol':<14} | {'Total':<7} | {'Closed':<7} | {'Open':<5} | {'Win Rate':<9} | {'Released PnL':<14} | {'Unreleased PnL':<15} | {'Total PnL':<13} | {'PF':<6}")
            print("-" * 115)

            def eval_symbol(sym):
                with db.get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT total_trades, closed_trades, total_long_trades, total_short_trades,
                               closed_long_trades, closed_short_trades, win_trades, loss_trades, win_rate,
                               released_pnl, unreleased_pnl, open_trades_count, total_pnl,
                               profit_factor, max_drawdown
                        FROM backtest_results
                        WHERE symbol = ? AND strategy_name = ? AND timeframe = ? AND total_long_trades IS NOT NULL
                    """, (sym, strat_name, tf))
                    row = cur.fetchone()
                if row and row["total_long_trades"] is not None:
                    return {
                        "symbol": sym,
                        "total_trades": row["total_trades"],
                        "total_long_trades": row["total_long_trades"],
                        "total_short_trades": row["total_short_trades"],
                        "closed_trades": row["closed_trades"],
                        "closed_long_trades": row["closed_long_trades"],
                        "closed_short_trades": row["closed_short_trades"],
                        "win_trades": row["win_trades"],
                        "loss_trades": row["loss_trades"],
                        "win_rate": row["win_rate"],
                        "released_pnl": row["released_pnl"],
                        "unreleased_pnl": row["unreleased_pnl"],
                        "open_trades_count": row["open_trades_count"],
                        "total_pnl": row["total_pnl"],
                        "profit_factor": row["profit_factor"],
                        "max_drawdown": row["max_drawdown"],
                        "cached": True
                    }

                df = KlineDataLoader.load_klines(sym, tf, days=days)
                if df.empty or len(df) < 55:
                    return None

                res = BacktestEngine.run_backtest(
                    strategy=strat_instance,
                    df=df,
                    stop_loss_pct=0.0,
                    take_profit_pct=tp_pct,
                    leverage=20,
                    trade_amount_usd=20.0,
                    use_chop_filter=chop_f,
                    use_mtf_filter=mtf_f,
                    strategy_type=stype
                )
                res["symbol"] = sym
                res["cached"] = False
                return res

            with ThreadPoolExecutor(max_workers=5) as executor:
                chunk_results = list(executor.map(eval_symbol, chunk))

            for res in chunk_results:
                if res is None:
                    continue
                sym = res["symbol"]
                t_cnt = res["total_trades"]
                t_long = res.get("total_long_trades", 0)
                t_short = res.get("total_short_trades", 0)
                c_cnt = res.get("closed_trades", 0)
                c_long = res.get("closed_long_trades", 0)
                c_short = res.get("closed_short_trades", 0)
                w_cnt = res["win_trades"]
                l_cnt = res["loss_trades"]
                w_rate = res["win_rate"]
                r_pnl = res["released_pnl"]
                u_pnl = res["unreleased_pnl"]
                tot_pnl = res["total_pnl"]
                open_cnt = res["open_trades_count"]
                pf = res["profit_factor"]
                mdd = res["max_drawdown"]
                events = res.get("trade_events")

                if not res.get("cached", False) and r_pnl > 0 and r_pnl >= (2.0 * t_cnt):
                    db.save_backtest_result(
                        symbol=sym,
                        strategy_name=strat_name,
                        timeframe=tf,
                        parameters=params,
                        total_trades=t_cnt,
                        closed_trades=c_cnt,
                        total_long_trades=t_long,
                        total_short_trades=t_short,
                        closed_long_trades=c_long,
                        closed_short_trades=c_short,
                        win_trades=w_cnt,
                        loss_trades=l_cnt,
                        win_rate=w_rate,
                        released_pnl=r_pnl,
                        unreleased_pnl=u_pnl,
                        open_trades_count=open_cnt,
                        total_pnl=tot_pnl,
                        profit_factor=pf,
                        max_drawdown=mdd,
                        period=f"{days}d",
                        trade_events=events
                    )

                suite_released_pnl += r_pnl
                suite_unreleased_pnl += u_pnl
                suite_total_trades += t_cnt
                suite_win_trades += w_cnt
                suite_loss_trades += l_cnt
                processed_symbols += 1

                suite_symbol_results.append({
                    "symbol": sym, "total_trades": t_cnt, "closed_trades": c_cnt, "win_rate": w_rate,
                    "released_pnl": r_pnl, "unreleased_pnl": u_pnl, "total_pnl": tot_pnl,
                    "open_cnt": open_cnt, "pf": pf, "mdd": mdd
                })

                r_str = f"+${r_pnl:.2f}" if r_pnl >= 0 else f"-${abs(r_pnl):.2f}"
                u_str = f"+${u_pnl:.2f}" if u_pnl >= 0 else f"-${abs(u_pnl):.2f}"
                tot_str = f"+${tot_pnl:.2f}" if tot_pnl >= 0 else f"-${abs(tot_pnl):.2f}"
                tag = " (cached)" if res.get("cached") else ""
                print(f"{sym:<14} | {t_cnt:<7} | {c_cnt:<7} | {open_cnt:<5} | {w_rate:>6.1f}%   | {r_str:>14} | {u_str:>15} | {tot_str:>13} | {pf:>6.2f}{tag}")

        # Suite Conclusion
        suite_win_rate = (suite_win_trades / suite_total_trades * 100.0) if suite_total_trades > 0 else 0.0
        suite_tot_pnl = suite_released_pnl + suite_unreleased_pnl
        r_sign = "+" if suite_released_pnl >= 0 else "-"
        u_sign = "+" if suite_unreleased_pnl >= 0 else "-"
        tot_sign = "+" if suite_tot_pnl >= 0 else "-"

        print("\n" + "=" * 95)
        print(f"SUITE CONCLUSION: {strat_name} [{tf.upper()}]")
        print(f"  - Valid Symbols Tested: {processed_symbols}/{len(all_symbols)}")
        print(f"  - Closed TP Trades: {suite_total_trades} (Wins: {suite_win_trades}, Losses: {suite_loss_trades})")
        print(f"  - Closed Trades Win Rate: {suite_win_rate:.2f}%")
        print(f"  - Released PnL: {r_sign}${abs(suite_released_pnl):.2f}")
        print(f"  - Unreleased PnL: {u_sign}${abs(suite_unreleased_pnl):.2f}")
        print(f"  - Combined Net PnL: {tot_sign}${abs(suite_tot_pnl):.2f}")

        # Sort top 5 best symbols
        suite_symbol_results.sort(key=lambda x: x["total_pnl"], reverse=True)
        top5 = suite_symbol_results[:5]
        print("  - Top 5 Performing Symbols:")
        for rank, s in enumerate(top5, 1):
            pnl_s = f"+${s['total_pnl']:.2f}" if s['total_pnl'] >= 0 else f"-${abs(s['total_pnl']):.2f}"
            print(f"     {rank}. {s['symbol']:<12} Total PnL: {pnl_s:>9} (Rel: ${s['released_pnl']:.2f}, Unrel: ${s['unreleased_pnl']:.2f}) | Win Rate: {s['win_rate']:>5.1f}%")
        print("=" * 95)

        overall_results_summary.append({
            "strategy": strat_name,
            "timeframe": tf,
            "symbols": processed_symbols,
            "trades": suite_total_trades,
            "win_rate": suite_win_rate,
            "released_pnl": suite_released_pnl,
            "unreleased_pnl": suite_unreleased_pnl,
            "total_pnl": suite_tot_pnl,
            "top_symbol": top5[0]["symbol"] if top5 else "N/A"
        })

    print("\n\n" + "#" * 115)
    print("ALL 26 DUAL-TIMEFRAME STRATEGY SUITES COMPLETED ACROSS ALL SYMBOL CHUNKS!")
    print("#" * 115)
    print(f"{'Strategy Name':<26} | {'TF':<5} | {'Symbols':<8} | {'Trades':<8} | {'Win Rate':<10} | {'Released PnL':<14} | {'Unreleased PnL':<15} | {'Total PnL':<13} | {'Best Pair':<10}")
    print("-" * 115)
    for r in overall_results_summary:
        r_str = f"+${r['released_pnl']:.2f}" if r['released_pnl'] >= 0 else f"-${abs(r['released_pnl']):.2f}"
        u_str = f"+${r['unreleased_pnl']:.2f}" if r['unreleased_pnl'] >= 0 else f"-${abs(r['unreleased_pnl']):.2f}"
        t_str = f"+${r['total_pnl']:.2f}" if r['total_pnl'] >= 0 else f"-${abs(r['total_pnl']):.2f}"
        print(f"{r['strategy']:<26} | {r['timeframe']:<5} | {r['symbols']:<8} | {r['trades']:<8} | {r['win_rate']:>6.2f}%    | {r_str:>14} | {u_str:>15} | {t_str:>13} | {r['top_symbol']:<10}")
    print("#" * 115)

if __name__ == "__main__":
    run_chunked_backtest(chunk_size=10, days=60)
