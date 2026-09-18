import logging
import asyncio
from database import Database
from main import TradingBot
from config import Config

logger = logging.getLogger("BotManager")

class BotManager:
    def __init__(self, db=None, simulation_mode=None):
        self.db = db if db is not None else Database()
        self.simulation_mode = simulation_mode
        self.active_bots = {}  # dict of bot_id: TradingBot instance

    async def start_all_bots(self, network=None):
        """Loads and starts all bots that should be running from database, optionally filtered by network."""
        bots = self.db.get_bots(network=network)
        for b in bots:
            if b.get("is_running"):
                await self.start_bot(b["id"])

    async def start_bot(self, bot_id) -> bool:
        """Starts a specific bot instance."""
        if bot_id in self.active_bots:
            logger.info(f"Bot {bot_id} is already running.")
            return True
            
        bot_data = self.db.get_bot(bot_id)
        if not bot_data:
            logger.error(f"Cannot start bot: Bot ID {bot_id} not found in database.")
            return False

        try:
            bot_network = (bot_data.get("network") or "mainnet").lower()
            
            # Determine simulation / testnet flags:
            # Testnet is strictly local database paper trading (simulation_mode=True, use_testnet=False).
            # Mainnet is live exchange trading (simulation_mode=False, use_testnet=False).
            if bot_network == "testnet":
                is_sim = True
                use_testnet = False
            else:
                is_sim = False
                use_testnet = False

            logger.info(f"Instantiating bot {bot_id} for {bot_data['symbol']} [{bot_network.upper()}] using strategy {bot_data['strategy_name']}...")
            bot = TradingBot(
                bot_id=bot_id,
                symbol=bot_data["symbol"],
                strategy_name=bot_data["strategy_name"],
                parameters=bot_data["parameters"],
                db=self.db,
                simulation_mode=is_sim,
                use_testnet=use_testnet,
                network=bot_network
            )
            await bot.start()
            self.active_bots[bot_id] = bot
            self.db.update_bot_status(bot_id, is_running=True)
            logger.info(f"Bot {bot_id} [{bot_network.upper()}] started successfully.")
            return True
        except Exception as e:
            logger.error(f"Failed to start bot {bot_id}: {e}", exc_info=True)
            self.db.log_message("ERROR", f"Failed to start bot: {str(e)}", bot_id=bot_id)
            return False

    async def stop_bot(self, bot_id, close_positions=True) -> bool:
        """Stops a specific running bot instance."""
        if bot_id not in self.active_bots:
            logger.info(f"Bot {bot_id} is not currently running.")
            self.db.update_bot_status(bot_id, is_running=False)
            return True

        try:
            bot = self.active_bots[bot_id]
            await bot.stop(close_positions=close_positions)
            del self.active_bots[bot_id]
            self.db.update_bot_status(bot_id, is_running=False)
            logger.info(f"Bot {bot_id} stopped successfully.")
            return True
        except Exception as e:
            logger.error(f"Failed to stop bot {bot_id}: {e}")
            return False

    async def stop_all_bots(self, close_positions=True, network=None):
        """Stops running bots, optionally filtered by network."""
        active_ids = list(self.active_bots.keys())
        for bot_id in active_ids:
            bot = self.active_bots.get(bot_id)
            if bot and (network is None or getattr(bot, "network", "").lower() == network.lower()):
                await self.stop_bot(bot_id, close_positions=close_positions)

    async def add_bot(self, symbol, strategy_name, parameters_dict, network="mainnet") -> int:
        """Creates a new bot configuration in database with designated network."""
        net = (network or "mainnet").lower()
        bot_id = self.db.add_bot(symbol, strategy_name, parameters_dict, network=net)
        self.db.log_message("INFO", f"Created new {net.upper()} bot instance {bot_id} for {symbol} using strategy {strategy_name}", bot_id=bot_id)
        return bot_id

    async def delete_bot(self, bot_id) -> bool:
        """Stops and deletes a bot configuration."""
        await self.stop_bot(bot_id)
        self.db.delete_bot(bot_id)
        logger.info(f"Deleted bot instance {bot_id} from database.")
        return True
