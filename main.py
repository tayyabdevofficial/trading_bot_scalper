import asyncio
import logging
import sys
import strategy
from database import Database
from config import Config
from data_engine import DataEngine
from risk_manager import RiskManager
from execution_handler import ExecutionHandler

# Configure Logger - Console gets INFO, File gets ERROR only to prevent log bloat
logger = logging.getLogger("BotRunner")
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
# Clear any default handlers to avoid duplication
root_logger.handlers = []

_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
root_logger.addHandler(_console_handler)

_file_handler = logging.FileHandler("live_trading.log", encoding="utf-8")
_file_handler.setLevel(logging.ERROR)
_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
root_logger.addHandler(_file_handler)
 
class TradingBot:
    def __init__(self, bot_id: int, symbol: str, strategy_name: str, parameters: dict,
                 db=None, simulation_mode=None, use_testnet=None, network=None):
        self.bot_id = bot_id
        self.symbol = symbol.upper()
        self.strategy_name = strategy_name
        self.parameters = parameters
        self.network = (network or parameters.get("network", "mainnet")).lower()
        
        # Core modules
        self.db = db if db is not None else Database()
        
        # Load risk parameters
        self.risk_manager = RiskManager(
            risk_percent=float(parameters.get("risk_percent", 2.0)),
            stop_loss_pct=float(parameters.get("stop_loss_pct", 1.0)),
            take_profit_pct=float(parameters.get("take_profit_pct", 2.0)),
            leverage=int(parameters.get("leverage", 20)),
            trade_amount_usd=float(parameters.get("trade_amount_usd")) if parameters.get("trade_amount_usd") is not None else None
        )
        
        # Pass network directly to ExecutionHandler
        self.execution = ExecutionHandler(
            db=self.db,
            bot_id=self.bot_id,
            simulation_mode=simulation_mode,
            use_testnet=use_testnet,
            network=self.network
        )
        
        # Load strategy dynamically
        strategy_params = parameters.copy()
        strategy_params["symbol"] = self.symbol
        self.strategy = strategy.get_strategy(strategy_name, strategy_params)
        
        # Classify strategy type for Choppiness filter
        self.strategy_type = "TREND"
        if self.strategy_name in [
            "Bollinger_Bands", "Stochastic_RSI", "RSI_Divergence_Scalp"
        ]:
            self.strategy_type = "REVERSION"

        # Data Engine using shared multiplexer (5m, 15m)
        self.data_engine = DataEngine(
            symbol=self.symbol,
            interval=parameters.get("candle_interval", "5m"),
            db=self.db,
            use_testnet=use_testnet,
            simulation_mode=simulation_mode
        )

        # Register callbacks
        self.data_engine.register_callback(self.on_candle_close)
        self.data_engine.register_tick_callback(self.on_price_tick)
        
        self.is_running = False

    async def on_candle_close(self, df):
        """Called when a candlestick closes. Evaluates trading strategy."""
        try:
            # 1. Calculate local ATR & CHOP indicators
            chop_val = 50.0
            try:
                chop_series = DataEngine.calculate_chop(df)
                if len(chop_series) > 0:
                    chop_val = chop_series.iloc[-1]
            except Exception as calc_err:
                logger.error(f"Error calculating local indicators: {calc_err}")

            signal = self.strategy.generate_signal(df)
            current_price = df.iloc[-1]["close"]
            
            logger.info(f"[{self.network.upper()} {self.symbol}] Candle closed at {current_price}. Signal: {signal}")
            if signal != "HOLD":
                self.db.log_message("INFO", f"[{self.network.upper()} {self.symbol}] Candle closed at {current_price}. Signal: {signal}", bot_id=self.bot_id)

            # Exchange Sync: Ensure local active positions match actual Binance exchange state
            if not self.execution.simulation_mode and self.execution.active_position:
                for pos in list(self.execution.active_position):
                    ex_qty = await self.execution.get_exchange_position_qty(pos["symbol"], pos["side"])
                    if ex_qty is not None and ex_qty == 0.0:
                        logger.info(f"[{self.symbol}] Sync check: Position {pos['side']} on Binance is confirmed 0. Syncing local closed position.")
                        sync_price = pos.get("entry_price", current_price)
                        pnl = (sync_price - pos["entry_price"]) * pos["qty"] if pos["side"] == "BUY" else (pos["entry_price"] - sync_price) * pos["qty"]
                        await self.execution.close_position(sync_price, "EXCHANGE_SYNC", pnl, pos["side"], pos_to_close=pos)

            # Signal evaluation:
            # Long and Short positions run independently (matching BacktestEngine).
            # Repeated signals in the same direction execute DCA position averaging.
            can_enter = (signal in ("BUY", "SELL"))

            if can_enter and signal != "HOLD":
                # 2. Chop Market regime filter check
                if self.parameters.get("use_chop_filter", False):
                    if self.strategy_type == "TREND" and chop_val > 60:
                        self.db.log_message("WARNING", f"[{self.symbol}] BUY/SELL signal BLOCKED by Choppiness Filter (CHOP={chop_val:.1f} > 60)", bot_id=self.bot_id)
                        return
                    if self.strategy_type == "REVERSION" and chop_val < 40:
                        self.db.log_message("WARNING", f"[{self.symbol}] BUY/SELL signal BLOCKED by Choppiness Filter (CHOP={chop_val:.1f} < 40)", bot_id=self.bot_id)
                        return

                # 3. Multi-Timeframe Trend filter check (1h 50 EMA)
                if self.parameters.get("use_mtf_filter", False):
                    htf = "1h"
                    htf_df = await DataEngine.fetch_external_klines(self.symbol, htf, limit=100)
                    if not htf_df.empty and len(htf_df) >= 50:
                        htf_close = htf_df["close"]
                        htf_ema50 = htf_close.ewm(span=50, adjust=False).mean()
                        latest_htf_price = htf_close.iloc[-1]
                        latest_htf_ema50 = htf_ema50.iloc[-1]
                        
                        is_htf_uptrend = latest_htf_price > latest_htf_ema50
                        
                        if signal == "BUY" and not is_htf_uptrend:
                            self.db.log_message("WARNING", f"[{self.symbol}] BUY signal BLOCKED by MTF filter ({htf} price {latest_htf_price} is below 50 EMA {latest_htf_ema50:.2f})", bot_id=self.bot_id)
                            return
                        if signal == "SELL" and is_htf_uptrend:
                            self.db.log_message("WARNING", f"[{self.symbol}] SELL signal BLOCKED by MTF filter ({htf} price {latest_htf_price} is above 50 EMA {latest_htf_ema50:.2f})", bot_id=self.bot_id)
                            return

                balance = await self.execution.get_available_balance()
                qty = self.risk_manager.calculate_position_size(balance, current_price)
                
                if qty > 0:
                    sl_price, tp_price = self.risk_manager.get_sl_tp_prices(signal, current_price)
                    logger.info(f"[{self.network.upper()} {self.symbol}] Scalp Entry: {signal} Qty: {qty}. SL: {sl_price}, TP: {tp_price}")
                    await self.execution.execute_order(
                        symbol=self.symbol,
                        side=signal,
                        qty=qty,
                        price=current_price,
                        sl_price=sl_price,
                        tp_price=tp_price
                    )
        except Exception as e:
            logger.error(f"Error in bot {self.bot_id} on_candle_close: {e}", exc_info=True)

    async def on_price_tick(self, current_price: float):
        """Called on every single price update. Evaluates SL/TP real-time exit."""
        try:
            # Real-time Stop-Loss and Take-Profit monitoring
            if self.execution.active_position:
                await self.execution.check_and_exit_positions(current_price)
        except Exception as e:
            logger.error(f"Error in bot {self.bot_id} on_price_tick: {e}")

    async def start(self):
        """Starts the bot."""
        self.is_running = True
        logger.info(f"Starting bot {self.bot_id} ({self.symbol} on {self.network.upper()})...")
        self.db.log_message("INFO", f"Bot Instance {self.bot_id} ({self.symbol} - {self.network.upper()}) Started.", bot_id=self.bot_id)
        
        # Sync leverage setting on exchange if live
        await self.execution.set_leverage(self.symbol, self.risk_manager.leverage)
        await self.data_engine.start()

    async def stop(self, close_positions=True):
        """Stops the bot."""
        self.is_running = False
        logger.info(f"Stopping bot {self.bot_id} ({self.symbol} on {self.network.upper()})...")
        self.db.log_message("INFO", f"Bot Instance {self.bot_id} ({self.symbol} - {self.network.upper()}) Stopped.", bot_id=self.bot_id)
        
        if close_positions and self.execution.active_position:
            current_price = 0.0
            if self.data_engine.klines:
                current_price = self.data_engine.klines[-1]["close"]
            await self.execution.close_all_positions(current_price=current_price, reason="FORCE_STOP_EXIT")
            
        await self.data_engine.stop()
