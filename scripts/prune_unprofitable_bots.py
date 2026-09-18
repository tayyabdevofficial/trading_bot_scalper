"""
Prune Unprofitable Bots
Deletes all bot instances with non-positive (negative or zero) backtest PnL,
keeping strictly the verified profitable bots with PnL > 0.
Also backups the complete backtest dataset into `backtest_results_all`
and prunes `backtest_results` to contain only PnL > 0 entries.
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "trading_bot.db")

def prune_unprofitable():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print(f"Connecting to {DB_PATH}...")

    # 1. Total bots before
    cursor.execute("SELECT COUNT(*) FROM bot_instances")
    initial_bots = cursor.fetchone()[0]
    print(f"Initial bot_instances count: {initial_bots}")

    cursor.execute("SELECT COUNT(*) FROM backtest_results")
    initial_backtests = cursor.fetchone()[0]
    print(f"Initial backtest_results count: {initial_backtests}")

    # 2. Backup all backtest results to backtest_results_all table
    cursor.execute("DROP TABLE IF EXISTS backtest_results_all")
    cursor.execute("CREATE TABLE backtest_results_all AS SELECT * FROM backtest_results")
    print("Created backup table: backtest_results_all")

    # 3. Identify and delete unprofitable bot instances
    # Keep only bots where (symbol, strategy_name) has backtest total_pnl > 0
    cursor.execute("""
        DELETE FROM bot_instances
        WHERE id NOT IN (
            SELECT b.id FROM bot_instances b
            JOIN backtest_results br ON UPPER(b.symbol) = UPPER(br.symbol) AND b.strategy_name = br.strategy_name
            WHERE br.total_pnl > 0
        )
    """)
    deleted_bots = cursor.rowcount
    print(f"Deleted {deleted_bots} unprofitable bot instances from bot_instances.")

    # 4. Prune backtest_results table to only keep total_pnl > 0
    cursor.execute("DELETE FROM backtest_results WHERE total_pnl <= 0")
    deleted_backtests = cursor.rowcount
    print(f"Deleted {deleted_backtests} negative/zero PnL entries from backtest_results.")

    # Commit changes
    conn.commit()

    # 5. Verify post-prune counts
    cursor.execute("SELECT COUNT(*) FROM bot_instances")
    final_bots = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM backtest_results")
    final_backtests = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT symbol) FROM bot_instances")
    unique_symbols = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT strategy_name) FROM bot_instances")
    unique_strategies = cursor.fetchone()[0]

    print("\n--- Summary of Kept Profitable Bots ---")
    print(f"Total Profitable Bots Kept: {final_bots}")
    print(f"Total Profitable Backtest Records: {final_backtests}")
    print(f"Unique Symbols Covered: {unique_symbols}")
    print(f"Unique Strategies Active: {unique_strategies}")

    # Breakdown by strategy
    cursor.execute("""
        SELECT b.strategy_name, COUNT(*), ROUND(AVG(br.total_pnl), 2), ROUND(AVG(br.win_rate), 2)
        FROM bot_instances b
        JOIN backtest_results br ON UPPER(b.symbol) = UPPER(br.symbol) AND b.strategy_name = br.strategy_name
        GROUP BY b.strategy_name
        ORDER BY COUNT(*) DESC
    """)
    print("\nProfitable Bots Per Strategy:")
    for row in cursor.fetchall():
        print(f"  - {row[0]}: {row[1]} bots | Avg PnL: +${row[2]} | Avg Win Rate: {row[3]}%")

    conn.close()

if __name__ == "__main__":
    prune_unprofitable()
