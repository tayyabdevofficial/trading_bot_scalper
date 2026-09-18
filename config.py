import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class Config:
    BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
    BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
    USE_TESTNET = os.getenv("USE_TESTNET", "true").lower() == "true"
    
    SIMULATION_MODE = os.getenv("SIMULATION_MODE", "true").lower() == "true"
    TRADE_SYMBOL = os.getenv("TRADE_SYMBOL", "BTCUSDT").upper()
    CANDLE_INTERVAL = os.getenv("CANDLE_INTERVAL", "1m")
    LEVERAGE = int(os.getenv("LEVERAGE", "20"))
    
    RISK_PERCENT = float(os.getenv("RISK_PERCENT", "2.0"))
    STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "1.0"))
    TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "2.0"))
    
    # Strategy parameters
    EMA_SHORT = int(os.getenv("EMA_SHORT", "9"))
    EMA_LONG = int(os.getenv("EMA_LONG", "21"))
    RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
    RSI_OVERBOUGHT = float(os.getenv("RSI_OVERBOUGHT", "70"))
    RSI_OVERSOLD = float(os.getenv("RSI_OVERSOLD", "30"))

    @classmethod
    def validate(cls):
        """Validate crucial configuration requirements."""
        if not cls.SIMULATION_MODE:
            if not cls.BINANCE_API_KEY or not cls.BINANCE_API_SECRET:
                raise ValueError("Live trading requires BINANCE_API_KEY and BINANCE_API_SECRET.")
        
        if cls.LEVERAGE < 1 or cls.LEVERAGE > 125:
            raise ValueError("Leverage must be between 1 and 125.")
            
        if cls.RISK_PERCENT <= 0 or cls.RISK_PERCENT > 100:
            raise ValueError("Risk percent must be between 0 and 100.")
