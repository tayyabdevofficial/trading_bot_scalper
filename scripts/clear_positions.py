import sqlite3
import os

db_path = "trading_bot.db"
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    # 1. Clear orders and trades
    c.execute("DELETE FROM orders")
    c.execute("DELETE FROM trades")
    
    # 2. Clear PnL history
    c.execute("DELETE FROM daily_pnl")
    try:
        c.execute("DELETE FROM hourly_pnl")
    except sqlite3.OperationalError:
        pass
        
    # 3. Clear active position states across all bots
    c.execute("DELETE FROM bot_state WHERE key LIKE 'active_position%'")
    
    # 4. Optional: Reset virtual balance to 500.0
    c.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES ('virtual_balance', '500.0')")
    
    conn.commit()
    conn.close()
    print("[OK] Successfully cleared all orders, trades, active positions, and PnL records from the database!")
else:
    print(f"Database {db_path} does not exist.")
