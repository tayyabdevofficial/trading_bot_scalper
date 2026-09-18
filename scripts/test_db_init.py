import sys, os
sys.path.append(os.getcwd())
from database import Database
import sqlite3

db = Database("trading_bot.db")
conn = sqlite3.connect("trading_bot.db")
c = conn.cursor()
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in c.fetchall()]
print("Tables in database:", tables)
assert "backtest_results" in tables
print("SUCCESS: backtest_results table exists!")
