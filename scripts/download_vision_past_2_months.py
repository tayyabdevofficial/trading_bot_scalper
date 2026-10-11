"""
Download and Assemble Past 2 Months (July 26, 2026 to September 24, 2026) Kline Data
Using official Binance Vision archive (data.binance.vision) with high-speed parallel workers.
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

VISION_MONTHLY = "https://data.binance.vision/data/futures/um/monthly/klines"
VISION_DAILY = "https://data.binance.vision/data/futures/um/daily/klines"
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "cache_klines")
os.makedirs(CACHE_DIR, exist_ok=True)

START_STR = "2026-07-26 00:00:00"
END_STR = "2026-09-24 23:59:59"
MONTHS = ["2026-07", "2026-08"]
DAILY_DAYS = [f"2026-09-{d:02d}" for d in range(1, 25)]
TIMEFRAMES = ["5m", "15m"]

def parse_zip_content(content: bytes) -> pd.DataFrame:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            fname = z.namelist()[0]
            with z.open(fname) as f:
                df = pd.read_csv(f, header=None)

        if df.empty:
            return pd.DataFrame()

        # Check if first row is header
        if str(df.iloc[0, 0]).lower() in ("open_time", "open time", "timestamp"):
            df = df.iloc[1:].copy()

        df = df[pd.to_numeric(df[0], errors="coerce").notnull()].copy()
        df[0] = df[0].astype("int64")

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

def fetch_vision_month(symbol: str, interval: str, month_str: str, session: requests.Session) -> pd.DataFrame:
    url = f"{VISION_MONTHLY}/{symbol.upper()}/{interval}/{symbol.upper()}-{interval}-{month_str}.zip"
    try:
        resp = session.get(url, timeout=12)
        if resp.status_code == 200:
            return parse_zip_content(resp.content)
    except Exception:
        pass
    return pd.DataFrame()

def fetch_vision_day(symbol: str, interval: str, day_str: str, session: requests.Session) -> pd.DataFrame:
    url = f"{VISION_DAILY}/{symbol.upper()}/{interval}/{symbol.upper()}-{interval}-{day_str}.zip"
    try:
        resp = session.get(url, timeout=12)
        if resp.status_code == 200:
            return parse_zip_content(resp.content)
    except Exception:
        pass
    return pd.DataFrame()

def process_symbol_tf_vision(args):
    symbol, interval = args
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })

    dfs = []

    # 1. Fetch monthly archives (July & August 2026)
    for m in MONTHS:
        df_m = fetch_vision_month(symbol, interval, m, session)
        if not df_m.empty:
            dfs.append(df_m)

    # 2. Fetch daily archives (September 1 to 24, 2026)
    for d_str in DAILY_DAYS:
        df_d = fetch_vision_day(symbol, interval, d_str, session)
        if not df_d.empty:
            dfs.append(df_d)

    if not dfs:
        return symbol, interval, 0, "No data available on Vision archive"

    df_combined = pd.concat(dfs, ignore_index=True)
    df_combined["timestamp"] = pd.to_datetime(df_combined["timestamp"])
    df_combined.drop_duplicates(subset=["timestamp"], inplace=True)
    df_combined.sort_values(by="timestamp", inplace=True)
    df_combined.reset_index(drop=True, inplace=True)

    # Filter to exact 60-day window: July 26, 2026 00:00:00 to September 24, 2026 23:59:59
    df_filtered = df_combined[(df_combined["timestamp"] >= START_STR) & (df_combined["timestamp"] <= END_STR)].copy()
    if df_filtered.empty:
        df_filtered = df_combined.copy()

    df_filtered["timestamp"] = df_filtered["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")

    # Save to standard cache locations
    p_60d = os.path.join(CACHE_DIR, f"{symbol.upper()}_{interval}_60d.csv")
    p_30d = os.path.join(CACHE_DIR, f"{symbol.upper()}_{interval}_30d.csv")
    
    df_filtered.to_csv(p_60d, index=False)
    df_filtered.to_csv(p_30d, index=False)

    min_ts = df_filtered["timestamp"].iloc[0]
    max_ts = df_filtered["timestamp"].iloc[-1]
    return symbol, interval, len(df_filtered), f"{min_ts} -> {max_ts}"

def run_vision_download(max_workers: int = 20):
    db = Database()
    symbols_rows = db.get_symbols_config()
    symbols = sorted([r["symbol"].upper() for r in symbols_rows])

    tasks = []
    for s in symbols:
        for tf in TIMEFRAMES:
            tasks.append((s, tf))

    total = len(tasks)
    print("=" * 110)
    print(f"HIGH-SPEED BINANCE VISION ARCHIVE DOWNLOAD (PAST 2 MONTHS: {START_STR} -> {END_STR})")
    print(f"Total Symbols: {len(symbols)} | Timeframes: {TIMEFRAMES} | Total Jobs: {total} | Workers: {max_workers}")
    print("=" * 110)

    completed = 0
    success = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_symbol_tf_vision, t): t for t in tasks}
        for fut in as_completed(futures):
            completed += 1
            sym, tf = futures[fut]
            try:
                symbol, interval, cnt, info = fut.result()
                if cnt > 0:
                    success += 1
                if completed % 50 == 0 or completed == total:
                    elapsed = round(time.time() - t0, 1)
                    pct = round(completed / total * 100, 1)
                    print(f"Progress: [{completed}/{total}] ({pct}%) | Elapsed: {elapsed}s | Success: {success} | Last: {symbol} [{interval}] -> {cnt} candles ({info})", flush=True)
            except Exception as e:
                print(f"Error on {sym} [{tf}]: {e}", flush=True)

    elapsed_total = round(time.time() - t0, 1)
    print("=" * 110)
    print(f"COMPLETED in {elapsed_total}s! Successfully fetched & saved {success}/{total} datasets ({START_STR} to {END_STR}).")
    print("=" * 110)

if __name__ == "__main__":
    run_vision_download(max_workers=20)
