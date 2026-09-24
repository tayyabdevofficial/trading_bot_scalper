import logging

logger = logging.getLogger("RiskManager")

class RiskManager:
    def __init__(self, risk_percent=2.0, stop_loss_pct=1.0, take_profit_pct=2.0, leverage=20, trade_amount_usd=None, **kwargs):
        self.risk_percent = float(risk_percent)
        self.stop_loss_pct = float(stop_loss_pct)
        self.take_profit_pct = float(take_profit_pct)
        self.leverage = int(leverage)
        self.trade_amount_usd = float(trade_amount_usd) if trade_amount_usd is not None else None

    def calculate_position_size(self, balance: float, entry_price: float, size_multiplier: float = 1.0) -> float:
        """
        Calculates position quantity to buy/sell based on risk settings or direct USD size.
        If trade_amount_usd is set, Position Value = (trade_amount_usd * size_multiplier) * leverage.
        Otherwise: Position Size = (Balance * Risk% * size_multiplier) / StopLoss%
        """
        if balance <= 0:
            logger.warning("Account balance is zero or negative. Cannot size position.")
            return 0.0
            
        multiplier = max(0.01, float(size_multiplier))
        if self.trade_amount_usd is not None and self.trade_amount_usd > 0:
            usd_to_use = min(self.trade_amount_usd * multiplier, balance)
            position_value = usd_to_use * self.leverage
        else:
            risk_amount = balance * (self.risk_percent / 100.0) * multiplier
            stop_loss_decimal = self.stop_loss_pct / 100.0
            position_value = risk_amount / stop_loss_decimal
        
        # Cap position value based on leverage safety (max buying power is balance * leverage)
        max_buying_power = balance * self.leverage * 0.95  # 95% threshold to avoid margin calls
        if position_value > max_buying_power:
            logger.info(f"Capping position value at {max_buying_power:.2f} due to leverage limit.")
            position_value = max_buying_power
            
        # Calculate asset quantity
        qty = position_value / entry_price
        
        # Round quantity to safety precision
        return round(qty, 3) 

    def get_sl_tp_prices(self, side: str, entry_price: float) -> tuple:
        """
        Calculates Stop Loss and Take Profit prices based on configured percentages.
        If stop_loss_pct is 0.0, Stop Loss is disabled (0.0).
        """
        tp_distance = entry_price * (self.take_profit_pct / 100.0)
        
        if self.stop_loss_pct > 0:
            sl_distance = entry_price * (self.stop_loss_pct / 100.0)
            sl_price = (entry_price - sl_distance) if side.upper() == "BUY" else (entry_price + sl_distance)
        else:
            sl_price = 0.0
            
        if side.upper() == "BUY":
            tp_price = entry_price + tp_distance
        elif side.upper() == "SELL":
            tp_price = entry_price - tp_distance
        else:
            raise ValueError(f"Invalid trading side: {side}")
            
        return round(sl_price, 8), round(tp_price, 8)
