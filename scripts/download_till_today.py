"""
High-Speed Binance Vision Archive & API Downloader to update cache till today (October 15, 2026)
Downloads daily archives from October 9 to October 14, and current live candles for October 15.
Merges seamlessly into data/cache_klines (*_30d.csv and *_60d.csv).
"""
import io
import os
import sys
import time
import zipfile
import requests
import pandas as pd
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import Database

VISION_DAILY = "https://data.binance.vision/data/futures/um/daily/klines"
FAPI_URL = "https://fapi.binance.com/fapi/v1/klines"
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "cache_klines")
os.makedirs(CACHE_DIR, exist_ok=True)

DAILY_DAYS = [f"2026-10-{d:02d}" for d in range(9, 15)]  # 2026-10-09 through 2026-10-14
TODAY_STR = "2026-10-15"
TIMEFRAMES = ["5m", "15m"]

def parse_zip_content(content: bytes) -> pd.DataFrame:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            fname = z.namelist()[0]
            with z.open(fname) as f:
                df = pd.read_csv(f, header=None)

        if df.empty:
            return pd.DataFrame()

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

def fetch_vision_day(symbol: str, interval: str, day_str: str, session: requests.Session) -> pd.DataFrame:
    url = f"{VISION_DAILY}/{symbol.upper()}/{interval}/{symbol.upper()}-{interval}-{day_str}.zip"
    try:
        r = session.get(url, timeout=8)
        if r.status_code == 200:
            return parse_zip_content(r.content)
    except Exception:
        pass
    return pd.DataFrame()

def fetch_today_klines(symbol: str, interval: str, session: requests.Session) -> pd.DataFrame:
    try:
        start_ms = int(datetime.strptime(f"{TODAY_STR} 00:00:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "startTime": start_ms,
            "limit": 1000
        }
        r = session.get(FAPI_URL, params=params, timeout=8)
        if r.status_code == 200:
            data = r.json()
            if data and isinstance(data, list):
                df = pd.DataFrame(data)
                return pd.DataFrame({
                    "timestamp": pd.to_datetime(df[0], unit="ms").dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "open": pd.to_numeric(df[1], errors="coerce").astype(float),
                    "high": pd.to_numeric(df[2], errors="coerce").astype(float),
                    "low": pd.to_numeric(df[3], errors="coerce").astype(float),
                    "close": pd.to_numeric(df[4], errors="coerce").astype(float),
                    "volume": pd.to_numeric(df[5], errors="coerce").astype(float),
                    "closed": True
                })
    except Exception:
        pass
    return pd.DataFrame()

def process_symbol_tf(args):
    symbol, interval = args
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })

    dfs = []

    # 1. Fetch daily archives (October 9 to 14, 2026)
    for d_str in DAILY_DAYS:
        df_d = fetch_vision_day(symbol, interval, d_str, session)
        if not df_d.empty:
            dfs.append(df_d)

    # 2. Fetch today's candles (October 15, 2026)
    df_today = fetch_today_klines(symbol, interval, session)
    if not df_today.empty:
        dfs.append(df_today)

    if not dfs:
        return symbol, interval, 0, "No new data"

    df_new = pd.concat(dfs, ignore_index=True)
    df_new["timestamp"] = pd.to_datetime(df_new["timestamp"])
    df_new.drop_duplicates(subset=["timestamp"], inplace=True)
    df_new.sort_values(by="timestamp", inplace=True)

    # Merge with existing cache files
    p_30d = os.path.join(CACHE_DIR, f"{symbol.upper()}_{interval}_30d.csv")
    p_60d = os.path.join(CACHE_DIR, f"{symbol.upper()}_{interval}_60d.csv")

    existing_dfs = []
    if os.path.exists(p_60d):
        try:
            df_old = pd.read_csv(p_60d)
            if not df_old.empty:
                df_old["timestamp"] = pd.to_datetime(df_old["timestamp"])
                existing_dfs.append(df_old)
        except Exception:
            pass
    elif os.path.exists(p_30d):
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

    min_ts = df_merged["timestamp"].iloc[0]
    max_ts = df_merged["timestamp"].iloc[-1]
    return symbol, interval, len(df_merged), f"{min_ts} -> {max_ts}"

def run_update(max_workers: int = 25):
    import glob
    files = glob.glob(os.path.join(CACHE_DIR, "*_5m_60d.csv"))
    symbols = sorted(list(set(os.path.basename(f).replace("_5m_60d.csv", "").upper() for f in files)))

    if not symbols:
        db = Database()
        symbols_rows = db.get_symbols_config()
        symbols = sorted([r["symbol"].upper() for r in symbols_rows])

    tasks = []
    for s in symbols:
        for tf in TIMEFRAMES:
            tasks.append((s, tf))

    total = len(tasks)
    print("=" * 100)
    print(f"UPDATING KLINE CACHE TILL TODAY ({TODAY_STR})")
    print(f"Total Symbols: {len(symbols)} | Timeframes: {TIMEFRAMES} | Total Tasks: {total} | Workers: {max_workers}")
    print("=" * 100)

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
            if completed % 100 == 0 or completed == total:
                elapsed = time.time() - t0
                print(f"[{completed}/{total}] ({completed/total*100:.1f}%) Completed in {elapsed:.1f}s | Success: {success}")

    print("=" * 100)
    print(f"CACHE UPDATE COMPLETE: {success}/{total} successfully updated in {time.time()-t0:.1f}s")
    print("=" * 100)

if __name__ == "__main__":
    run_update(max_workers=25)
