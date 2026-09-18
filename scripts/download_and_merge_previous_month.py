"""
Download and Merge Previous Month Kline Data (2026-07-19 to 2026-08-19)
Uses official Binance Vision archive (data.binance.vision) for ultra-fast, zero-rate-limit historical downloads.
Merges historical candles with the existing dataset, deduplicates, and saves sorted chronological CSV files.
"""
import io
import os
import sys
import time
import zipfile
import requests
import pandas as pd
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import Database

VISION_URL = "https://data.binance.vision/data/futures/um/monthly/klines"
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "cache_klines")
os.makedirs(CACHE_DIR, exist_ok=True)

START_STR = "2026-07-19 00:00:00"
END_STR = "2026-08-19 00:00:00"
MONTHS = ["2026-07", "2026-08"]
TIMEFRAMES = ["5m", "15m"]

def download_vision_month(symbol: str, interval: str, month_str: str) -> pd.DataFrame:
    url = f"{VISION_URL}/{symbol.upper()}/{interval}/{symbol.upper()}-{interval}-{month_str}.zip"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            return pd.DataFrame()

        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            fname = z.namelist()[0]
            with z.open(fname) as f:
                df = pd.read_csv(f, header=None)

        if df.empty:
            return pd.DataFrame()

        # Handle header row if present
        if str(df.iloc[0, 0]).lower() in ("open_time", "open time", "timestamp"):
            df = df.iloc[1:].copy()

        df = df[pd.to_numeric(df[0], errors="coerce").notnull()].copy()
        df[0] = df[0].astype("int64")

        # Convert to standard columns
        df_out = pd.DataFrame({
            "timestamp": pd.to_datetime(df[0], unit="ms").dt.strftime("%Y-%m-%d %H:%M:%S"),
            "open": pd.to_numeric(df[1], errors="coerce").astype(float),
            "high": pd.to_numeric(df[2], errors="coerce").astype(float),
            "low": pd.to_numeric(df[3], errors="coerce").astype(float),
            "close": pd.to_numeric(df[4], errors="coerce").astype(float),
            "volume": pd.to_numeric(df[5], errors="coerce").astype(float),
            "closed": True
        })
        return df_out
    except Exception:
        return pd.DataFrame()

def process_symbol_tf(args):
    symbol, interval = args
    csv_path = os.path.join(CACHE_DIR, f"{symbol.upper()}_{interval}_30d.csv")

    # 1. Read existing CSV if exists
    df_existing = pd.DataFrame()
    if os.path.exists(csv_path):
        try:
            df_existing = pd.read_csv(csv_path)
            if not df_existing.empty and "timestamp" in df_existing.columns:
                df_existing["timestamp"] = df_existing["timestamp"].astype(str)
                # Check if already starts on or before 2026-07-19
                if df_existing["timestamp"].min() <= "2026-07-19 01:00:00":
                    return symbol, interval, len(df_existing), f"Already merged ({df_existing['timestamp'].min()} -> {df_existing['timestamp'].max()})"
        except Exception:
            pass

    # 2. Fetch months from Binance Vision
    monthly_dfs = []
    for m in MONTHS:
        df_m = download_vision_month(symbol, interval, m)
        if not df_m.empty:
            monthly_dfs.append(df_m)

    if not monthly_dfs and df_existing.empty:
        return symbol, interval, 0, "No data available"

    # 3. Filter downloaded monthly data to target 2026-07-19 -> 2026-08-19
    df_hist = pd.DataFrame()
    if monthly_dfs:
        df_all_hist = pd.concat(monthly_dfs, ignore_index=True)
        df_hist = df_all_hist[(df_all_hist["timestamp"] >= START_STR) & (df_all_hist["timestamp"] < END_STR)].copy()

    # 4. Merge historical + existing
    if not df_existing.empty and not df_hist.empty:
        df_merged = pd.concat([df_hist, df_existing], ignore_index=True)
    elif not df_existing.empty:
        df_merged = df_existing
    elif not df_hist.empty:
        df_merged = df_hist
    else:
        return symbol, interval, 0, "No valid data"

    # 5. Clean, deduplicate, sort chronologically
    df_merged["timestamp"] = pd.to_datetime(df_merged["timestamp"])
    df_merged.drop_duplicates(subset=["timestamp"], inplace=True)
    df_merged.sort_values(by="timestamp", inplace=True)
    df_merged.reset_index(drop=True, inplace=True)

    # Convert timestamp back to string format
    df_merged["timestamp"] = df_merged["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")

    # 6. Save to CSV
    df_merged.to_csv(csv_path, index=False)
    return symbol, interval, len(df_merged), f"{df_merged['timestamp'].min()} -> {df_merged['timestamp'].max()}"

def run_download_and_merge():
    db = Database()
    symbols_rows = db.get_symbols_config()
    symbols = sorted([r["symbol"].upper() for r in symbols_rows])

    tasks = []
    for s in symbols:
        for tf in TIMEFRAMES:
            tasks.append((s, tf))

    total = len(tasks)
    print(f"Starting historical data download & merge via Binance Vision for {len(symbols)} symbols across {TIMEFRAMES} ({total} total jobs)...", flush=True)
    print(f"Target Date Coverage: {START_STR} to 2026-09-17 23:55:00", flush=True)

    completed = 0
    success = 0
    t0 = time.time()

    # Use 16 parallel workers since Binance Vision CDN has no API rate limits
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = {executor.submit(process_symbol_tf, t): t for t in tasks}
        for fut in as_completed(futures):
            completed += 1
            sym, tf = futures[fut]
            try:
                symbol, interval, cnt, info = fut.result()
                if cnt > 0:
                    success += 1
                if completed % 50 == 0 or completed == total:
                    elapsed = round(time.time() - t0, 1)
                    print(f"Progress: [{completed}/{total}] ({round(completed/total*100, 1)}%) | Elapsed: {elapsed}s | Last: {symbol} [{interval}] -> {cnt} candles ({info})", flush=True)
            except Exception as e:
                print(f"Error on {sym} [{tf}]: {e}", flush=True)

    total_time = round(time.time() - t0, 1)
    print(f"\nSuccessfully completed historical data download & merge in {total_time}s! Result: {success}/{total} datasets updated.", flush=True)

if __name__ == "__main__":
    run_download_and_merge()
