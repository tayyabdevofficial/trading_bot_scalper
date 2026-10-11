"""
High-Speed Binance Vision Archive & API Downloader for September 1, 2026 -> October 8, 2026
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

START_STR = "2026-09-01 00:00:00"
END_STR = "2026-10-08 23:59:59"
MONTHS = ["2026-09"]
DAILY_DAYS = [f"2026-10-{d:02d}" for d in range(1, 9)]
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

def fetch_from_fapi_fallback(symbol: str, interval: str, start_ts: int, end_ts: int, session: requests.Session) -> pd.DataFrame:
    """Fallback fetch via Binance Futures REST API."""
    url = "https://fapi.binance.com/fapi/v1/klines"
    all_rows = []
    curr_start = start_ts
    
    while curr_start < end_ts:
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "startTime": curr_start,
            "endTime": end_ts,
            "limit": 1000
        }
        try:
            r = session.get(url, params=params, timeout=10)
            if r.status_code != 200:
                break
            data = r.json()
            if not data or not isinstance(data, list):
                break
            all_rows.extend(data)
            last_ts = int(data[-1][0])
            if len(data) < 1000 or last_ts <= curr_start:
                break
            curr_start = last_ts + 1
        except Exception:
            break
            
    if not all_rows:
        return pd.DataFrame()
        
    df = pd.DataFrame(all_rows)
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

def process_symbol_tf(args):
    symbol, interval = args
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })

    dfs = []

    # 1. Fetch monthly archives (September 2026)
    for m in MONTHS:
        df_m = fetch_vision_month(symbol, interval, m, session)
        if not df_m.empty:
            dfs.append(df_m)

    # 2. Fetch daily archives (October 1 to 8, 2026)
    for d_str in DAILY_DAYS:
        df_d = fetch_vision_day(symbol, interval, d_str, session)
        if not df_d.empty:
            dfs.append(df_d)

    # If Vision archive is missing or partial, fallback to REST API
    if not dfs or len(dfs) < (len(MONTHS) + len(DAILY_DAYS)):
        start_ms = int(datetime.strptime(START_STR, "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
        end_ms = int(datetime.strptime(END_STR, "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
        df_fapi = fetch_from_fapi_fallback(symbol, interval, start_ms, end_ms, session)
        if not df_fapi.empty:
            dfs.append(df_fapi)

    if not dfs:
        return symbol, interval, 0, "No data available"

    df_new = pd.concat(dfs, ignore_index=True)
    df_new["timestamp"] = pd.to_datetime(df_new["timestamp"])
    df_new.drop_duplicates(subset=["timestamp"], inplace=True)
    df_new.sort_values(by="timestamp", inplace=True)

    # Merge with any existing earlier history in cache (e.g. June-August) so we never lose historical depth
    p_30d = os.path.join(CACHE_DIR, f"{symbol.upper()}_{interval}_30d.csv")
    p_60d = os.path.join(CACHE_DIR, f"{symbol.upper()}_{interval}_60d.csv")

    existing_dfs = []
    if os.path.exists(p_30d):
        try:
            df_old = pd.read_csv(p_30d)
            if not df_old.empty:
                df_old["timestamp"] = pd.to_datetime(df_old["timestamp"])
                existing_dfs.append(df_old)
        except Exception:
            pass

    existing_dfs.append(df_new)
    df_merged = pd.concat(existing_dfs, ignore_index=True)
    df_merged.drop_duplicates(subset=["timestamp"], inplace=True)
    df_merged.sort_values(by="timestamp", inplace=True)
    df_merged.reset_index(drop=True, inplace=True)
    df_merged["timestamp"] = df_merged["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")

    df_merged.to_csv(p_30d, index=False)
    df_merged.to_csv(p_60d, index=False)

    # Check candles strictly in target window
    df_target = df_merged[(df_merged["timestamp"] >= START_STR) & (df_merged["timestamp"] <= END_STR)]
    count_target = len(df_target)
    min_ts = df_target["timestamp"].iloc[0] if count_target > 0 else "N/A"
    max_ts = df_target["timestamp"].iloc[-1] if count_target > 0 else "N/A"

    return symbol, interval, count_target, f"{min_ts} -> {max_ts} (Total in file: {len(df_merged)})"

def run_download(max_workers: int = 25):
    db = Database()
    symbols_rows = db.get_symbols_config()
    symbols = sorted([r["symbol"].upper() for r in symbols_rows])

    tasks = []
    for s in symbols:
        for tf in TIMEFRAMES:
            tasks.append((s, tf))

    total = len(tasks)
    print("=" * 110)
    print(f"HIGH-SPEED DOWNLOAD & CACHE UPDATE ({START_STR} -> {END_STR})")
    print(f"Total Symbols: {len(symbols)} | Timeframes: {TIMEFRAMES} | Total Jobs: {total} | Workers: {max_workers}")
    print("=" * 110)

    completed = 0
    success = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_symbol_tf, t): t for t in tasks}
        for fut in as_completed(futures):
            completed += 1
            sym, tf, count, info = fut.result()
            if count > 0:
                success += 1
                status = f"[OK] {count} candles ({info})"
            else:
                status = f"[FAILED] {info}"

            if completed % 50 == 0 or completed == total:
                elapsed = round(time.time() - t0, 1)
                rate = round(completed / max(1.0, time.time() - t0), 1)
                print(f"[{completed:>4}/{total}] ({completed*100//total:>3}%) | {sym:<12} {tf:<4} | {status} | Elapsed: {elapsed}s ({rate} jobs/s)", flush=True)

    total_time = round(time.time() - t0, 1)
    print("=" * 110)
    print(f"DOWNLOAD FINISHED in {total_time}s! Result: {success}/{total} datasets ready.")
    print("=" * 110)

if __name__ == "__main__":
    run_download(max_workers=25)
