import os
import sys
import json
import sqlite3

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from database import Database
from backtest.run_chunked_suite import STRATEGY_TEMPLATES, TIMEFRAMES

def deploy_testnet_bots(symbols=None):
    db = Database(os.path.join(os.path.dirname(os.path.dirname(__file__)), "trading_bot.db"))
    
    if not symbols:
        # Load all symbols from symbols_config
        rows = db.get_symbols_config()
        symbols = [r["symbol"].upper() for r in rows]

    total_expected = len(symbols) * len(STRATEGY_TEMPLATES) * len(TIMEFRAMES)
    print("=" * 80)
    print(f"DEPLOYING DUAL-TIMEFRAME (5m & 15m) TESTNET BOTS")
    print(f"Target Symbols: {len(symbols)} | Strategies: {len(STRATEGY_TEMPLATES)} | Timeframes: {TIMEFRAMES}")
    print(f"Total Bots to Deploy: {total_expected:,}")
    print("=" * 80)

    total_created = 0
    with db.get_connection() as conn:
        cursor = conn.cursor()

        for tpl in STRATEGY_TEMPLATES:
            strat_name = tpl["name"]
            stype = tpl["strategy_type"]
            chop_f = tpl["use_chop_filter"]
            mtf_f = tpl["use_mtf_filter"]
            params = tpl["params"]

            for tf in TIMEFRAMES:
                tp_pct = tpl["tp_5m"] if tf == "5m" else tpl["tp_15m"]

                for sym in symbols:
                    # Check symbol max leverage from symbols_config
                    cursor.execute("SELECT max_leverage FROM symbols_config WHERE symbol = ?", (sym,))
                    lev_row = cursor.fetchone()
                    max_lev = lev_row["max_leverage"] if lev_row else 20
                    bot_leverage = min(10, max_lev)

                    bot_params = {
                        "leverage": bot_leverage,
                        "trade_amount_usd": 20.0,
                        "stop_loss_pct": 0.0,
                        "take_profit_pct": tp_pct,
                        "candle_interval": tf,
                        "use_chop_filter": chop_f,
                        "use_mtf_filter": mtf_f,
                        **params
                    }

                    # Check if this exact bot configuration already exists on testnet
                    cursor.execute("""
                        SELECT id FROM bot_instances
                        WHERE symbol = ? AND strategy_name = ? AND network = 'testnet'
                          AND json_extract(parameters, '$.candle_interval') = ?
                    """, (sym, strat_name, tf))
                    existing = cursor.fetchone()

                    if not existing:
                        cursor.execute("""
                            INSERT INTO bot_instances (symbol, strategy_name, parameters, is_running, network)
                            VALUES (?, ?, ?, 0, 'testnet')
                        """, (sym, strat_name, json.dumps(bot_params)))
                        total_created += 1

        conn.commit()

    print(f"\nSuccessfully deployed {total_created} dual-timeframe testnet bots into bot_instances (state: STOPPED)!")

if __name__ == "__main__":
    deploy_testnet_bots()
