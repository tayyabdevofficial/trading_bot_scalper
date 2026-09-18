import asyncio
import json
import logging
import aiohttp
import websockets
import pandas as pd
from datetime import datetime
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
        self.max_klines = 100
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
        """Fetch historical candles once to initialize indicators for all subscriber bots."""
        if len(self.klines) >= 50:
            return  # Already populated

        params = {
            "symbol": self.symbol,
            "interval": self.interval,
            "limit": self.max_klines
        }
        msg = f"[{self.symbol} {self.interval}] Fetching shared historical candles..."
        logger.info(msg)

        import random
        attempts = 0
        while self.running and attempts < 10:
            attempts += 1
            async with aiohttp.ClientSession() as session:
                try:
                    binance_logger.info(f"REST REQUEST (Historical): GET {self.rest_url} | Params: {params}")
                    async with session.get(self.rest_url, params=params, timeout=6) as resp:
                        binance_logger.info(f"REST RESPONSE (Historical): HTTP {resp.status}")
                        if resp.status == 200:
                            data = await resp.json()
                            self._parse_klines(data)
                            return
                        else:
                            logger.warning(f"[{self.symbol}] Futures API returned status {resp.status}. Retrying...")
                except Exception as e:
                    logger.warning(f"[{self.symbol}] Futures API failed ({e}). Retrying...")
                    binance_logger.error(f"REST ERROR (Historical) Failed: {e}")

            await asyncio.sleep(random.uniform(2.0, 5.0))

    def _parse_klines(self, data):
        self.klines = []
        for k in data:
            self.klines.append({
                "timestamp": datetime.fromtimestamp(k[0] / 1000),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "closed": True
            })
        logger.info(f"[{self.symbol} {self.interval}] Successfully loaded {len(self.klines)} shared historical candles.")

    def get_dataframe(self) -> pd.DataFrame:
        if not self.klines:
            return pd.DataFrame()
        return pd.DataFrame(self.klines)

    async def _connect_websocket(self):
        import random
        while self.running:
            try:
                logger.info(f"[{self.symbol} {self.interval}] Connecting to Shared WebSocket: {self.ws_url}")
                ws = await asyncio.wait_for(websockets.connect(self.ws_url, open_timeout=6), timeout=10)
                async with ws:
                    self.received_first_tick = False
                    while self.running:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=12)
                        except asyncio.TimeoutError:
                            logger.warning(f"[{self.symbol} {self.interval}] No data received for 12s. Reconnecting...")
                            break
                        
                        data = json.loads(message)
                        if not self.received_first_tick:
                            self.received_first_tick = True
                            logger.info(f"[{self.symbol} {self.interval}] Successfully receiving real-time shared price feed.")

                        kline_data = data.get("k")
                        if not kline_data or "t" not in kline_data:
                            continue
                        is_closed = kline_data.get("x", False)

                        candle = {
                            "timestamp": datetime.fromtimestamp(kline_data.get("t") / 1000),
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
                            logger.info(f"[{self.symbol} {self.interval}] Candle closed at {curr_close}. Broadcasting to {len(self._subscribers)} subscribers.")
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
                logger.warning(f"[{self.symbol} {self.interval}] Shared WebSocket error: {e}")

            if self.running:
                delay = random.uniform(3.0, 7.0)
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
        """Fetch historical candles for any timeframe from Binance REST API."""
        url = "https://fapi.binance.com/fapi/v1/klines"
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": limit
        }
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, params=params, timeout=8) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        klines = []
                        for k in data:
                            klines.append({
                                "timestamp": datetime.fromtimestamp(k[0] / 1000),
                                "open": float(k[1]),
                                "high": float(k[2]),
                                "low": float(k[3]),
                                "close": float(k[4]),
                                "volume": float(k[5]),
                                "closed": True
                            })
                        return pd.DataFrame(klines)
            except Exception as e:
                logger.error(f"Error fetching external klines for {symbol} on {interval}: {e}")
        return pd.DataFrame()
