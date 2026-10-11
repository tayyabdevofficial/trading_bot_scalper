"""
Download fresh historical Kline data for the past 2 months (60 days) up to yesterday 23:59:59 UTC.
Fetches 5m and 15m intervals directly from Binance Futures API (fapi.binance.com) with parallel threading.
Saves and updates local cache in data/cache_klines.
"""
import os
import sys
import time
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import Database

BASE_URL = "https://fapi.binance.com/fapi/v1/klines"
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "cache_klines")
os.makedirs(CACHE_DIR, exist_ok=True)

TIMEFRAMES = ["5m", "15m"]

def get_target_date_range(days: int = 60):
    # End date: yesterday 23:59:59 UTC
    now = datetime.now(timezone.utc)
    yesterday = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) - timedelta(seconds=1)
    end_ms = int(yesterday.timestamp() * 1000)
    start_dt = yesterday - timedelta(days=days)
    start_ms = int(start_dt.timestamp() * 1000)
    return start_ms, end_ms, start_dt, yesterday

def fetch_symbol_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })

    all_klines = []
    current_start = start_ms
    limit = 1500
    retries = 0

    while current_start < end_ms:
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": limit
        }
        try:
            resp = session.get(BASE_URL, params=params, timeout=12)
            if resp.status_code == 400:
                # Symbol not available on Binance Futures
                break
            if resp.status_code == 429:
                time.sleep(2.0)
                retries += 1
                if retries > 3:
                    break
                continue
            if resp.status_code != 200:
                break

            data = resp.json()
            if not data or not isinstance(data, list):
                break

            for k in data:
                all_klines.append({
                    "timestamp": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                    "closed": True
                })

            last_time = data[-1][0]
            if last_time <= current_start:
                break
            current_start = last_time + 1

            if len(data) < limit:
                break

            time.sleep(0.04)

        except Exception:
            time.sleep(0.5)
            retries += 1
            if retries > 3:
                break

    if not all_klines:
        return pd.DataFrame()

    df = pd.DataFrame(all_klines)
    df.drop_duplicates(subset=["timestamp"], inplace=True)
    df.sort_values(by="timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

def process_download_job(args):
    symbol, interval, start_ms, end_ms, days = args
    df = fetch_symbol_klines(symbol, interval, start_ms, end_ms)
    if df.empty or len(df) < 50:
        return symbol, interval, 0, "No data / symbol invalid"

    # Save to standard cache filenames (both {days}d and 30d for compatibility)
    p_days = os.path.join(CACHE_DIR, f"{symbol.upper()}_{interval}_{days}d.csv")
    p_30d = os.path.join(CACHE_DIR, f"{symbol.upper()}_{interval}_30d.csv")
    
    df.to_csv(p_days, index=False)
    df.to_csv(p_30d, index=False)

    first_ts = df["timestamp"].iloc[0]
    last_ts = df["timestamp"].iloc[-1]
    return symbol, interval, len(df), f"{first_ts} -> {last_ts}"

def run_download_past_2_months(days: int = 60, max_workers: int = 14):
    db = Database()
    symbols_rows = db.get_symbols_config()
    symbols = sorted([r["symbol"].upper() for r in symbols_rows])

    start_ms, end_ms, start_dt, end_dt = get_target_date_range(days)

    print("=" * 100)
    print(f"DOWNLOADING FRESH 2-MONTH (60 DAYS) KLINE DATA FROM BINANCE FUTURES")
    print(f"Total Symbols: {len(symbols)} | Timeframes: {TIMEFRAMES} | Total Jobs: {len(symbols) * len(TIMEFRAMES)}")
    print(f"Date Range: {start_dt.strftime('%Y-%m-%d %H:%M:%S UTC')} -> {end_dt.strftime('%Y-%m-%d %H:%M:%S UTC')} (Yesterday End)")
    print("=" * 100)

    jobs = []
    for s in symbols:
        for tf in TIMEFRAMES:
            jobs.append((s, tf, start_ms, end_ms, days))

    total = len(jobs)
    completed = 0
    success = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_download_job, job): job for job in jobs}
        for fut in as_completed(futures):
            completed += 1
            sym, tf, _, _, _ = futures[fut]
            try:
                symbol, interval, cnt, info = fut.result()
                if cnt > 0:
                    success += 1
                if completed % 50 == 0 or completed == total:
                    elapsed = round(time.time() - t0, 1)
                    pct = round(completed / total * 100, 1)
                    print(f"[{completed}/{total}] ({pct}%) | Elapsed: {elapsed}s | Success: {success} | Last: {symbol} [{interval}] -> {cnt} candles ({info})", flush=True)
            except Exception as e:
                print(f"Error on {sym} [{tf}]: {e}", flush=True)

    elapsed_total = round(time.time() - t0, 1)
    print("=" * 100)
    print(f"DOWNLOAD COMPLETED in {elapsed_total}s! Successfully fetched & cached {success}/{total} datasets.")
    print("=" * 100)

if __name__ == "__main__":
    run_download_past_2_months(days=60, max_workers=14)
