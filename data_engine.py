import asyncio
import os
import json
import logging
import aiohttp
import websockets
import pandas as pd
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Callable, Optional, Tuple

logger = logging.getLogger("DataEngine")

# Setup dedicated logger for Binance API calls (ERROR only to prevent log bloat)
binance_logger = logging.getLogger("BinanceAPI")
binance_logger.setLevel(logging.ERROR)
binance_logger.propagate = False
if not binance_logger.handlers:
    api_handler = logging.FileHandler("binance_api.log")
    api_handler.setLevel(logging.ERROR)
    api_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    binance_logger.addHandler(api_handler)


class KlineCacheManager:
    """
    Manages local persistent 2-day CSV kline storage and gap-filling.
    Directory: data/live_klines/{symbol}_{interval}.csv
    """
    CACHE_DIR = os.path.join("data", "live_klines")
    INTERVAL_SECONDS = {
        "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
        "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400
    }

    @classmethod
    def _ensure_dir(cls):
        os.makedirs(cls.CACHE_DIR, exist_ok=True)

    @classmethod
    def get_csv_path(cls, symbol: str, interval: str) -> str:
        cls._ensure_dir()
        return os.path.join(cls.CACHE_DIR, f"{symbol.upper()}_{interval}.csv")

    @classmethod
    def get_interval_seconds(cls, interval: str) -> int:
        return cls.INTERVAL_SECONDS.get(interval, 300)

    @classmethod
    async def sync_klines(cls, symbol: str, interval: str, days: int = 2) -> List[dict]:
        """
        Loads 2 days of klines from local CSV.
        If file is missing, downloads 2 days from Binance.
        If file exists with missing gap, downloads ONLY the missing gap candles.
        Prunes candles older than 2 days and saves to CSV.
        """
        cls._ensure_dir()
        csv_path = cls.get_csv_path(symbol, interval)
        now = datetime.now(timezone.utc)
        cutoff_dt = now - timedelta(days=days)
        interval_sec = cls.get_interval_seconds(interval)

        existing_df = pd.DataFrame()
        if os.path.exists(csv_path):
            try:
                df_read = pd.read_csv(csv_path)
                if not df_read.empty and "timestamp" in df_read.columns:
                    df_read["timestamp"] = pd.to_datetime(df_read["timestamp"], utc=True)
                    # Filter out any old data or future-shifted timestamps (e.g. from local timezone bugs)
                    df_read = df_read[(df_read["timestamp"] >= cutoff_dt) & (df_read["timestamp"] <= now + timedelta(minutes=interval_sec/60))].sort_values("timestamp").reset_index(drop=True)
                    existing_df = df_read
            except Exception as e:
                logger.warning(f"[{symbol} {interval}] Error reading local CSV: {e}. Re-fetching.")
                existing_df = pd.DataFrame()

        # Check if we have data and whether there is a gap
        if not existing_df.empty:
            last_dt = existing_df["timestamp"].iloc[-1]
            gap_seconds = (now - last_dt).total_seconds()
            
            # If the last candle is older than 1.2 intervals, fetch gap
            if gap_seconds > (interval_sec * 1.2):
                start_ms = int(last_dt.timestamp() * 1000) + 1
                end_ms = int(now.timestamp() * 1000)
                logger.info(f"[{symbol} {interval}] Local CSV cache found ({len(existing_df)} candles). Filling gap of ~{int(gap_seconds/60)}m...")
                gap_klines = await cls._fetch_binance_range(symbol, interval, start_ms=start_ms, end_ms=end_ms)
                if gap_klines:
                    gap_df = pd.DataFrame(gap_klines)
                    gap_df["timestamp"] = pd.to_datetime(gap_df["timestamp"], utc=True)
                    combined_df = pd.concat([existing_df, gap_df], ignore_index=True)
                    combined_df = combined_df.drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp").reset_index(drop=True)
                    existing_df = combined_df
                    logger.info(f"[{symbol} {interval}] Filled gap with {len(gap_klines)} candles. Total cached: {len(existing_df)}.")

            if len(existing_df) >= 50:
                cls._save_df_to_csv(existing_df, csv_path)
                return cls._df_to_klines_list(existing_df)

        # Full 2-day fetch if missing or insufficient
        start_ms = int(cutoff_dt.timestamp() * 1000)
        end_ms = int(now.timestamp() * 1000)
        logger.info(f"[{symbol} {interval}] Initializing local CSV with 2-day historical klines from Binance...")
        full_klines = await cls._fetch_binance_range(symbol, interval, start_ms=start_ms, end_ms=end_ms)
        if full_klines:
            df = pd.DataFrame(full_klines)
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp").reset_index(drop=True)
            cls._save_df_to_csv(df, csv_path)
            logger.info(f"[{symbol} {interval}] Saved {len(df)} candles (2 days) to {csv_path}.")
            return cls._df_to_klines_list(df)

        return []

    @classmethod
    async def _fetch_binance_range(cls, symbol: str, interval: str, start_ms: int, end_ms: int) -> List[dict]:
        url = "https://fapi.binance.com/fapi/v1/klines"
        klines = []
        current_start = start_ms
        
        async with aiohttp.ClientSession() as session:
            while current_start < end_ms:
                params = {
                    "symbol": symbol.upper(),
                    "interval": interval,
                    "startTime": current_start,
                    "limit": 1000
                }
                try:
                    binance_logger.info(f"REST REQUEST (Klines): GET {url} | Params: {params}")
                    async with session.get(url, params=params, timeout=8) as resp:
                        if resp.status != 200:
                            logger.warning(f"[{symbol} {interval}] Binance API status {resp.status} during range fetch.")
                            break
                        data = await resp.json()
                        if not data:
                            break
                        for k in data:
                            klines.append({
                                "timestamp": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
                                "open": float(k[1]),
                                "high": float(k[2]),
                                "low": float(k[3]),
                                "close": float(k[4]),
                                "volume": float(k[5])
                            })
                        last_kline_ms = data[-1][0]
                        if len(data) < 1000 or last_kline_ms <= current_start:
                            break
                        current_start = last_kline_ms + 1
                except Exception as e:
                    logger.warning(f"[{symbol} {interval}] Error fetching klines range: {e}")
                    break
        return klines

    @classmethod
    def _save_df_to_csv(cls, df: pd.DataFrame, csv_path: str):
        try:
            df_to_save = df.copy()
            if "timestamp" in df_to_save.columns and pd.api.types.is_datetime64_any_dtype(df_to_save["timestamp"]):
                df_to_save["timestamp"] = df_to_save["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
            df_to_save.to_csv(csv_path, index=False, columns=["timestamp", "open", "high", "low", "close", "volume"])
        except Exception as e:
            logger.error(f"Error saving klines to CSV {csv_path}: {e}")

    @classmethod
    def _df_to_klines_list(cls, df: pd.DataFrame) -> List[dict]:
        klines = []
        for _, row in df.iterrows():
            ts = row["timestamp"]
            if isinstance(ts, str):
                ts = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            elif hasattr(ts, "to_pydatetime"):
                ts = ts.to_pydatetime()
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            klines.append({
                "timestamp": ts,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "closed": True
            })
        return klines

    @classmethod
    def append_closed_candle(cls, symbol: str, interval: str, candle: dict):
        """Appends a completed candle to local CSV in standardized UTC."""
        csv_path = cls.get_csv_path(symbol, interval)
        try:
            ts_val = candle["timestamp"]
            if isinstance(ts_val, datetime):
                if ts_val.tzinfo is not None:
                    ts_val = ts_val.astimezone(timezone.utc)
                ts_str = ts_val.strftime("%Y-%m-%d %H:%M:%S")
            else:
                ts_str = str(ts_val)
            line = f"{ts_str},{candle['open']:.8f},{candle['high']:.8f},{candle['low']:.8f},{candle['close']:.8f},{candle['volume']:.8f}\n"
            
            if not os.path.exists(csv_path):
                with open(csv_path, "w", encoding="utf-8") as f:
                    f.write("timestamp,open,high,low,close,volume\n")
                    f.write(line)
            else:
                with open(csv_path, "a", encoding="utf-8") as f:
                    f.write(line)
        except Exception as e:
            logger.error(f"[{symbol} {interval}] Error appending candle to CSV: {e}")


class _SharedMarketStream:
    """
    Singleton connection manager per (symbol, interval).
    Maintains exactly ONE WebSocket connection and ONE cached candlestick buffer.
    Dispatches closed candles and price ticks to all subscribed bots synchronously.
    """
    def __init__(self, symbol: str, interval: str):
        self.symbol = symbol.upper()
        self.interval = interval
        self.ws_url = f"wss://fstream.binance.com/market/ws/{self.symbol.lower()}@kline_{self.interval}"
        self.rest_url = "https://fapi.binance.com/fapi/v1/klines"
        
        self.klines: List[dict] = []
        self.max_klines = 1000  # Holds 2 days of 5m candles (~576) in memory
        self.running = False
        self._ws_task: Optional[asyncio.Task] = None
        self._fetch_task: Optional[asyncio.Task] = None
        self._ready_event = asyncio.Event()
        self._start_lock = asyncio.Lock()
        
        # Subscribers: bot_id/engine -> (candle_callbacks, tick_callbacks)
        self._subscribers: Dict[object, Tuple[List[Callable], List[Callable]]] = {}
        self.received_first_tick = False

    def add_subscriber(self, engine, candle_cb: Optional[Callable] = None, tick_cb: Optional[Callable] = None):
        if engine not in self._subscribers:
            self._subscribers[engine] = ([], [])
        c_list, t_list = self._subscribers[engine]
        if candle_cb and candle_cb not in c_list:
            c_list.append(candle_cb)
        if tick_cb and tick_cb not in t_list:
            t_list.append(tick_cb)

    def remove_subscriber(self, engine):
        if engine in self._subscribers:
            del self._subscribers[engine]

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def ensure_started(self):
        if self._ready_event.is_set():
            return
        async with self._start_lock:
            if not self.running:
                self.running = True
                await self.fetch_historical_data()
                if self._ws_task is None or self._ws_task.done():
                    self._ws_task = asyncio.create_task(self._connect_websocket())
                self._ready_event.set()
            else:
                await self._ready_event.wait()

    async def stop(self):
        self.running = False
        self._ready_event.clear()
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
        logger.info(f"[{self.symbol} {self.interval}] SharedMarketStream stopped.")

    async def fetch_historical_data(self):
        """Load 2 days of historical candles from local CSV and fill any missing gaps."""
        if len(self.klines) >= 50:
            return  # Already populated in memory

        logger.info(f"[{self.symbol} {self.interval}] Syncing 2-day historical klines cache...")
        loaded = await KlineCacheManager.sync_klines(self.symbol, self.interval, days=2)
        if loaded:
            self.klines = loaded[-self.max_klines:]
            logger.info(f"[{self.symbol} {self.interval}] Successfully loaded {len(self.klines)} candles from 2-day CSV cache.")
        else:
            logger.warning(f"[{self.symbol} {self.interval}] Could not load historical candles from cache.")

    def get_dataframe(self) -> pd.DataFrame:
        if not self.klines:
            return pd.DataFrame()
        return pd.DataFrame(self.klines)

    async def _fill_runtime_gap(self):
        """Fetch any missing candles from Binance REST API if connection was temporarily interrupted."""
        if not self.klines:
            return
        try:
            last_dt = self.klines[-1]["timestamp"]
            now = datetime.now(timezone.utc)
            gap_seconds = (now - last_dt).total_seconds()
            interval_sec = KlineCacheManager.get_interval_seconds(self.interval)
            if gap_seconds > (interval_sec * 1.5):
                start_ms = int(last_dt.timestamp() * 1000) + 1
                end_ms = int(now.timestamp() * 1000)
                logger.info(f"[{self.symbol} {self.interval}] Gap of ~{int(gap_seconds/60)}m detected. Fetching missing candles from REST API...")
                gap_klines = await KlineCacheManager._fetch_binance_range(self.symbol, self.interval, start_ms=start_ms, end_ms=end_ms)
                if gap_klines:
                    for k in gap_klines:
                        if not self.klines or k["timestamp"] > self.klines[-1]["timestamp"]:
                            self.klines.append(k)
                            KlineCacheManager.append_closed_candle(self.symbol, self.interval, k)
                        elif k["timestamp"] == self.klines[-1]["timestamp"]:
                            self.klines[-1] = k
                    if len(self.klines) > self.max_klines:
                        self.klines = self.klines[-self.max_klines:]
                    logger.info(f"[{self.symbol} {self.interval}] Gap filled with {len(gap_klines)} candles.")
        except Exception as e:
            logger.warning(f"[{self.symbol} {self.interval}] Error in _fill_runtime_gap: {e}")

    async def _connect_websocket(self):
        import random
        while self.running:
            try:
                logger.info(f"[{self.symbol} {self.interval}] Connecting to Shared WebSocket: {self.ws_url}")
                ws = await asyncio.wait_for(
                    websockets.connect(
                        self.ws_url,
                        open_timeout=10,
                        ping_interval=20,
                        ping_timeout=20,
                        close_timeout=5
                    ),
                    timeout=15
                )
                async with ws:
                    self.received_first_tick = False
                    # On connect, ensure no candles were missed during reconnect window
                    await self._fill_runtime_gap()
                    
                    while self.running:
                        try:
                            # 60-second timeout allows quieter altcoin markets without premature disconnects
                            message = await asyncio.wait_for(ws.recv(), timeout=60)
                        except asyncio.TimeoutError:
                            logger.info(f"[{self.symbol} {self.interval}] WebSocket idle (no trades for 60s). Checking connection health...")
                            await self._fill_runtime_gap()
                            continue
                        
                        data = json.loads(message)
                        if not self.received_first_tick:
                            self.received_first_tick = True
                            logger.info(f"[{self.symbol} {self.interval}] Successfully receiving real-time shared price feed.")

                        kline_data = data.get("k")
                        if not kline_data or "t" not in kline_data:
                            continue
                        is_closed = kline_data.get("x", False)

                        candle = {
                            "timestamp": datetime.fromtimestamp(kline_data.get("t") / 1000, tz=timezone.utc),
                            "open": float(kline_data.get("o")),
                            "high": float(kline_data.get("h")),
                            "low": float(kline_data.get("l")),
                            "close": float(kline_data.get("c")),
                            "volume": float(kline_data.get("v")),
                            "closed": is_closed
                        }

                        # Update in-progress candle or append
                        if self.klines:
                            if self.klines[-1]["timestamp"] == candle["timestamp"]:
                                self.klines[-1] = candle
                            else:
                                self.klines.append(candle)
                        else:
                            self.klines.append(candle)

                        if len(self.klines) > self.max_klines:
                            self.klines.pop(0)

                        curr_close = candle["close"]

                        # Append closed candle to local persistent CSV storage
                        if is_closed:
                            KlineCacheManager.append_closed_candle(self.symbol, self.interval, candle)

                        # Dispatch tick to all subscribers
                        for engine, (candle_cbs, tick_cbs) in list(self._subscribers.items()):
                            for t_cb in tick_cbs:
                                try:
                                    if asyncio.iscoroutinefunction(t_cb):
                                        asyncio.create_task(t_cb(curr_close))
                                    else:
                                        t_cb(curr_close)
                                except Exception as t_err:
                                    logger.error(f"Error in subscriber tick callback: {t_err}")

                        # If candle is closed, snapshot dataframe and dispatch to all subscribers
                        if is_closed:
                            df_snapshot = self.get_dataframe()
                            logger.info(f"[{self.symbol} {self.interval}] Candle closed at {curr_close}. Broadcasting {len(df_snapshot)} candles to {len(self._subscribers)} subscribers.")
                            for engine, (candle_cbs, tick_cbs) in list(self._subscribers.items()):
                                for c_cb in candle_cbs:
                                    try:
                                        if asyncio.iscoroutinefunction(c_cb):
                                            asyncio.create_task(c_cb(df_snapshot.copy()))
                                        else:
                                            c_cb(df_snapshot.copy())
                                    except Exception as c_err:
                                        logger.error(f"Error in subscriber candle callback: {c_err}")

                    if not self.running:
                        break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[{self.symbol} {self.interval}] Shared WebSocket notice: {e}")

            if self.running:
                delay = random.uniform(2.0, 5.0)
                await asyncio.sleep(delay)


class SharedMarketDataManager:
    """Registry maintaining active shared market streams per (symbol, interval)."""
    _instance = None
    _streams: Dict[Tuple[str, str], _SharedMarketStream] = {}

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    async def get_stream(cls, symbol: str, interval: str) -> _SharedMarketStream:
        key = (symbol.upper(), interval)
        if key not in cls._streams:
            cls._streams[key] = _SharedMarketStream(symbol, interval)
        return cls._streams[key]

    @classmethod
    async def release_stream(cls, symbol: str, interval: str, engine):
        key = (symbol.upper(), interval)
        if key in cls._streams:
            stream = cls._streams[key]
            stream.remove_subscriber(engine)
            if stream.subscriber_count == 0:
                await stream.stop()
                del cls._streams[key]


class DataEngine:
    def __init__(self, symbol, interval, db=None, use_testnet=None, simulation_mode=None):
        self.symbol = symbol.upper()
        self.interval = interval
        self.db = db
        self.use_testnet = use_testnet
        self.simulation_mode = simulation_mode
        self.max_klines = 100
        self.running = False
        
        self.callbacks = []
        self.tick_callbacks = []
        self._shared_stream: Optional[_SharedMarketStream] = None

    @property
    def klines(self) -> list:
        if self._shared_stream:
            return self._shared_stream.klines
        return []

    def register_callback(self, callback):
        """Register a callback function that runs when a candle closes."""
        self.callbacks.append(callback)
        if self._shared_stream:
            self._shared_stream.add_subscriber(self, candle_cb=callback)

    def register_tick_callback(self, callback):
        """Register a callback function that runs on every price tick."""
        self.tick_callbacks.append(callback)
        if self._shared_stream:
            self._shared_stream.add_subscriber(self, tick_cb=callback)

    def get_dataframe(self) -> pd.DataFrame:
        """Return the current candles as a Pandas DataFrame."""
        if self._shared_stream:
            return self._shared_stream.get_dataframe()
        return pd.DataFrame()

    async def start(self):
        self.running = True
        self._shared_stream = await SharedMarketDataManager.get_stream(self.symbol, self.interval)
        for c_cb in self.callbacks:
            self._shared_stream.add_subscriber(self, candle_cb=c_cb)
        for t_cb in self.tick_callbacks:
            self._shared_stream.add_subscriber(self, tick_cb=t_cb)
        await self._shared_stream.ensure_started()

    async def stop(self):
        self.running = False
        logger.info(f"[{self.symbol} {self.interval}] Stopping DataEngine subscriber...")
        await SharedMarketDataManager.release_stream(self.symbol, self.interval, self)
        self._shared_stream = None

    @staticmethod
    def calculate_atr(df, period=14):
        """Calculate Average True Range (ATR) on OHLCV DataFrame."""
        if len(df) < period + 1:
            return pd.Series([0.0] * len(df))
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        return atr.fillna(0.0)

    @staticmethod
    def calculate_chop(df, period=14):
        """Calculate Choppiness Index (CHOP) on OHLCV DataFrame."""
        if len(df) < period:
            return pd.Series([50.0] * len(df))
        import numpy as np
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        tr_sum = tr.rolling(window=period).sum()
        max_high = high.rolling(window=period).max()
        min_low = low.rolling(window=period).min()
        
        high_low_range = max_high - min_low
        high_low_range = high_low_range.replace(0, 1e-9)
        
        chop = 100 * (np.log10(tr_sum / high_low_range)) / np.log10(period)
        return chop.fillna(50.0)

    @staticmethod
    async def fetch_external_klines(symbol, interval, limit=100):
        """Fetch historical candles for any timeframe from local 2-day CSV cache / Binance."""
        try:
            klines = await KlineCacheManager.sync_klines(symbol, interval, days=2)
            if klines:
                df = pd.DataFrame(klines)
                return df.iloc[-limit:] if len(df) > limit else df
        except Exception as e:
            logger.error(f"Error fetching cached klines for {symbol} on {interval}: {e}")
        return pd.DataFrame()
