import os
import sqlite3
import sys

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "trading_bot.db")

def clean_database():
    if not os.path.exists(DB_PATH):
        print(f"Database file not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print("Cleaning database...")

    # Tables to wipe completely
    tables_to_clear = [
        "bot_instances",
        "orders",
        "trades",
        "daily_pnl",
        "hourly_pnl",
        "binance_api_logs",
        "system_logs",
        "backtest_results",
        "backtest_results_all"
    ]

    for table in tables_to_clear:
        cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
        if cursor.fetchone():
            cursor.execute(f"DROP TABLE {table}")
            print(f"  - Dropped table: {table} (will be recreated fresh)")

    # Clear bot-specific states while keeping admin credentials & global configs
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bot_state'")
    if cursor.fetchone():
        cursor.execute("DELETE FROM bot_state WHERE key LIKE 'active_position_bot_%'")
        cursor.execute("DELETE FROM bot_state WHERE key LIKE 'bot_error_%'")
        cursor.execute("DELETE FROM bot_state WHERE key LIKE 'virtual_balance_%'")
        # Reset default virtual balance
        cursor.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES ('virtual_balance', '1000.0')")
        print("  - Cleared bot-specific keys from bot_state and reset virtual balance to $1000")

    conn.commit()
    conn.close()

    # Recreate tables fresh with updated schema
    from database import Database
    Database(DB_PATH)
    print("  - Database tables recreated fresh with updated schema.")

    # Re-connect to verify counts
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print("\nVerification of table counts post-cleanup:")
    for table in [t for t in tables_to_clear if t != "backtest_results_all"]:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"  {table}: {count} rows")

    cursor.execute("SELECT COUNT(*) FROM symbols_config")
    sym_count = cursor.fetchone()[0]
    print(f"  symbols_config preserved: {sym_count} symbols")

    conn.close()
    print("\nDatabase cleanup successfully completed!")

if __name__ == "__main__":
    clean_database()
