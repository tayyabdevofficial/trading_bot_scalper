import hmac
import hashlib
import time
import asyncio
import collections
import logging
import aiohttp
import urllib.parse
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from config import Config

logger = logging.getLogger("ExecutionHandler")

# Setup dedicated logger for Binance API calls (ERROR only to keep log files small)
binance_logger = logging.getLogger("BinanceAPI")
binance_logger.setLevel(logging.ERROR)
binance_logger.propagate = False
if not binance_logger.handlers:
    api_handler = logging.FileHandler("binance_api.log", encoding="utf-8")
    api_handler.setLevel(logging.ERROR)
    api_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    binance_logger.addHandler(api_handler)

# Setup dedicated logger for Binance Orders (ERROR only)
binance_orders_logger = logging.getLogger("BinanceOrders")
binance_orders_logger.setLevel(logging.ERROR)
binance_orders_logger.propagate = False
if not binance_orders_logger.handlers:
    order_handler = logging.FileHandler("binance_orders.log", encoding="utf-8")
    order_handler.setLevel(logging.ERROR)
    order_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    binance_orders_logger.addHandler(order_handler)


class ExecutionHandler:
    # --- Class-level Binance rate limiter (shared across ALL instances / bots) ---
    # Binance Futures limit: 20 order requests per 10 seconds per API key.
    _rate_lock: asyncio.Lock = None          # initialised lazily (event loop must exist)
    _rate_timestamps: collections.deque = collections.deque(maxlen=20)
    _RATE_LIMIT_CALLS: int = 18
    _RATE_LIMIT_WINDOW: float = 10.0        # seconds

    def __init__(self, db=None, bot_id=None, simulation_mode=None, use_testnet=None, network=None):
        self.db = db
        self.bot_id = bot_id
        
        # Determine network explicitly ('mainnet' or 'testnet')
        if network is not None:
            self.network = str(network).lower()
        elif use_testnet or simulation_mode:
            self.network = "testnet"
        else:
            self.network = "mainnet"

        # TESTNET IS 100% LOCAL DATABASE PAPER TRADING (SIMULATION)
        # All testnet orders execute locally in SQLite database.
        # Only mainnet orders execute on the Binance exchange.
        if self.network == "testnet":
            self.simulation_mode = True
            self.use_testnet = False
        else:
            self.simulation_mode = False if simulation_mode is None else simulation_mode
            self.use_testnet = False

        # Endpoint is fapi.binance.com (for public exchange info and live mainnet orders)
        self.base_url = "https://fapi.binance.com"
            
        self.api_key = Config.BINANCE_API_KEY
        self.api_secret = Config.BINANCE_API_SECRET
        
        # Simulated Account State
        self.virtual_balance = 500.0  # Starting simulation balance $500
        self.active_position = []  # List of dicts: [{pos_id, order_id, side, entry_price, qty, ...}]

        # Initialize virtual balance from database if exists
        if self.db:
            saved_balance = self.db.get_state("virtual_balance")
            if saved_balance is not None:
                self.virtual_balance = float(saved_balance)
            else:
                self.db.set_state("virtual_balance", self.virtual_balance)
                
            # Retrieve specific active position per bot
            pos_key = f"active_position_bot_{self.bot_id}" if self.bot_id else "active_position"
            saved_pos = self.db.get_state(pos_key)
            if saved_pos:
                if isinstance(saved_pos, list):
                    self.active_position = saved_pos
                elif isinstance(saved_pos, dict):
                    self.active_position = [saved_pos]
                else:
                    self.active_position = []
            else:
                self.active_position = []

    @staticmethod
    def _decimals_from_step(step_str: str) -> int:
        """Derive number of decimal places from a tick/step size string e.g. '0.10' → 1, '0.001' → 3."""
        step_str = step_str.rstrip("0")          # "0.10000000" → "0.1"
        if "." in step_str:
            return len(step_str.split(".")[-1])  # "0.1" → 1, "0.01" → 2
        return 0                                  # "1" → 0

    async def get_symbol_precisions(self, symbol: str):
        symbol = symbol.upper()
        if not hasattr(self, "_precisions_cache"):
            self._precisions_cache = {}
        if symbol in self._precisions_cache:
            return self._precisions_cache[symbol]

        try:
            url = f"{self.base_url}/fapi/v1/exchangeInfo"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for s in data.get("symbols", []):
                            sym = s["symbol"].upper()
                            price_prec = int(s["pricePrecision"])   # fallback
                            qty_prec   = int(s["quantityPrecision"]) # fallback
                            for f in s.get("filters", []):
                                if f["filterType"] == "PRICE_FILTER" and f.get("tickSize"):
                                    price_prec = self._decimals_from_step(f["tickSize"])
                                elif f["filterType"] == "LOT_SIZE" and f.get("stepSize"):
                                    qty_prec = self._decimals_from_step(f["stepSize"])
                            self._precisions_cache[sym] = (price_prec, qty_prec)
            if symbol in self._precisions_cache:
                return self._precisions_cache[symbol]
        except Exception as e:
            logger.error(f"Error fetching exchange info for precision: {e}")

        # Fallbacks based on actual Binance tick sizes
        if symbol == "BTCUSDT":
            return 1, 3
        elif symbol == "ETHUSDT":
            return 2, 3
        elif symbol == "BNBUSDT":
            return 3, 2
        elif symbol == "SOLUSDT":
            return 3, 2
        elif symbol == "XRPUSDT":
            return 4, 1
        elif symbol == "ADAUSDT":
            return 5, 1
        elif symbol == "DOGEUSDT":
            return 5, 0
        return 2, 4

    def _signature(self, query_string):
        return hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    async def _send_request(self, method, endpoint, params=None, action=None):
        """Send a signed REST request to Binance Futures API with rate limiting and dedicated logging."""
        if not self.api_key or not self.api_secret:
            raise ValueError("Binance API keys not configured.")

        # Classify action name for logging
        if not action:
            if "/order" in endpoint:
                action = "NEW_ORDER" if method.upper() == "POST" else ("CANCEL_ORDER" if method.upper() == "DELETE" else "QUERY_ORDER")
            elif "/leverage" in endpoint:
                action = "SET_LEVERAGE"
            elif "/marginType" in endpoint:
                action = "SET_MARGIN_TYPE"
            elif "/balance" in endpoint or "/account" in endpoint:
                action = "QUERY_ACCOUNT"
            else:
                action = f"{method.upper()}_{endpoint.split('/')[-1]}"

        # --- Rate limiting (20 order calls per 10 s, shared across all bots) ---
        if method.upper() in ("POST", "DELETE"):
            if ExecutionHandler._rate_lock is None:
                ExecutionHandler._rate_lock = asyncio.Lock()
            async with ExecutionHandler._rate_lock:
                now = time.monotonic()
                while ExecutionHandler._rate_timestamps and \
                        now - ExecutionHandler._rate_timestamps[0] >= ExecutionHandler._RATE_LIMIT_WINDOW:
                    ExecutionHandler._rate_timestamps.popleft()

                if len(ExecutionHandler._rate_timestamps) >= ExecutionHandler._RATE_LIMIT_CALLS:
                    wait_for = ExecutionHandler._RATE_LIMIT_WINDOW - (now - ExecutionHandler._rate_timestamps[0])
                    if wait_for > 0:
                        logger.info(f"[RateLimit] {ExecutionHandler._RATE_LIMIT_CALLS} calls in {ExecutionHandler._RATE_LIMIT_WINDOW}s window. Waiting {wait_for:.2f}s...")
                        await asyncio.sleep(wait_for)
                        now = time.monotonic()
                        while ExecutionHandler._rate_timestamps and \
                                now - ExecutionHandler._rate_timestamps[0] >= ExecutionHandler._RATE_LIMIT_WINDOW:
                            ExecutionHandler._rate_timestamps.popleft()

                ExecutionHandler._rate_timestamps.append(time.monotonic())

        params = params or {}
        params["timestamp"] = int(time.time() * 1000)

        query_string = urllib.parse.urlencode(params)
        signature = self._signature(query_string)
        query_string += f"&signature={signature}"

        url = f"{self.base_url}{endpoint}?{query_string}"
        headers = {"X-MBX-APIKEY": self.api_key}

        sym = params.get("symbol")

        async with aiohttp.ClientSession() as session:
            async with getattr(session, method.lower())(url, headers=headers) as resp:
                data = await resp.json()
                
                is_err = (resp.status != 200 and data.get("code") != -4046)
                err_msg = data.get("msg") if is_err else None
                
                # Only log errors to file handlers to prevent log bloat
                if is_err:
                    binance_logger.error(f"REST ERROR: {method} {endpoint} HTTP {resp.status} | Data: {data}")
                    binance_orders_logger.error(
                        f"[{self.network.upper()}] [Bot {self.bot_id}] ERROR HTTP {resp.status} ({action}) | Data: {data}"
                    )

                # Persist to database binance_api_logs table for dedicated UI page
                if self.db:
                    try:
                        self.db.log_binance_api(
                            network=self.network,
                            bot_id=self.bot_id,
                            symbol=sym,
                            action=action,
                            method=method,
                            endpoint=endpoint,
                            request_payload=params,
                            response_status=resp.status,
                            response_payload=data,
                            is_error=is_err,
                            error_message=err_msg
                        )
                    except Exception as db_log_err:
                        logger.error(f"Error logging to binance_api_logs: {db_log_err}")

                if resp.status != 200:
                    if data.get("code") == -4046:
                        return data
                    logger.error(f"Binance API Error: {data}")
                    raise Exception(f"Binance API Error: {data.get('msg', 'Unknown error')}")
                return data

    async def test_binance_api(self) -> bool:
        """Test if Binance Futures API keys are working. Not needed for testnet paper trading."""
        if self.network == "testnet" or self.simulation_mode:
            return True
        if not self.api_key or not self.api_secret:
            return False
        try:
            await self._send_request("GET", "/fapi/v2/balance", action="TEST_CONNECTION")
            return True
        except Exception as e:
            logger.error(f"Binance API check failed: {e}")
            return False

    async def set_leverage(self, symbol, leverage):
        if self.simulation_mode:
            logger.info(f"[SIMULATION] Set leverage for {symbol} to {leverage}x")
            return

        state_key = f"leverage_{self.network}_{symbol.upper()}"
        if self.db:
            cached_lev = self.db.get_state(state_key)
            if cached_lev == int(leverage):
                logger.debug(f"Leverage for {symbol} is already set to {leverage}x. Skipping API request.")
                return
            
        try:
            endpoint = "/fapi/v1/leverage"
            params = {"symbol": symbol.upper(), "leverage": int(leverage)}
            res = await self._send_request("POST", endpoint, params, action="SET_LEVERAGE")
            logger.info(f"Set leverage success: {res}")
            if self.db:
                self.db.set_state(state_key, int(leverage))
        except Exception as e:
            logger.error(f"Error setting leverage: {e}")

    async def set_margin_type(self, symbol: str, margin_type: str = None):
        """Set margin type on Binance Futures (CROSSED or ISOLATED). Defaults to Config.MARGIN_TYPE (CROSSED)."""
        target_margin = (margin_type or getattr(Config, "MARGIN_TYPE", "CROSSED")).upper()
        if self.simulation_mode:
            logger.info(f"[SIMULATION] Set margin type for {symbol} to {target_margin}")
            return

        state_key = f"margin_type_{self.network}_{symbol.upper()}"
        if self.db:
            cached_margin = self.db.get_state(state_key)
            if cached_margin == target_margin:
                logger.debug(f"Margin type for {symbol} is already set to {target_margin}. Skipping API request.")
                return
            
        try:
            endpoint = "/fapi/v1/marginType"
            params = {"symbol": symbol.upper(), "marginType": target_margin}
            res = await self._send_request("POST", endpoint, params, action="SET_MARGIN_TYPE")
            logger.info(f"Set margin type for {symbol} to {target_margin}: {res}")
            if self.db:
                self.db.set_state(state_key, target_margin)
            return res
        except Exception as e:
            err_msg = str(e)
            if "No need to change margin type" in err_msg or "-4046" in err_msg:
                logger.info(f"Margin type for {symbol} is already {target_margin} on Binance. State cached.")
                if self.db:
                    self.db.set_state(state_key, target_margin)
            else:
                logger.error(f"Error setting margin type for {symbol}: {e}")

    async def get_balance(self) -> float:
        if self.simulation_mode:
            if self.db:
                saved_balance = self.db.get_state("virtual_balance")
                if saved_balance is not None:
                    self.virtual_balance = float(saved_balance)
            return self.virtual_balance
            
        try:
            endpoint = "/fapi/v2/balance"
            res = await self._send_request("GET", endpoint, action="QUERY_BALANCE")
            for asset in res:
                if asset.get("asset") == "USDT":
                    return float(asset.get("balance"))
            return 0.0
        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            return 0.0

    async def get_available_balance(self) -> float:
        wallet_balance = await self.get_balance()
        if not self.simulation_mode:
            try:
                endpoint = "/fapi/v2/account"
                res = await self._send_request("GET", endpoint, action="QUERY_ACCOUNT")
                return float(res.get("availableBalance", wallet_balance))
            except Exception as e:
                logger.error(f"Error fetching real available balance: {e}")
                return wallet_balance
                
        if not self.db:
            return wallet_balance
            
        total_margin = 0.0
        bots = self.db.get_bots()
        for b in bots:
            pos_val = self.db.get_state(f"active_position_bot_{b['id']}")
            if pos_val:
                positions = pos_val if isinstance(pos_val, list) else [pos_val]
                leverage = int(b["parameters"].get("leverage", 20))
                for pos in positions:
                    position_value = pos["entry_price"] * pos["qty"]
                    margin = position_value / leverage
                    total_margin += margin
                
        available = wallet_balance - total_margin
        return max(0.0, available)

    def calculate_tp_targets(self, side, entry_price, qty, take_profit_pct, price_precision=2, qty_precision=4):
        """Divide Take Profit targets into multiple levels if TP > 1%."""
        num_tps = int(take_profit_pct)
        if num_tps <= 1:
            tp_price = entry_price * (1 + take_profit_pct/100) if side == "BUY" else entry_price * (1 - take_profit_pct/100)
            return [{
                "target_price": round(tp_price, price_precision),
                "qty": round(qty, qty_precision),
                "hit": False,
                "pct": 100,
                "order_id": None
            }]
            
        targets = []
        step = take_profit_pct / num_tps
        remaining_qty = qty
        
        for i in range(1, num_tps + 1):
            tp_pct = step * i
            tp_price = entry_price * (1 + tp_pct/100) if side == "BUY" else entry_price * (1 - tp_pct/100)
            
            if i == num_tps:
                tp_qty = remaining_qty
            else:
                tp_qty = round(qty / num_tps, qty_precision)
                remaining_qty -= tp_qty
                
            pct_closed = round((i / num_tps) * 100)
            
            targets.append({
                "target_price": round(tp_price, price_precision),
                "qty": round(tp_qty, qty_precision),
                "hit": False,
                "pct": pct_closed,
                "order_id": None
            })
        return targets

    async def execute_order(self, symbol, side, qty, price, sl_price=0.0, tp_price=0.0, hedge_mode=False):
        """
        Execute a market entry order on Binance (or simulation).
        Supports opening new trades in the same direction (pyramiding/DCA).
        Inserts a persistent row in the dedicated 'orders' table with created_at and updated_at.
        """
        if not isinstance(self.active_position, list):
            self.active_position = [self.active_position] if self.active_position else []

        existing_pos = None
        for p in self.active_position:
            if p["side"].upper() == side.upper():
                existing_pos = p
                break

        is_dca = (existing_pos is not None)

        # Precisions
        price_precision, qty_precision = await self.get_symbol_precisions(symbol)

        # Calculate TP targets
        bot_tp_pct = Config.TAKE_PROFIT_PCT
        if self.db and self.bot_id:
            bot = self.db.get_bot(self.bot_id)
            if bot:
                bot_tp_pct = float(bot["parameters"].get("take_profit_pct", Config.TAKE_PROFIT_PCT))

        if qty_precision == 0:
            qty = int(round(float(qty)))
        else:
            qty = round(float(qty), qty_precision)

        price = round(float(price), price_precision)
        if sl_price > 0:
            sl_price = round(float(sl_price), price_precision)
        if tp_price > 0:
            tp_price = round(float(tp_price), price_precision)

        pos_id = f"pos_{int(time.time() * 1000)}_{self.bot_id}_{uuid.uuid4().hex[:6]}"
        sim_order_id = f"ORD_SIM_{self.network.upper()}_{self.bot_id}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"

        new_pos = {
            "pos_id": pos_id,
            "order_id": sim_order_id,
            "order_ids": [sim_order_id],
            "symbol": symbol.upper(),
            "side": side.upper(),
            "entry_price": float(price),
            "original_qty": float(qty),
            "qty": float(qty),
            "sl_price": float(sl_price),
            "tp_price": float(tp_price),
            "tp_targets": [],
            "released_pnl": 0.0,
            "entries_count": 1,
            "timestamp": datetime.utcnow().isoformat()
        }

        # ── SIMULATION EXECUTION ──
        if self.simulation_mode:
            if is_dca:
                old_qty = existing_pos["qty"]
                old_entry_price = existing_pos["entry_price"]
                new_qty = float(qty)
                new_price = float(price)
                
                total_qty = old_qty + new_qty
                avg_entry_price = (old_entry_price * old_qty + new_price * new_qty) / total_qty
                
                sl_pct = abs(new_price - sl_price) / new_price if new_price > 0 and sl_price > 0 else 0.0
                if side.upper() == "BUY":
                    new_sl = avg_entry_price * (1 - sl_pct) if sl_pct > 0 else 0.0
                else:
                    new_sl = avg_entry_price * (1 + sl_pct) if sl_pct > 0 else 0.0
                    
                new_tp = avg_entry_price * (1 + bot_tp_pct/100) if side.upper() == "BUY" else avg_entry_price * (1 - bot_tp_pct/100)
                existing_pos["entries_count"] = existing_pos.get("entries_count", 1) + 1
                existing_pos["original_qty"] = round(total_qty, qty_precision)
                existing_pos["qty"] = round(total_qty, qty_precision)
                existing_pos["entry_price"] = round(avg_entry_price, price_precision)
                existing_pos["tp_price"] = round(new_tp, price_precision)
                existing_pos["sl_price"] = round(new_sl, price_precision) if new_sl > 0 else 0.0
                existing_pos["tp_targets"] = self.calculate_tp_targets(side, avg_entry_price, total_qty, bot_tp_pct, price_precision, qty_precision)
                existing_pos["timestamp"] = datetime.utcnow().isoformat()
                existing_pos.setdefault("order_ids", []).append(sim_order_id)
                existing_pos["order_id"] = sim_order_id
            else:
                initial_tp = price * (1 + bot_tp_pct/100) if side.upper() == "BUY" else price * (1 - bot_tp_pct/100)
                new_pos["tp_price"] = round(initial_tp, price_precision)
                new_pos["tp_targets"] = self.calculate_tp_targets(side, float(price), float(qty), bot_tp_pct, price_precision, qty_precision)
                self.active_position.append(new_pos)

            # Insert / update order in the dedicated orders table
            if self.db:
                pos_key = f"active_position_bot_{self.bot_id}" if self.bot_id else "active_position"
                self.db.set_state(pos_key, self.active_position)
                
                if is_dca:
                    primary_ord_id = existing_pos.get("order_ids", [sim_order_id])[0]
                    self.db.update_order_dca(
                        order_id=primary_ord_id,
                        entry_price=existing_pos["entry_price"],
                        qty=existing_pos["qty"],
                        tp_price=existing_pos["tp_price"],
                        tp_targets=existing_pos["tp_targets"],
                        entries_count=existing_pos["entries_count"]
                    )
                    # Also record child DCA order marked as CONSOLIDATED_DCA for history
                    self.db.create_order(
                        order_id=sim_order_id,
                        bot_id=self.bot_id,
                        network=self.network,
                        symbol=symbol,
                        side=side,
                        order_type="MARKET_DCA",
                        entry_price=price,
                        qty=qty,
                        sl_price=sl_price,
                        tp_price=tp_price,
                        tp_targets=existing_pos.get("tp_targets"),
                        position_side="LONG" if side.upper() == "BUY" else "SHORT",
                        status="CONSOLIDATED_DCA",
                        entries_count=1
                    )
                else:
                    self.db.create_order(
                        order_id=sim_order_id,
                        bot_id=self.bot_id,
                        network=self.network,
                        symbol=symbol,
                        side=side,
                        order_type="MARKET_ENTRY",
                        entry_price=price,
                        qty=qty,
                        sl_price=sl_price,
                        tp_price=tp_price,
                        tp_targets=new_pos.get("tp_targets"),
                        position_side="LONG" if side.upper() == "BUY" else "SHORT",
                        status="OPEN",
                        entries_count=1
                    )
                self.db.log_trade(
                    symbol=symbol,
                    side=side,
                    order_type="MARKET_DCA" if is_dca else "MARKET_ENTRY",
                    price=price,
                    qty=qty,
                    realized_pnl=0.0,
                    order_id=sim_order_id,
                    bot_id=self.bot_id
                )
                self.db.log_message("INFO", f"[SIMULATION] Same-direction trade executed for {qty} {symbol} at {price} (DCA/Pyramid, {existing_pos['entries_count']}x)." if is_dca else f"[SIMULATION] Opened {side} position of {qty} {symbol} at {price}", bot_id=self.bot_id)

            logger.info(f"[SIMULATION] Bot {self.bot_id} Executed {side} order of {qty} {symbol} at {price} (DCA={is_dca}, count={existing_pos['entries_count'] if is_dca else 1})")
            return

        # ── LIVE / REAL TESTNET BINANCE API EXECUTION ──
        try:
            is_hedge = False
            if not hasattr(self, "is_hedge"):
                try:
                    dual_res = await self._send_request("GET", "/fapi/v1/positionSide/dual", action="CHECK_DUAL_SIDE")
                    self.is_hedge = dual_res.get("dualSidePosition", False)
                except Exception:
                    self.is_hedge = False
            is_hedge = self.is_hedge

            # Set cross margin mode (CROSSED)
            try:
                target_margin = getattr(Config, "MARGIN_TYPE", "CROSSED").upper()
                endpoint_margin = "/fapi/v1/marginType"
                params_margin = {"symbol": symbol.upper(), "marginType": target_margin}
                await self._send_request("POST", endpoint_margin, params_margin, action="SET_MARGIN_TYPE")
            except Exception:
                pass
                
            endpoint = "/fapi/v1/order"
            params = {
                "symbol": symbol.upper(),
                "side": side.upper(),
                "type": "MARKET",
                "quantity": float(qty)
            }
            if is_hedge:
                params["positionSide"] = "LONG" if side.upper() == "BUY" else "SHORT"
                
            res = await self._send_request("POST", endpoint, params, action="NEW_MARKET_ORDER")
            order_id = str(res.get("orderId", f"ORD_{self.bot_id}_{int(time.time()*1000)}"))
            entry_price = float(res.get("avgPrice", price)) or price
        except Exception as e:
            logger.error(f"Failed to execute real order for bot {self.bot_id}: {e}")
            if self.db:
                self.db.log_message("ERROR", f"Order execution failed on Binance: {str(e)}", bot_id=self.bot_id)
            return

        # ── POST-ENTRY HANDLING ON BINANCE ──
        try:
            if is_dca:
                old_qty = existing_pos["qty"]
                old_entry_price = existing_pos["entry_price"]
                new_qty = float(qty)
                new_price = entry_price
                
                total_qty = old_qty + new_qty
                avg_entry_price = (old_entry_price * old_qty + new_price * new_qty) / total_qty
                
                sl_pct = abs(new_price - sl_price) / new_price if new_price > 0 and sl_price > 0 else 0.0
                if side.upper() == "BUY":
                    new_sl = avg_entry_price * (1 - sl_pct) if sl_pct > 0 else 0.0
                else:
                    new_sl = avg_entry_price * (1 + sl_pct) if sl_pct > 0 else 0.0
                    
                new_sl = round(new_sl, price_precision)
                new_tp = avg_entry_price * (1 + bot_tp_pct/100) if side.upper() == "BUY" else avg_entry_price * (1 - bot_tp_pct/100)
                
                # 1. Cancel previous TP orders ONLY for this direction before updating to new targets
                await self.cancel_direction_orders(symbol, side, existing_pos)

                existing_pos["entries_count"] = existing_pos.get("entries_count", 1) + 1
                existing_pos["original_qty"] = round(total_qty, qty_precision)
                existing_pos["qty"] = round(total_qty, qty_precision)
                existing_pos["entry_price"] = round(avg_entry_price, price_precision)
                existing_pos["tp_price"] = round(new_tp, price_precision)
                existing_pos["sl_price"] = round(new_sl, price_precision) if new_sl > 0 else 0.0
                existing_pos["timestamp"] = datetime.utcnow().isoformat()
                existing_pos.setdefault("order_ids", []).append(order_id)
                existing_pos["order_id"] = order_id
                existing_pos["status"] = "ACTIVE"
                existing_pos["tp_targets"] = self.calculate_tp_targets(side, avg_entry_price, total_qty, bot_tp_pct, price_precision, qty_precision)
            else:
                initial_tp = entry_price * (1 + bot_tp_pct/100) if side.upper() == "BUY" else entry_price * (1 - bot_tp_pct/100)
                new_pos["entry_price"] = entry_price
                new_pos["tp_price"] = round(initial_tp, price_precision)
                new_pos["order_id"] = order_id
                new_pos["order_ids"] = [order_id]
                new_pos["tp_targets"] = self.calculate_tp_targets(side, entry_price, float(qty), bot_tp_pct, price_precision, qty_precision)
                new_pos["status"] = "ACTIVE"
                self.active_position.append(new_pos)

            # Record in orders table
            if self.db:
                pos_key = f"active_position_bot_{self.bot_id}" if self.bot_id else "active_position"
                self.db.set_state(pos_key, self.active_position)
                
                if is_dca:
                    primary_ord_id = existing_pos.get("order_ids", [order_id])[0]
                    self.db.update_order_dca(
                        order_id=primary_ord_id,
                        entry_price=existing_pos["entry_price"],
                        qty=existing_pos["qty"],
                        tp_price=existing_pos["tp_price"],
                        tp_targets=existing_pos["tp_targets"],
                        entries_count=existing_pos["entries_count"]
                    )
                    self.db.create_order(
                        order_id=order_id,
                        bot_id=self.bot_id,
                        network=self.network,
                        symbol=symbol,
                        side=side,
                        order_type="MARKET_DCA",
                        entry_price=entry_price,
                        qty=qty,
                        sl_price=sl_price,
                        tp_price=tp_price,
                        tp_targets=existing_pos.get("tp_targets"),
                        position_side="LONG" if side.upper() == "BUY" else "SHORT",
                        status="CONSOLIDATED_DCA",
                        entries_count=1
                    )
                else:
                    self.db.create_order(
                        order_id=order_id,
                        bot_id=self.bot_id,
                        network=self.network,
                        symbol=symbol,
                        side=side,
                        order_type="MARKET_ENTRY",
                        entry_price=entry_price,
                        qty=qty,
                        sl_price=sl_price,
                        tp_price=tp_price,
                        tp_targets=new_pos.get("tp_targets"),
                        position_side="LONG" if side.upper() == "BUY" else "SHORT",
                        status="OPEN",
                        entries_count=1
                    )
                self.db.log_trade(
                    symbol=symbol,
                    side=side,
                    order_type="MARKET_DCA" if is_dca else "MARKET_ENTRY",
                    price=entry_price,
                    qty=qty,
                    realized_pnl=0.0,
                    order_id=f"POS_{order_id}_ENTRY",
                    bot_id=self.bot_id
                )
                self.db.log_message("INFO", f"[{self.network.upper()}] Executed {side} order at {entry_price} (Same-direction add/DCA, {existing_pos['entries_count']}x)" if is_dca else f"[{self.network.upper()}] Opened {side} position on Binance Futures at {entry_price}", bot_id=self.bot_id)

            logger.info(f"[{self.network.upper()}] Bot {self.bot_id} Executed {side} order of {qty} {symbol} at {entry_price} (DCA={is_dca})")

            # Place TP Limit orders on Binance
            target_list = existing_pos["tp_targets"] if is_dca else new_pos["tp_targets"]
            for target in target_list:
                try:
                    tp_params = {
                        "symbol": symbol.upper(),
                        "side": "SELL" if side.upper() == "BUY" else "BUY",
                        "type": "LIMIT",
                        "price": float(target["target_price"]),
                        "quantity": float(target["qty"]),
                        "timeInForce": "GTC"
                    }
                    if is_hedge:
                        tp_params["positionSide"] = "LONG" if side.upper() == "BUY" else "SHORT"
                    else:
                        tp_params["reduceOnly"] = "true"
                    tp_res = await self._send_request("POST", "/fapi/v1/order", tp_params, action="PLACE_TP_LIMIT")
                    target["order_id"] = str(tp_res.get("orderId"))
                except Exception as tp_err:
                    logger.error(f"Failed to place TP target order: {tp_err}")

        except Exception as post_err:
            logger.error(f"Failed in post-entry execution handler: {post_err}")

    async def check_and_exit_positions(self, current_price: float):
        """Check active positions against SL and progressive TP targets, updating exact order rows in database."""
        if not self.active_position:
            return

        if not isinstance(self.active_position, list):
            self.active_position = [self.active_position]

        for pos in list(self.active_position):
            side = pos["side"]
            entry_price = pos["entry_price"]
            sl = float(pos.get("sl_price") or 0.0)
            tp = float(pos.get("tp_price") or 0.0)
            qty = float(pos.get("qty") or 0.0)
            entries_count = int(pos.get("entries_count") or 1)
            dca_count = max(0, entries_count - 1)
            
            # 0. Check Max Loss Stop-Loss (Max Loss = $150 if DCA count > 5, otherwise $100)
            max_loss_threshold = 150.0 if (dca_count > 5 or entries_count > 6) else 100.0
            unrealized_pnl = (current_price - entry_price) * qty if side == "BUY" else (entry_price - current_price) * qty
            if unrealized_pnl <= -max_loss_threshold:
                logger.warning(
                    f"Bot {self.bot_id} [{pos.get('symbol')}] MAX LOSS STOP TRIGGERED: "
                    f"Position ({entries_count}x entries / {dca_count}x DCA) Loss is ${abs(unrealized_pnl):.2f} (<= -${max_loss_threshold:.2f}). Auto-closing position on Binance."
                )
                if self.db:
                    self.db.log_message(
                        "WARNING",
                        f"[{pos.get('symbol')}] Max Loss Stop triggered ({entries_count}x entries / {dca_count}x DCA, Loss: ${unrealized_pnl:.2f}, limit: ${max_loss_threshold:.0f}). Auto-closing position.",
                        bot_id=self.bot_id
                    )
                await self.close_position(current_price, "MAX_LOSS_EXIT", unrealized_pnl, side, pos_to_close=pos)
                continue

            # 1. Check Take Profit first (Matches BacktestEngine)
            trigger_tp = False
            if side == "BUY" and tp > 0 and current_price >= tp:
                trigger_tp = True
            elif side == "SELL" and tp > 0 and current_price <= tp:
                trigger_tp = True

            if trigger_tp:
                exit_price = tp
                pnl = (exit_price - entry_price) * pos["qty"] if side == "BUY" else (entry_price - exit_price) * pos["qty"]
                await self.close_position(exit_price, "TAKE_PROFIT", pnl, side, pos_to_close=pos)
                continue
            
            # 2. Check Stop Loss ONLY if explicitly enabled (sl > 0)
            trigger_sl = False
            if sl > 0:
                if side == "BUY" and current_price <= sl:
                    trigger_sl = True
                elif side == "SELL" and current_price >= sl:
                    trigger_sl = True
                
            if trigger_sl:
                pnl = (current_price - entry_price) * pos["qty"] if side == "BUY" else (entry_price - current_price) * pos["qty"]
                await self.close_position(current_price, "STOP_LOSS", pnl, side, pos_to_close=pos)
                continue

            # Check progressive TP Targets
            tp_targets = pos.get("tp_targets", [])
            for target in tp_targets:
                if target.get("hit"):
                    continue
                
                trigger_tp = False
                if side == "BUY" and current_price >= target["target_price"]:
                    trigger_tp = True
                elif side == "SELL" and current_price <= target["target_price"]:
                    trigger_tp = True
                    
                if trigger_tp:
                    target["hit"] = True
                    pnl = (target["target_price"] - entry_price) * target["qty"] if side == "BUY" else (entry_price - target["target_price"]) * target["qty"]
                    
                    pos["qty"] = max(0.0, round(pos["qty"] - target["qty"], 4))
                    pos["released_pnl"] = round(pos.get("released_pnl", 0.0) + pnl, 2)
                    
                    # Update Stop Loss based on TPs hit
                    hit_count = sum(1 for t in tp_targets if t.get("hit"))
                    if hit_count == 2:
                        pos["sl_price"] = entry_price
                        logger.info(f"Bot {self.bot_id} - 2 TPs hit, moved SL to entry price: {entry_price}")
                        if self.db:
                            self.db.log_message("INFO", f"2 TPs hit, moved Stop Loss to entry price: {entry_price}", bot_id=self.bot_id)
                    elif hit_count >= 3:
                        new_sl = tp_targets[hit_count - 3]["target_price"]
                        pos["sl_price"] = new_sl
                        logger.info(f"Bot {self.bot_id} - {hit_count} TPs hit, moved SL to TP{hit_count-2} price: {new_sl}")
                        if self.db:
                            self.db.log_message("INFO", f"{hit_count} TPs hit, moved Stop Loss to TP{hit_count-2} price: {new_sl}", bot_id=self.bot_id)
                    
                    # In-place update for exact order row in the database
                    ord_id = pos.get("order_id")
                    if self.db and ord_id:
                        primary_ord_id = pos.get("order_ids", [ord_id])[0] if pos.get("order_ids") else ord_id
                        entries_count = pos.get("entries_count", 1)
                        self.db.update_order_exit(
                            order_id=primary_ord_id,
                            close_price=target["target_price"],
                            realized_pnl=pnl,
                            status="TP_HIT",
                            exit_reason=f"TP_{target.get('pct', 100)}%",
                            entries_count=entries_count
                        )
                        for add_oid in pos.get("order_ids", []):
                            if add_oid != primary_ord_id:
                                self.db.update_order_exit(
                                    order_id=add_oid,
                                    close_price=target["target_price"],
                                    realized_pnl=0.0,
                                    status="CONSOLIDATED_DCA",
                                    exit_reason="CONSOLIDATED_DCA",
                                    entries_count=1
                                )
                        pos_key = f"active_position_bot_{self.bot_id}" if self.bot_id else "active_position"
                        self.db.set_state(pos_key, self.active_position)
                        self.db.log_trade(
                            symbol=pos["symbol"],
                            side="SELL" if side == "BUY" else "BUY",
                            order_type=f"PARTIAL_TP_{target['pct']}%",
                            price=target["target_price"],
                            qty=target["qty"],
                            realized_pnl=pnl,
                            order_id=f"ORD_{ord_id}_TP_{target['pct']}",
                            bot_id=self.bot_id
                        )
                        
                        if self.simulation_mode:
                            current_balance = float(self.db.get_state("virtual_balance", 500.0))
                            self.virtual_balance = current_balance + pnl
                            self.db.set_state("virtual_balance", self.virtual_balance)
                            self.db.log_pnl(self.virtual_balance, pnl)
                            
                        self.db.log_message("INFO", f"Take Profit Target {target['pct']}% Hit at {target['target_price']}. PnL: {pnl:.2f}", bot_id=self.bot_id)

                    # If this was the final TP target, completely remove the position
                    if pos["qty"] <= 0.0001 or target["pct"] == 100:
                        if pos in self.active_position:
                            self.active_position.remove(pos)
                        if self.db:
                            pos_key = f"active_position_bot_{self.bot_id}" if self.bot_id else "active_position"
                            self.db.set_state(pos_key, self.active_position)

    async def get_all_exchange_positions(self, symbol: str) -> Dict[str, float]:
        """
        Query Binance exchange for actual open positions for BOTH Long and Short in ONE SINGLE API call.
        Returns a dict: {'LONG': qty, 'SHORT': qty}.
        """
        if self.simulation_mode:
            return {"LONG": 0.0, "SHORT": 0.0}
        try:
            data = await self._send_request("GET", "/fapi/v2/positionRisk", {"symbol": symbol.upper()}, action="QUERY_POSITION_RISK")
            res = {"LONG": 0.0, "SHORT": 0.0}
            if isinstance(data, list):
                for pos in data:
                    amt = float(pos.get("positionAmt", 0.0))
                    p_side = pos.get("positionSide", "BOTH").upper()
                    if p_side == "LONG":
                        res["LONG"] = abs(amt)
                    elif p_side == "SHORT":
                        res["SHORT"] = abs(amt)
                    elif p_side == "BOTH":
                        if amt > 0:
                            res["LONG"] = abs(amt)
                        elif amt < 0:
                            res["SHORT"] = abs(amt)
            return res
        except Exception as e:
            logger.error(f"Error querying position risk for {symbol}: {e}")
            return {"LONG": None, "SHORT": None}

    async def get_exchange_position_qty(self, symbol: str, side: str = "BUY"):
        """Query Binance exchange for actual open position quantity for a symbol and side."""
        if self.simulation_mode:
            return 0.0
        positions = await self.get_all_exchange_positions(symbol)
        pos_side = "LONG" if side.upper() in ("BUY", "LONG") else "SHORT"
        return positions.get(pos_side)

    async def cancel_direction_orders(self, symbol: str, pos_side: str, target_pos: dict = None):
        """
        Cancel only open orders (TP / SL limit orders) belonging to a specific position/direction.
        Never cancels orders belonging to the opposite direction in hedge mode.
        """
        if self.simulation_mode:
            return

        # 1. Cancel specific tracked order IDs from position state
        if target_pos:
            for t in target_pos.get("tp_targets", []):
                oid = t.get("order_id")
                if oid:
                    try:
                        await self._send_request("DELETE", "/fapi/v1/order", {"symbol": symbol.upper(), "orderId": int(oid)}, action="CANCEL_TP_LIMIT")
                    except Exception:
                        pass

        # 2. Query open orders and cancel ONLY orders belonging to this positionSide / direction
        try:
            open_orders = await self._send_request("GET", "/fapi/v1/openOrders", {"symbol": symbol.upper()}, action="QUERY_OPEN_ORDERS")
            if isinstance(open_orders, list):
                target_p_side = "LONG" if pos_side.upper() in ("BUY", "LONG") else "SHORT"
                target_order_side = "SELL" if pos_side.upper() in ("BUY", "LONG") else "BUY" # Closing order side
                
                for o in open_orders:
                    o_id = o.get("orderId")
                    o_p_side = o.get("positionSide", "BOTH").upper()
                    o_side = o.get("side", "").upper()
                    
                    # In Hedge mode, match positionSide. In One-Way mode, match the closing side.
                    is_match = False
                    if o_p_side == target_p_side:
                        is_match = True
                    elif o_p_side == "BOTH" and o_side == target_order_side:
                        is_match = True
                        
                    if is_match and o_id:
                        try:
                            await self._send_request("DELETE", "/fapi/v1/order", {"symbol": symbol.upper(), "orderId": int(o_id)}, action=f"CANCEL_{target_p_side}_LIMIT")
                        except Exception:
                            pass
        except Exception as e:
            logger.warning(f"Error while safely canceling direction orders for {symbol} {pos_side}: {e}")

    async def close_position(self, current_price: float, reason: str, pnl: float, side: str = None, pos_to_close = None, pos_id: str = None):
        """
        Close active position completely on Binance and in DB.
        Updates that exact row in the 'orders' table with updated_at timestamp.
        """
        if not self.active_position:
            return

        if not isinstance(self.active_position, list):
            self.active_position = [self.active_position]

        target_pos = pos_to_close
        if not target_pos and pos_id:
            for p in self.active_position:
                if p.get("pos_id") == pos_id:
                    target_pos = p
                    break

        if not target_pos:
            if side:
                for p in self.active_position:
                    if p["side"].upper() == side.upper():
                        target_pos = p
                        break
            else:
                if len(self.active_position) > 0:
                    target_pos = self.active_position[0]

        if not target_pos:
            return

        symbol = target_pos["symbol"]
        pos_side = target_pos["side"]
        qty = target_pos["qty"]
        target_ord_id = target_pos.get("order_id") or f"ORD_{int(time.time() * 1000)}_{self.bot_id}"
        primary_ord_id = target_pos.get("order_ids", [target_ord_id])[0] if target_pos.get("order_ids") else target_ord_id
        entries_count = target_pos.get("entries_count", 1)
        close_side = "SELL" if pos_side == "BUY" else "BUY"
        exit_status = "SL_HIT" if reason == "STOP_LOSS" else ("OPPOSITE_SIGNAL_CLOSED" if "OPPOSITE" in reason.upper() else "CLOSED")

        # ── SIMULATION CLOSE ──
        if self.simulation_mode:
            if self.db:
                current_balance = float(self.db.get_state("virtual_balance", 500.0))
                self.virtual_balance = current_balance + pnl
                self.db.set_state("virtual_balance", self.virtual_balance)
                
                # In-place update of exact primary order row in 'orders' table
                self.db.update_order_exit(
                    order_id=primary_ord_id,
                    close_price=current_price,
                    realized_pnl=pnl,
                    status=exit_status,
                    exit_reason=reason,
                    entries_count=entries_count
                )
                for add_oid in target_pos.get("order_ids", []):
                    if add_oid != primary_ord_id:
                        self.db.update_order_exit(
                            order_id=add_oid,
                            close_price=current_price,
                            realized_pnl=0.0,
                            status="CONSOLIDATED_DCA",
                            exit_reason="CONSOLIDATED_DCA",
                            entries_count=1
                        )

                if target_pos in self.active_position:
                    self.active_position.remove(target_pos)
                pos_key = f"active_position_bot_{self.bot_id}" if self.bot_id else "active_position"
                self.db.set_state(pos_key, self.active_position)
                
                self.db.log_trade(
                    symbol=symbol,
                    side=close_side,
                    order_type=reason,
                    price=current_price,
                    qty=qty,
                    realized_pnl=pnl,
                    order_id=f"POS_{target_ord_id}_EXIT_{reason}_{int(time.time() * 1000)}",
                    bot_id=self.bot_id
                )
                self.db.log_pnl(self.virtual_balance, pnl)
                self.db.log_message("INFO", f"[SIMULATION] Closed {pos_side} position via {reason} at {current_price} ({entries_count}x entries). PnL: {pnl:.2f}", bot_id=self.bot_id)
                
            if target_pos in self.active_position:
                self.active_position.remove(target_pos)
            logger.info(f"[SIMULATION] Bot {self.bot_id} Closed {pos_side} position via {reason} at {current_price}. PnL: {pnl:.2f}")
            return

        # ── LIVE / REAL POSITION CLOSING ──
        try:
            close_price = current_price
            order_id = f"POS_{target_ord_id}_EXIT_{reason}_{int(time.time() * 1000)}"

            if not reason.startswith("PARTIAL_TP_") and not reason.startswith("EXCHANGE_SYNC"):
                try:
                    price_precision, qty_precision = await self.get_symbol_precisions(symbol)
                    qty_to_close = int(round(float(qty))) if qty_precision == 0 else round(float(qty), qty_precision)
                    close_params = {
                        "symbol": symbol.upper(),
                        "side": close_side,
                        "type": "MARKET",
                        "quantity": float(qty_to_close)
                    }
                    is_hedge = getattr(self, "is_hedge", False)
                    if is_hedge:
                        close_params["positionSide"] = "LONG" if pos_side == "BUY" else "SHORT"
                        
                    res = await self._send_request("POST", "/fapi/v1/order", close_params, action=f"CLOSE_POSITION_{reason}")
                    order_id = str(res.get("orderId", order_id))
                    close_price = float(res.get("avgPrice", current_price)) or current_price
                    logger.info(f"[{self.network.upper()}] Bot {self.bot_id} MARKET close sent for {pos_side} {symbol} via {reason} @ {close_price}")
                except Exception as e:
                    ex_qty = await self.get_exchange_position_qty(symbol, pos_side)
                    if ex_qty == 0.0:
                        logger.info(f"[{self.network.upper()}] Bot {self.bot_id} Position on Binance is ALREADY 0. Syncing state.")
                        order_id = f"EXCHANGE_SYNC_{int(time.time() * 1000)}_{self.bot_id}"
                    else:
                        logger.warning(f"Market close failed on Binance ({e}). Retrying close once...")
                        await asyncio.sleep(1.0)
                        try:
                            res = await self._send_request("POST", "/fapi/v1/order", close_params, action="RETRY_CLOSE_POSITION")
                            order_id = str(res.get("orderId", order_id))
                            close_price = float(res.get("avgPrice", current_price)) or current_price
                        except Exception as retry_err:
                            logger.error(f"Retry market close failed: {retry_err}")
            else:
                logger.info(f"[{self.network.upper()}] Bot {self.bot_id} TP recorded for {pos_side} {symbol} @ {current_price}")

            if target_pos in self.active_position:
                self.active_position.remove(target_pos)

            # Cancel remaining open orders ONLY for this closed position/direction
            await self.cancel_direction_orders(symbol, pos_side, target_pos)

            # In-place update of exact order row in 'orders' table
            if self.db:
                pos_key = f"active_position_bot_{self.bot_id}" if self.bot_id else "active_position"
                self.db.set_state(pos_key, self.active_position)
                
                self.db.update_order_exit(
                    order_id=primary_ord_id,
                    close_price=close_price,
                    realized_pnl=pnl,
                    status=exit_status,
                    exit_reason=reason,
                    entries_count=entries_count
                )
                for add_oid in target_pos.get("order_ids", []):
                    if add_oid != primary_ord_id:
                        self.db.update_order_exit(
                            order_id=add_oid,
                            close_price=close_price,
                            realized_pnl=0.0,
                            status="CONSOLIDATED_DCA",
                            exit_reason="CONSOLIDATED_DCA",
                            entries_count=1
                        )

                self.db.log_trade(
                    symbol=symbol,
                    side=close_side,
                    order_type=reason,
                    price=close_price,
                    qty=qty,
                    realized_pnl=pnl,
                    order_id=order_id,
                    bot_id=self.bot_id
                )
                try:
                    new_balance = await self.get_balance()
                    self.db.log_pnl(new_balance, pnl)
                except Exception:
                    pass
                self.db.log_message("INFO", f"[{self.network.upper()}] {pos_side} position closed via {reason} at {close_price} ({entries_count}x entries). PnL: {pnl:.2f}", bot_id=self.bot_id)

        except Exception as e:
            logger.error(f"Failed to close position for bot {self.bot_id}: {e}")
            if target_pos in self.active_position:
                self.active_position.remove(target_pos)
            if self.db:
                pos_key = f"active_position_bot_{self.bot_id}" if self.bot_id else "active_position"
                self.db.set_state(pos_key, self.active_position)

    async def close_all_positions(self, current_price: float, reason: str = "OPPOSITE_SIGNAL", side_to_close: str = None):
        """
        Close ALL active positions on Binance and in DB.
        Called when an opposite signal triggers or on emergency close.
        """
        if not self.active_position:
            return
        positions = list(self.active_position) if isinstance(self.active_position, list) else [self.active_position]
        for pos in positions:
            if not pos:
                continue
            pos_side = pos.get("side", "").upper()
            if side_to_close and pos_side != side_to_close.upper():
                continue
            entry_price = float(pos.get("entry_price", current_price))
            qty = float(pos.get("qty", 0.0))
            pnl = (current_price - entry_price) * qty if pos_side == "BUY" else (entry_price - current_price) * qty
            await self.close_position(current_price=current_price, reason=reason, pnl=pnl, side=pos_side, pos_to_close=pos)

    async def retry_tp_orders(self) -> bool:
        """Retry placing take profit limit orders for active positions if they failed previously."""
        if not self.active_position or self.simulation_mode:
            return False

        pos = self.active_position[0] if isinstance(self.active_position, list) else self.active_position
        if not pos:
            return False

        symbol = pos["symbol"]
        side = pos["side"]
        
        is_hedge = False
        try:
            dual_res = await self._send_request("GET", "/fapi/v1/positionSide/dual", action="CHECK_DUAL_SIDE")
            is_hedge = dual_res.get("dualSidePosition", False)
        except Exception:
            pass

        tp_targets = pos.get("tp_targets", [])
        if not tp_targets:
            return False

        placed_any = False
        for target in tp_targets:
            if target.get("hit") or target.get("order_id"):
                continue

            try:
                tp_params = {
                    "symbol": symbol.upper(),
                    "side": "SELL" if side.upper() == "BUY" else "BUY",
                    "type": "LIMIT",
                    "price": float(target["target_price"]),
                    "quantity": float(target["qty"]),
                    "timeInForce": "GTC"
                }
                if is_hedge:
                    tp_params["positionSide"] = "LONG" if side.upper() == "BUY" else "SHORT"
                else:
                    tp_params["reduceOnly"] = "true"
                
                tp_res = await self._send_request("POST", "/fapi/v1/order", tp_params, action="RETRY_TP_LIMIT")
                target["order_id"] = str(tp_res.get("orderId"))
                placed_any = True
            except Exception as e:
                logger.error(f"Retry TP target failed for price {target['target_price']}: {e}")

        if placed_any:
            still_failed = any(not t.get("hit") and not t.get("order_id") for t in tp_targets)
            pos["status"] = "TP_ORDERS_FAILED" if still_failed else "ACTIVE"
            if self.db:
                pos_key = f"active_position_bot_{self.bot_id}" if self.bot_id else "active_position"
                self.db.set_state(pos_key, self.active_position)
                self.db.log_message("INFO", f"Manually retried and placed TP limit orders for {symbol}.", bot_id=self.bot_id)
            return not still_failed
            
        return False