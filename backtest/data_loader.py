import os
import time
import requests
import pandas as pd
from datetime import datetime, timedelta

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "cache_klines")
os.makedirs(CACHE_DIR, exist_ok=True)

class KlineDataLoader:
    BASE_URL = "https://fapi.binance.com/fapi/v1/klines"

    @classmethod
    def get_cache_path(cls, symbol: str, interval: str, days: int = 30) -> str:
        return os.path.join(CACHE_DIR, f"{symbol.upper()}_{interval}_{days}d.csv")

    @classmethod
    def load_klines(cls, symbol: str, interval: str, days: int = 30, force_refresh: bool = False) -> pd.DataFrame:
        """
        Load historical klines for symbol and interval.
        Uses local CSV cache if available; otherwise fetches from Binance fapi and caches locally.
        """
        cache_path = cls.get_cache_path(symbol, interval, days)
        fallback_30d = cls.get_cache_path(symbol, interval, 30)

        for path in [cache_path, fallback_30d]:
            if not force_refresh and os.path.exists(path):
                try:
                    df = pd.read_csv(path)
                    if not df.empty and len(df) > 50:
                        df["timestamp"] = pd.to_datetime(df["timestamp"])
                        return df
                except Exception as e:
                    pass

        # Fetch from Binance fapi
        df = cls._fetch_from_binance(symbol, interval, days)
        if not df.empty:
            df.to_csv(cache_path, index=False)
        return df

    @classmethod
    def _fetch_from_binance(cls, symbol: str, interval: str, days: int = 30) -> pd.DataFrame:
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - (days * 24 * 60 * 60 * 1000)
        
        all_klines = []
        current_start = start_ms
        limit = 1500

        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })

        retries = 0
        while current_start < now_ms:
            params = {
                "symbol": symbol.upper(),
                "interval": interval,
                "startTime": current_start,
                "limit": limit
            }
            try:
                resp = session.get(cls.BASE_URL, params=params, timeout=10)
                if resp.status_code == 400:
                    # Symbol not found or invalid
                    break
                if resp.status_code == 429:
                    time.sleep(3)
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
                        "timestamp": datetime.fromtimestamp(k[0] / 1000),
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                        "closed": True
                    })

                # Move current_start past the last candle fetched
                last_time = data[-1][0]
                if last_time <= current_start:
                    break
                current_start = last_time + 1

                if len(data) < limit:
                    break

                # Polite pause to protect rate limits
                time.sleep(0.08)

            except Exception:
                break

        if not all_klines:
            return pd.DataFrame()

        df = pd.DataFrame(all_klines)
        df.drop_duplicates(subset=["timestamp"], inplace=True)
        df.sort_values(by="timestamp", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df
