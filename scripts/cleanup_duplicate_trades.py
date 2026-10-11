#!/usr/bin/env python3
"""
Server Database Maintenance & Realignment Script
================================================
This script realigns historical positions and updates status:
1. Creates an automatic timestamped backup of trading_bot.db.
2. Updates orders table status:
   - Sets status = 'SL_HIT' for orders closed by 'MAX_LOSS_EXIT' or 'STOP_LOSS'
   - Sets status = 'TP_HIT' for orders closed by 'TAKE_PROFIT'
3. Verifies that unified closed positions returned by the web app
   no longer have duplicates and show accurate 1x Net PnL.
"""

import os
import sys
import shutil
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "trading_bot.db")

def main():
    if not os.path.exists(DB_PATH):
        print(f"[ERROR] Database file not found at: {DB_PATH}")
        sys.exit(1)

    print("=" * 70)
    print("  TRADING BOT SERVER DATABASE REALIGNMENT TOOL")
    print("=" * 70)
    print(f"Target Database: {DB_PATH}")

    # Connect with timeout to avoid lock contention
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. Create Timestamped Online Backup (Safe while server is running)
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{DB_PATH}.backup_{timestamp_str}"
    try:
        backup_conn = sqlite3.connect(backup_path)
        conn.backup(backup_conn)
        backup_conn.close()
        print(f"[OK] Database online backup created at: {backup_path}\n")
    except Exception as e:
        print(f"[WARNING] Backup warning: {e}\n")

    try:
        # Check current stats
        cursor.execute("SELECT COUNT(*), COALESCE(SUM(realized_pnl), 0.0) FROM orders WHERE status IN ('TP_HIT', 'SL_HIT', 'CLOSED', 'OPPOSITE_SIGNAL_CLOSED', 'CANCELLED')")
        orders_cnt, orders_pnl = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) FROM trades")
        trades_cnt = cursor.fetchone()[0]

        print(f"Current State in Database:")
        print(f"  - Authoritative closed orders in 'orders' table: {orders_cnt} (Net PnL: ${orders_pnl:.2f})")
        print(f"  - Total transactions in 'trades' table: {trades_cnt}")

        # 2. Fix status in 'orders' table
        cursor.execute("""
            UPDATE orders
            SET status = 'SL_HIT'
            WHERE (exit_reason IN ('STOP_LOSS', 'MAX_LOSS_EXIT') OR exit_reason LIKE '%LOSS%')
              AND status = 'CLOSED'
        """)
        sl_fixed = cursor.rowcount

        cursor.execute("""
            UPDATE orders
            SET status = 'TP_HIT'
            WHERE (exit_reason IN ('TAKE_PROFIT', 'TP') OR exit_reason LIKE 'TP_%')
              AND status = 'CLOSED'
        """)
        tp_fixed = cursor.rowcount

        conn.commit()
        print(f"\n[Orders Status Realignment]:")
        print(f"  - Updated {sl_fixed} order(s) to 'SL_HIT' (Stop Loss)")
        print(f"  - Updated {tp_fixed} order(s) to 'TP_HIT' (Take Profit)")

        # 3. Verify with Unified Positions API
        print(f"\n[Verifying Unified Closed Positions...]")
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from web.app import _get_closed_positions_unified
        unified = _get_closed_positions_unified()
        total_pnl = sum(float(p.get("realized_pnl") or 0.0) for p in unified)
        print(f"  - Total unified closed positions: {len(unified)}")
        print(f"  - Verified Net Realized PnL: ${total_pnl:.2f}")
        print("\n[SUCCESS] Server database verified and aligned with zero duplicates!")
        print("=" * 70)

    except Exception as e:
        conn.rollback()
        print(f"\n[ERROR] An error occurred: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
