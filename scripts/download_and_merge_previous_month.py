"""
Download Historical Kline Data from 1st June 2026 to 25th August 2026 from Binance Vision
and Merge with Existing Cache Data (26th August 2026 to 24th September 2026)
Resulting in complete historical dataset spanning 1st June 2026 to 24th September 2026.
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
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "cache_klines")
os.makedirs(CACHE_DIR, exist_ok=True)

# Historical range to download & merge
START_HIST_STR = "2026-06-01 00:00:00"
END_HIST_STR = "2026-08-25 23:59:59"
MONTHS = ["2026-06", "2026-07", "2026-08"]
TIMEFRAMES = ["5m", "15m"]

def parse_zip_content(content: bytes) -> pd.DataFrame:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
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

        # Standard Binance futures kline format
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
        resp = session.get(url, timeout=15)
        if resp.status_code == 200:
            return parse_zip_content(resp.content)
    except Exception:
        pass
    return pd.DataFrame()

def process_symbol_tf(args):
    symbol, interval = args
    csv_path = os.path.join(CACHE_DIR, f"{symbol.upper()}_{interval}_30d.csv")
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })

    # 1. Read existing cache data (contains 2026-08-26 to 2026-09-24)
    df_existing = pd.DataFrame()
    if os.path.exists(csv_path):
        try:
            df_existing = pd.read_csv(csv_path)
            if not df_existing.empty and "timestamp" in df_existing.columns:
                df_existing["timestamp"] = df_existing["timestamp"].astype(str)
                # Check if already starts on or before 2026-06-01
                if df_existing["timestamp"].min() <= "2026-06-01 01:00:00":
                    return symbol, interval, len(df_existing), f"Already merged ({df_existing['timestamp'].min()} -> {df_existing['timestamp'].max()})"
        except Exception:
            pass

    # 2. Fetch monthly archives (June, July, August 2026)
    monthly_dfs = []
    for m in MONTHS:
        df_m = fetch_vision_month(symbol, interval, m, session)
        if not df_m.empty:
            monthly_dfs.append(df_m)

    if not monthly_dfs and df_existing.empty:
        return symbol, interval, 0, "No data available on Binance Vision"

    # 3. Filter downloaded monthly data to target 2026-06-01 -> 2026-08-25
    df_hist = pd.DataFrame()
    if monthly_dfs:
        df_all_hist = pd.concat(monthly_dfs, ignore_index=True)
        df_hist = df_all_hist[(df_all_hist["timestamp"] >= START_HIST_STR) & (df_all_hist["timestamp"] <= END_HIST_STR)].copy()

    # 4. Merge historical (June 1 - Aug 25) + existing (Aug 26 - Sep 24)
    if not df_existing.empty and not df_hist.empty:
        df_merged = pd.concat([df_hist, df_existing], ignore_index=True)
    elif not df_existing.empty:
        df_merged = df_existing
    elif not df_hist.empty:
        df_merged = df_hist
    else:
        return symbol, interval, 0, "No valid data to merge"

    # 5. Clean, deduplicate and sort chronologically
    df_merged["timestamp"] = pd.to_datetime(df_merged["timestamp"])
    df_merged.drop_duplicates(subset=["timestamp"], inplace=True)
    df_merged.sort_values(by="timestamp", inplace=True)
    df_merged.reset_index(drop=True, inplace=True)
    df_merged["timestamp"] = df_merged["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")

    # 6. Save to CSV
    df_merged.to_csv(csv_path, index=False)
    min_t = df_merged["timestamp"].min()
    max_t = df_merged["timestamp"].max()
    return symbol, interval, len(df_merged), f"{min_t} -> {max_t}"

def run_download_and_merge():
    db = Database()
    symbols_rows = db.get_symbols_config()
    symbols = sorted([r["symbol"].upper() for r in symbols_rows])

    tasks = []
    for s in symbols:
        for tf in TIMEFRAMES:
            tasks.append((s, tf))

    total = len(tasks)
    print(f"Starting download of June 1 to August 25, 2026 data & merging into existing dataset for {len(symbols)} symbols across {TIMEFRAMES} ({total} total jobs)...", flush=True)
    print(f"Historical Download Target: {START_HIST_STR} -> {END_HIST_STR}", flush=True)
    print(f"Final Merged Target: 2026-06-01 00:00:00 -> 2026-09-24 23:59:59 (~116 Days)", flush=True)

    completed = 0
    success = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=24) as executor:
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
    print(f"\nSuccessfully completed download and merge in {total_time}s! Result: {success}/{total} datasets merged.", flush=True)

if __name__ == "__main__":
    run_download_and_merge()
