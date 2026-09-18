"""
Deploy Live Trading Bots (Testnet & Mainnet) with Synchronized Backtesting Settings:
- Default Leverage: 20x (capped by symbol max leverage)
- Trade Amount: $10 USD margin ($200 notional at 20x)
- Take Profit: Strategy-specific (1.0% to 3.0% depending on timeframe and strategy)
- Stop Loss: 0.0% (TP-only DCA exits matching backtest engine)
- Strategy Parameters: Exactly synchronized with backtest STRATEGY_TEMPLATES
"""
import os
import sys
import json
import sqlite3
import argparse

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import Database
from backtest.run_chunked_suite import STRATEGY_TEMPLATES, TIMEFRAMES

def deploy_bots(network="testnet", symbols=None, from_backtest_only=False, top_n=None):
    db = Database()
    net = (network or "testnet").lower()
    
    print("=" * 85)
    print(f"DEPLOYING {net.upper()} BOTS WITH SYNCHRONIZED BACKTEST SETTINGS")
    print(f"Leverage: 20x | Margin: $10 USD | SL: 0.0% (TP-Only DCA)")
    print("=" * 85)

    created_count = 0
    updated_count = 0

    with db.get_connection() as conn:
        cursor = conn.cursor()

        if from_backtest_only:
            # Deploy only verified high-performance models from backtest_results
            query = """
                SELECT symbol, strategy_name, timeframe, parameters, released_pnl, total_trades, win_rate
                FROM backtest_results
                WHERE released_pnl >= (2.0 * total_trades) AND released_pnl > 0
                ORDER BY released_pnl DESC
            """
            cursor.execute(query)
            rows = cursor.fetchall()
            if top_n:
                rows = rows[:top_n]
            
            print(f"Deploying {len(rows)} verified top-performing models from backtest results onto {net.upper()}...")
            for r in rows:
                sym = r["symbol"].upper()
                strat_name = r["strategy_name"]
                tf = r["timeframe"]
                bt_params = json.loads(r["parameters"]) if isinstance(r["parameters"], str) else r["parameters"]

                # Get symbol max leverage
                cursor.execute("SELECT max_leverage FROM symbols_config WHERE symbol = ?", (sym,))
                lev_row = cursor.fetchone()
                max_lev = lev_row["max_leverage"] if lev_row else 20
                bot_leverage = min(20, max_lev)

                bot_params = {
                    **bt_params,
                    "leverage": bot_leverage,
                    "trade_amount_usd": 10.0,
                    "stop_loss_pct": 0.0,
                    "candle_interval": tf
                }

                cursor.execute("""
                    SELECT id FROM bot_instances
                    WHERE symbol = ? AND strategy_name = ? AND network = ?
                      AND json_extract(parameters, '$.candle_interval') = ?
                """, (sym, strat_name, net, tf))
                existing = cursor.fetchone()

                if not existing:
                    cursor.execute("""
                        INSERT INTO bot_instances (symbol, strategy_name, parameters, is_running, network)
                        VALUES (?, ?, ?, 0, ?)
                    """, (sym, strat_name, json.dumps(bot_params), net))
                    created_count += 1
                else:
                    cursor.execute("""
                        UPDATE bot_instances SET parameters = ? WHERE id = ?
                    """, (json.dumps(bot_params), existing["id"]))
                    updated_count += 1
        else:
            if not symbols:
                rows = db.get_symbols_config()
                symbols = [r["symbol"].upper() for r in rows]

            print(f"Deploying all {len(symbols)} symbols across {len(STRATEGY_TEMPLATES)} strategies & {TIMEFRAMES} onto {net.upper()}...")

            for tpl in STRATEGY_TEMPLATES:
                strat_name = tpl["name"]
                chop_f = tpl["use_chop_filter"]
                mtf_f = tpl["use_mtf_filter"]
                params = tpl["params"]

                for tf in TIMEFRAMES:
                    tp_pct = tpl["tp_5m"] if tf == "5m" else tpl["tp_15m"]

                    for sym in symbols:
                        cursor.execute("SELECT max_leverage FROM symbols_config WHERE symbol = ?", (sym,))
                        lev_row = cursor.fetchone()
                        max_lev = lev_row["max_leverage"] if lev_row else 20
                        bot_leverage = min(20, max_lev)

                        bot_params = {
                            "leverage": bot_leverage,
                            "trade_amount_usd": 10.0,
                            "stop_loss_pct": 0.0,
                            "take_profit_pct": tp_pct,
                            "candle_interval": tf,
                            "use_chop_filter": chop_f,
                            "use_mtf_filter": mtf_f,
                            **params
                        }

                        cursor.execute("""
                            SELECT id FROM bot_instances
                            WHERE symbol = ? AND strategy_name = ? AND network = ?
                              AND json_extract(parameters, '$.candle_interval') = ?
                        """, (sym, strat_name, net, tf))
                        existing = cursor.fetchone()

                        if not existing:
                            cursor.execute("""
                                INSERT INTO bot_instances (symbol, strategy_name, parameters, is_running, network)
                                VALUES (?, ?, ?, 0, ?)
                            """, (sym, strat_name, json.dumps(bot_params), net))
                            created_count += 1
                        else:
                            cursor.execute("""
                                UPDATE bot_instances SET parameters = ? WHERE id = ?
                            """, (json.dumps(bot_params), existing["id"]))
                            updated_count += 1

        conn.commit()

    print(f"\n[DONE] Finished {net.upper()} deployment: {created_count} bots created, {updated_count} bots updated.")
    return created_count, updated_count

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deploy trading bots with backtest settings")
    parser.add_argument("--network", default="testnet", choices=["testnet", "mainnet", "both"], help="Target network")
    parser.add_argument("--from-backtest", action="store_true", help="Deploy only profitable backtested models")
    parser.add_argument("--top", type=int, default=None, help="Limit to top N models")
    args = parser.parse_args()

    if args.network == "both":
        deploy_bots("testnet", from_backtest_only=args.from_backtest, top_n=args.top)
        deploy_bots("mainnet", from_backtest_only=args.from_backtest, top_n=args.top)
    else:
        deploy_bots(args.network, from_backtest_only=args.from_backtest, top_n=args.top)
