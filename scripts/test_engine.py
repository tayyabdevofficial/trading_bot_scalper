import sys, os
sys.path.append(os.getcwd())
from backtest.data_loader import KlineDataLoader
from backtest.engine import BacktestEngine
from strategy import STRATEGY_MAP

df = KlineDataLoader.load_klines('BTCUSDT', '15m', days=30)
strat_cls = STRATEGY_MAP['EMA_Ribbon_Scalp']
strat = strat_cls()

res = BacktestEngine.run_backtest(
    strategy=strat,
    df=df,
    stop_loss_pct=1.5,
    take_profit_pct=2.5,
    leverage=10,
    trade_amount_usd=20.0
)

print("EMA_Ribbon_Scalp BTCUSDT 15m (30 Days) Result:")
print("Total Trades:", res["total_trades"])
print("Wins:", res["win_trades"])
print("Losses:", res["loss_trades"])
print("Win Rate:", res["win_rate"], "%")
print("Total PnL: $", res["total_pnl"])
print("Profit Factor:", res["profit_factor"])
print("Max Drawdown: $", res["max_drawdown"])
