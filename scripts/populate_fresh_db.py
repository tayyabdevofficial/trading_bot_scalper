import os
import sys
import glob
import json
import sqlite3
import requests

sys.path.insert(0, r"d:\Freelancing\My Projects\python\trading_bot")
from database import Database
from config import Config

def populate():
    db = Database()
    
    # 1. Gather all symbols from data/cache_klines
    kline_files = glob.glob(r"d:\Freelancing\My Projects\python\trading_bot\data\cache_klines\*_*_*.csv")
    cache_symbols = set()
    for f in kline_files:
        basename = os.path.basename(f)
        parts = basename.split("_")
        if len(parts) >= 2:
            cache_symbols.add(parts[0].upper())
            
    print(f"Found {len(cache_symbols)} unique symbols from cache_klines.")

    # 2. Fetch leverage and symbol metadata from Binance Futures API
    leverage_map = {}
    try:
        resp = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for s in data.get("symbols", []):
                sym = s.get("symbol", "").upper()
                if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING":
                    cache_symbols.add(sym)
                    leverage_map[sym] = 20  # default safe leverage
    except Exception as e:
        print(f"Notice fetching exchangeInfo: {e}")

    symbols_list = sorted(list(cache_symbols))
    print(f"Total symbols to register in symbols_config: {len(symbols_list)}")

    with db.get_connection() as conn:
        cur = conn.cursor()
        for sym in symbols_list:
            lev = leverage_map.get(sym, 20)
            cur.execute("""
                INSERT OR REPLACE INTO symbols_config (symbol, max_leverage)
                VALUES (?, ?)
            """, (sym, lev))
        conn.commit()

    print(f"Successfully populated symbols_config with {len(symbols_list)} symbols.")

    # 3. Deploy bots for the 14 key pairs with standard settings
    default_bot_symbols = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT", 
        "XRPUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "NEARUSDT", 
        "ARBUSDT", "OPUSDT", "SUIUSDT", "PEPEUSDT"
    ]
    print(f"Deploying bots for {len(default_bot_symbols)} key symbols...")
    with db.get_connection() as conn:
        cur = conn.cursor()
        for sym in default_bot_symbols:
            bot_params = {
                "leverage": 20,
                "trade_amount_usd": 10.0,
                "stop_loss_pct": 0.0,
                "take_profit_pct": 1.0,
                "candle_interval": "5m",
                "trigger_mode": "candle_close",
                "tick_cooldown_sec": 15,
                "atr_sl_mult": 1.5,
                "atr_tp_mult": 3,
                "use_mtf_filter": False,
                "use_atr_stops": False,
                "use_chop_filter": False,
                "hedge_mode": False,
                "lookback": 25,
                "rsi_period": 14,
                "ema_trend": 50
            }
            cur.execute("""
                INSERT OR REPLACE INTO bot_instances (symbol, strategy_name, parameters, is_running, network)
                VALUES (?, 'RSI_Divergence_Scalp', ?, 0, 'mainnet')
            """, (sym, json.dumps(bot_params)))
        conn.commit()

    # 4. Verify counts
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM symbols_config")
        sym_cnt = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM bot_instances")
        bot_cnt = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM backtest_results")
        bt_cnt = cur.fetchone()[0]
        print("=" * 60)
        print("DATABASE SUMMARY:")
        print(f"  symbols_config:   {sym_cnt} symbols")
        print(f"  bot_instances:    {bot_cnt} bots")
        print(f"  backtest_results: {bt_cnt} rows (clean)")
        print("=" * 60)

if __name__ == "__main__":
    populate()
