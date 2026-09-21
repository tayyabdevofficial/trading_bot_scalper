import sqlite3
import os
import json
from datetime import datetime

# Determine default DB path: unified single database
from config import Config
DB_PATH = os.path.join(os.path.dirname(__file__), "trading_bot.db")

class Database:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self.init_db()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=60.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=60000;")
        except Exception:
            pass
        return conn

    def init_db(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Bot Instances table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bot_instances (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    strategy_name TEXT NOT NULL,
                    parameters TEXT NOT NULL,
                    is_running BOOLEAN DEFAULT 0,
                    network TEXT NOT NULL DEFAULT 'mainnet'
                )
            """)

            # Dedicated Orders Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT UNIQUE,
                    bot_id INTEGER,
                    network TEXT NOT NULL DEFAULT 'mainnet',
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    position_side TEXT DEFAULT 'BOTH',
                    order_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    entry_price REAL NOT NULL,
                    qty REAL NOT NULL,
                    close_price REAL DEFAULT NULL,
                    sl_price REAL DEFAULT NULL,
                    tp_price REAL DEFAULT NULL,
                    tp_targets TEXT DEFAULT NULL,
                    realized_pnl REAL DEFAULT 0.0,
                    exit_reason TEXT DEFAULT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    entries_count INTEGER DEFAULT 1
                )
            """)
            try:
                cursor.execute("ALTER TABLE orders ADD COLUMN entries_count INTEGER DEFAULT 1")
            except Exception:
                pass
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_bot_id ON orders(bot_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_network ON orders(network)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at)")

            # Dedicated Binance API Calls Log Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS binance_api_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    network TEXT NOT NULL DEFAULT 'mainnet',
                    bot_id INTEGER,
                    symbol TEXT,
                    action TEXT NOT NULL,
                    method TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    request_payload TEXT,
                    response_status INTEGER,
                    response_payload TEXT,
                    is_error BOOLEAN DEFAULT 0,
                    error_message TEXT
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_logs_timestamp ON binance_api_logs(timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_logs_network ON binance_api_logs(network)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_logs_symbol ON binance_api_logs(symbol)")

            # Trades table (transaction log)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_id INTEGER,
                    network TEXT NOT NULL DEFAULT 'mainnet',
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    type TEXT NOT NULL,
                    price REAL NOT NULL,
                    qty REAL NOT NULL,
                    realized_pnl REAL DEFAULT 0.0,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    order_id TEXT UNIQUE
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_network ON trades(network)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_bot_id ON trades(bot_id)")
            
            # Daily PnL / Account history with network support
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_pnl (
                    date DATE,
                    network TEXT NOT NULL DEFAULT 'mainnet',
                    total_balance REAL NOT NULL,
                    daily_pnl REAL DEFAULT 0.0,
                    PRIMARY KEY (date, network)
                )
            """)

            # Hourly PnL / Account history with network support
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS hourly_pnl (
                    hour DATETIME,
                    network TEXT NOT NULL DEFAULT 'mainnet',
                    total_balance REAL NOT NULL,
                    hourly_pnl REAL DEFAULT 0.0,
                    PRIMARY KEY (hour, network)
                )
            """)
            
            # Logs table with network support
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_id INTEGER,
                    network TEXT DEFAULT 'mainnet',
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL
                )
            """)
            
            # Bot configuration / state storage
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            
            # Symbols config table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS symbols_config (
                    symbol TEXT PRIMARY KEY,
                    max_leverage INTEGER NOT NULL DEFAULT 125
                )
            """)
            
            # Populate default symbols config if empty
            cursor.execute("SELECT COUNT(*) FROM symbols_config")
            if cursor.fetchone()[0] == 0:
                default_symbols = [
                    ("BTCUSDT", 125),
                    ("ETHUSDT", 100),
                    ("SOLUSDT", 75),
                    ("XRPUSDT", 75),
                    ("ADAUSDT", 75),
                    ("LINKUSDT", 75)
                ]
                for sym, lev in default_symbols:
                    cursor.execute("""
                        INSERT INTO symbols_config (symbol, max_leverage)
                        VALUES (?, ?)
                    """, (sym, lev))
            
            # Backtest results table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS backtest_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    strategy_name TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    parameters TEXT NOT NULL,
                    total_trades INTEGER DEFAULT 0,
                    closed_trades INTEGER DEFAULT 0,
                    win_trades INTEGER DEFAULT 0,
                    loss_trades INTEGER DEFAULT 0,
                    win_rate REAL DEFAULT 0.0,
                    released_pnl REAL DEFAULT 0.0,
                    unreleased_pnl REAL DEFAULT 0.0,
                    open_trades_count INTEGER DEFAULT 0,
                    total_pnl REAL DEFAULT 0.0,
                    profit_factor REAL DEFAULT 0.0,
                    max_drawdown REAL DEFAULT 0.0,
                    period TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_bt_sym_strat ON backtest_results(symbol, strategy_name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_bt_strat ON backtest_results(strategy_name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_bt_tf ON backtest_results(timeframe)")

            # Ensure new columns exist in existing database
            for col_def in [
                ("closed_trades", "INTEGER DEFAULT 0"),
                ("total_long_trades", "INTEGER DEFAULT 0"),
                ("total_short_trades", "INTEGER DEFAULT 0"),
                ("closed_long_trades", "INTEGER DEFAULT 0"),
                ("closed_short_trades", "INTEGER DEFAULT 0"),
                ("trade_events", "TEXT DEFAULT NULL")
            ]:
                try:
                    cursor.execute(f"ALTER TABLE backtest_results ADD COLUMN {col_def[0]} {col_def[1]}")
                except Exception:
                    pass
            
            conn.commit()

    # --- BOT INSTANCE MANAGEMENT ---
    def add_bot(self, symbol, strategy_name, parameters_dict, network="mainnet"):
        param_str = json.dumps(parameters_dict)
        net = (network or "mainnet").lower()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO bot_instances (symbol, strategy_name, parameters, is_running, network)
                VALUES (?, ?, ?, 0, ?)
            """, (symbol.upper(), strategy_name, param_str, net))
            conn.commit()
            return cursor.lastrowid

    def get_bots(self, network=None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if network:
                cursor.execute("SELECT * FROM bot_instances WHERE network = ?", (network.lower(),))
            else:
                cursor.execute("SELECT * FROM bot_instances")
            bots = []
            for row in cursor.fetchall():
                d = dict(row)
                d["parameters"] = json.loads(d["parameters"])
                d["is_running"] = bool(d["is_running"])
                d["network"] = d.get("network", "mainnet")
                bots.append(d)
            return bots

    def get_bots_paginated(self, network=None, strategy=None, symbol=None, timeframe=None, is_running=None, search=None,
                           sort_by="id_asc", limit=20, offset=0):
        query = "SELECT * FROM bot_instances WHERE 1=1"
        count_query = "SELECT COUNT(*) FROM bot_instances WHERE 1=1"
        params = []
        count_params = []

        if network and network != "all":
            query += " AND network = ?"
            count_query += " AND network = ?"
            params.append(network.lower())
            count_params.append(network.lower())
        if strategy and strategy != "all":
            query += " AND strategy_name = ?"
            count_query += " AND strategy_name = ?"
            params.append(strategy)
            count_params.append(strategy)
        if timeframe and timeframe != "all":
            query += " AND (json_extract(parameters, '$.candle_interval') = ? OR json_extract(parameters, '$.interval') = ?)"
            count_query += " AND (json_extract(parameters, '$.candle_interval') = ? OR json_extract(parameters, '$.interval') = ?)"
            params.extend([timeframe, timeframe])
            count_params.extend([timeframe, timeframe])
        if symbol and symbol.strip():
            query += " AND symbol LIKE ?"
            count_query += " AND symbol LIKE ?"
            params.append(f"%{symbol.strip().upper()}%")
            count_params.append(f"%{symbol.strip().upper()}%")
        if is_running is not None:
            query += " AND is_running = ?"
            count_query += " AND is_running = ?"
            params.append(1 if is_running else 0)
            count_params.append(1 if is_running else 0)
        if search and search.strip():
            s = f"%{search.strip()}%"
            query += " AND (symbol LIKE ? OR strategy_name LIKE ? OR CAST(id AS TEXT) LIKE ?)"
            count_query += " AND (symbol LIKE ? OR strategy_name LIKE ? OR CAST(id AS TEXT) LIKE ?)"
            params.extend([s, s, s])
            count_params.extend([s, s, s])

        order_clause = "id ASC"
        if sort_by == "id_desc":
            order_clause = "id DESC"
        elif sort_by == "symbol_asc":
            order_clause = "symbol ASC"
        elif sort_by == "symbol_desc":
            order_clause = "symbol DESC"
        elif sort_by == "strategy_asc":
            order_clause = "strategy_name ASC"

        query += f" ORDER BY {order_clause} LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(count_query, count_params)
            total = cursor.fetchone()[0]

            cursor.execute(query, params)
            bots = []
            for row in cursor.fetchall():
                d = dict(row)
                d["parameters"] = json.loads(d["parameters"])
                d["is_running"] = bool(d["is_running"])
                d["network"] = d.get("network", "mainnet")
                bots.append(d)
            return bots, total

    def get_bot_performance_paginated(self, network=None, strategy=None, symbol=None, timeframe=None, is_running=None, search=None,
                                      sort_by="pnl_desc", limit=20, offset=0):
        """Query bots with aggregated PnL and trade statistics directly via SQL for ultra-fast performance."""
        where_clauses = ["1=1"]
        params = []
        count_params = []

        if network and network != "all":
            where_clauses.append("b.network = ?")
            params.append(network.lower())
            count_params.append(network.lower())
        if strategy and strategy != "all":
            where_clauses.append("b.strategy_name = ?")
            params.append(strategy)
            count_params.append(strategy)
        if timeframe and timeframe != "all":
            where_clauses.append("(json_extract(b.parameters, '$.candle_interval') = ? OR json_extract(b.parameters, '$.interval') = ?)")
            params.extend([timeframe, timeframe])
            count_params.extend([timeframe, timeframe])
        if symbol and symbol.strip():
            where_clauses.append("b.symbol LIKE ?")
            s_sym = f"%{symbol.strip().upper()}%"
            params.append(s_sym)
            count_params.append(s_sym)
        if is_running is not None:
            where_clauses.append("b.is_running = ?")
            params.append(1 if is_running else 0)
            count_params.append(1 if is_running else 0)
        if search and search.strip():
            s_search = f"%{search.strip()}%"
            where_clauses.append("(b.symbol LIKE ? OR b.strategy_name LIKE ? OR CAST(b.id AS TEXT) LIKE ?)")
            params.extend([s_search, s_search, s_search])
            count_params.extend([s_search, s_search, s_search])

        where_sql = " AND ".join(where_clauses)

        count_sql = f"SELECT COUNT(*) FROM bot_instances b WHERE {where_sql}"

        order_map = {
            "pnl_desc": "net_pnl DESC, total_trades DESC",
            "pnl_asc": "net_pnl ASC, total_trades ASC",
            "winrate_desc": "win_rate DESC, net_pnl DESC",
            "winrate_asc": "win_rate ASC, net_pnl ASC",
            "trades_desc": "total_trades DESC, net_pnl DESC",
            "trades_asc": "total_trades ASC, net_pnl ASC",
            "id_asc": "b.id ASC",
            "id_desc": "b.id DESC",
            "symbol_asc": "b.symbol ASC"
        }
        order_clause = order_map.get(sort_by, "net_pnl DESC, total_trades DESC")

        main_sql = f"""
            SELECT 
                b.id,
                b.symbol,
                b.strategy_name,
                b.parameters,
                b.is_running,
                b.network,
                COALESCE(SUM(o.realized_pnl), 0.0) as net_pnl,
                COUNT(o.id) as total_trades,
                COUNT(CASE WHEN o.realized_pnl > 0 THEN 1 END) as profit_count,
                COALESCE(SUM(CASE WHEN o.realized_pnl > 0 THEN o.realized_pnl ELSE 0.0 END), 0.0) as profit_sum,
                COUNT(CASE WHEN o.realized_pnl < 0 THEN 1 END) as loss_count,
                COALESCE(SUM(CASE WHEN o.realized_pnl < 0 THEN ABS(o.realized_pnl) ELSE 0.0 END), 0.0) as loss_sum
            FROM bot_instances b
            LEFT JOIN orders o ON b.id = o.bot_id AND o.status IN ('TP_HIT', 'SL_HIT', 'CLOSED', 'OPPOSITE_SIGNAL_CLOSED')
            WHERE {where_sql}
            GROUP BY b.id
            ORDER BY {order_clause}
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(count_sql, count_params)
            total = cursor.fetchone()[0]

            cursor.execute(main_sql, params)
            bots = []
            for row in cursor.fetchall():
                d = dict(row)
                d["parameters"] = json.loads(d["parameters"])
                d["is_running"] = bool(d["is_running"])
                d["network"] = d.get("network", "mainnet")
                d["net_pnl"] = round(float(d.get("net_pnl") or 0.0), 2)
                d["profit_sum"] = round(float(d.get("profit_sum") or 0.0), 2)
                d["loss_sum"] = round(float(d.get("loss_sum") or 0.0), 2)
                tt = int(d.get("total_trades") or 0)
                pc = int(d.get("profit_count") or 0)
                d["win_rate"] = round((pc / tt * 100.0), 1) if tt > 0 else 0.0
                bots.append(d)

            return bots, total

    def get_bot(self, bot_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM bot_instances WHERE id = ?", (bot_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["parameters"] = json.loads(d["parameters"])
                d["is_running"] = bool(d["is_running"])
                d["network"] = d.get("network", "mainnet")
                return d
            return None

    def update_bot_status(self, bot_id, is_running):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE bot_instances SET is_running = ? WHERE id = ?
            """, (1 if is_running else 0, bot_id))
            conn.commit()

    def update_bot_config(self, bot_id, strategy_name, parameters_dict, network=None):
        param_str = json.dumps(parameters_dict)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if network:
                cursor.execute("""
                    UPDATE bot_instances 
                    SET strategy_name = ?, parameters = ?, network = ?
                    WHERE id = ?
                """, (strategy_name, param_str, network.lower(), bot_id))
            else:
                cursor.execute("""
                    UPDATE bot_instances 
                    SET strategy_name = ?, parameters = ?
                    WHERE id = ?
                """, (strategy_name, param_str, bot_id))
            conn.commit()

    def delete_bot(self, bot_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM bot_instances WHERE id = ?", (bot_id,))
            cursor.execute("DELETE FROM trades WHERE bot_id = ?", (bot_id,))
            cursor.execute("DELETE FROM orders WHERE bot_id = ?", (bot_id,))
            cursor.execute("DELETE FROM system_logs WHERE bot_id = ?", (bot_id,))
            cursor.execute("DELETE FROM bot_state WHERE key = ?", (f"active_position_bot_{bot_id}",))
            conn.commit()

    # --- DEDICATED ORDERS MANAGEMENT ---
    def create_order(self, order_id, bot_id, network, symbol, side, order_type, entry_price, qty,
                     sl_price=None, tp_price=None, tp_targets=None, position_side="BOTH", status="OPEN", entries_count=1):
        """Insert a newly opened order. Returns the order ID or DB row ID."""
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        tp_targets_str = json.dumps(tp_targets) if tp_targets is not None else None
        net = (network or "mainnet").lower()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO orders (
                    order_id, bot_id, network, symbol, side, position_side, order_type,
                    status, entry_price, qty, sl_price, tp_price, tp_targets,
                    realized_pnl, created_at, updated_at, entries_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    entry_price = excluded.entry_price,
                    qty = excluded.qty,
                    updated_at = excluded.updated_at,
                    entries_count = excluded.entries_count
            """, (
                order_id, bot_id, net, symbol.upper(), side.upper(), position_side.upper(),
                order_type, status, float(entry_price), float(qty),
                float(sl_price) if sl_price is not None else None,
                float(tp_price) if tp_price is not None else None,
                tp_targets_str, now_str, now_str, int(entries_count)
            ))
            conn.commit()
            return cursor.lastrowid

    def update_order_dca(self, order_id, entry_price, qty, tp_price=None, tp_targets=None, entries_count=1):
        """Update existing primary order when DCA is executed (updates weighted avg entry price, total qty, TP targets & count)."""
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        tp_targets_str = json.dumps(tp_targets) if tp_targets is not None else None
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE orders
                SET entry_price = ?,
                    qty = ?,
                    tp_price = ?,
                    tp_targets = ?,
                    entries_count = ?,
                    updated_at = ?
                WHERE order_id = ?
            """, (float(entry_price), float(qty), float(tp_price) if tp_price is not None else None,
                  tp_targets_str, int(entries_count), now_str, str(order_id)))
            conn.commit()
            return cursor.rowcount > 0

    def update_order_exit(self, order_id, close_price, realized_pnl, status="CLOSED", exit_reason=None, entries_count=None):
        """Update that EXACT order row when profit is booked (TP hit), stop loss (SL hit), or closed."""
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if entries_count is not None:
                cursor.execute("""
                    UPDATE orders
                    SET status = ?,
                        close_price = ?,
                        realized_pnl = ?,
                        exit_reason = ?,
                        entries_count = ?,
                        updated_at = ?
                    WHERE order_id = ?
                """, (status, float(close_price), float(realized_pnl), exit_reason or status, int(entries_count), now_str, str(order_id)))
            else:
                cursor.execute("""
                    UPDATE orders
                    SET status = ?,
                        close_price = ?,
                        realized_pnl = ?,
                        exit_reason = ?,
                        updated_at = ?
                    WHERE order_id = ?
                """, (status, float(close_price), float(realized_pnl), exit_reason or status, now_str, str(order_id)))
            conn.commit()
            return cursor.rowcount > 0

    def get_orders(self, network=None, bot_id=None, status=None, symbol=None, side=None, limit=100, offset=0):
        """Fetch orders with full multi-criteria filtering."""
        query = "SELECT * FROM orders WHERE 1=1"
        params = []
        if network:
            query += " AND LOWER(network) = ?"
            params.append(network.lower())
        if bot_id is not None:
            query += " AND bot_id = ?"
            params.append(bot_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol.upper())
        if side:
            query += " AND side = ?"
            params.append(side.upper())
            
        query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = []
            for row in cursor.fetchall():
                d = dict(row)
                if d.get("tp_targets"):
                    try:
                        d["tp_targets"] = json.loads(d["tp_targets"])
                    except Exception:
                        pass
                rows.append(d)
            return rows

    def get_order_by_id(self, order_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM orders WHERE order_id = ?", (str(order_id),))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                if d.get("tp_targets"):
                    try:
                        d["tp_targets"] = json.loads(d["tp_targets"])
                    except Exception:
                        pass
                return d
            return None

    # --- BINANCE ORDER API LOGGING ---
    def log_binance_api(self, network, bot_id, symbol, action, method, endpoint, request_payload,
                        response_status, response_payload, is_error=False, error_message=None):
        """Log Binance order API calls in database for inspection and on-page display."""
        net = (network or "mainnet").lower()
        req_str = json.dumps(request_payload) if isinstance(request_payload, (dict, list)) else str(request_payload or "")
        res_str = json.dumps(response_payload) if isinstance(response_payload, (dict, list)) else str(response_payload or "")
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO binance_api_logs (
                    timestamp, network, bot_id, symbol, action, method, endpoint,
                    request_payload, response_status, response_payload, is_error, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                now_str, net, bot_id, (symbol or "").upper(), action, method.upper(), endpoint,
                req_str, int(response_status) if response_status is not None else None,
                res_str, 1 if is_error else 0, str(error_message) if error_message else None
            ))
            conn.commit()

    def get_binance_api_logs(self, network=None, is_error=None, symbol=None, action=None, status=None, search=None, limit=100, offset=0):
        """Query Binance order API logs with filtering."""
        query = "SELECT * FROM binance_api_logs WHERE 1=1"
        params = []
        if network:
            query += " AND LOWER(network) = ?"
            params.append(network.lower())
        if is_error is not None:
            query += " AND is_error = ?"
            params.append(1 if is_error else 0)
        if status:
            st_str = str(status).lower()
            if st_str == "error":
                query += " AND is_error = 1"
            elif st_str == "success":
                query += " AND is_error = 0"
            elif st_str.isdigit():
                query += " AND response_status = ?"
                params.append(int(st_str))
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol.upper())
        if action:
            query += " AND action = ?"
            params.append(action)
        if search:
            query += " AND (endpoint LIKE ? OR request_payload LIKE ? OR response_payload LIKE ? OR error_message LIKE ?)"
            s = f"%{search}%"
            params.extend([s, s, s, s])

        query += " ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    # --- SYMBOLS CONFIG ---
    def get_symbols_config(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT symbol, max_leverage FROM symbols_config ORDER BY symbol")
            return [dict(row) for row in cursor.fetchall()]

    def update_symbol_leverage(self, symbol: str, max_leverage: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO symbols_config (symbol, max_leverage)
                VALUES (?, ?)
                ON CONFLICT(symbol) DO UPDATE SET max_leverage = excluded.max_leverage
            """, (symbol.upper(), int(max_leverage)))
            conn.commit()

    def delete_symbol_config(self, symbol: str):
        sym = symbol.upper()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM bot_instances WHERE symbol = ?", (sym,))
            bot_ids = [row['id'] for row in cursor.fetchall()]
            if bot_ids:
                placeholders = ','.join('?' * len(bot_ids))
                cursor.execute(f"DELETE FROM trades WHERE bot_id IN ({placeholders})", bot_ids)
                cursor.execute(f"DELETE FROM orders WHERE bot_id IN ({placeholders})", bot_ids)
                cursor.execute(f"DELETE FROM system_logs WHERE bot_id IN ({placeholders})", bot_ids)
            cursor.execute("DELETE FROM trades WHERE symbol = ?", (sym,))
            cursor.execute("DELETE FROM orders WHERE symbol = ?", (sym,))
            cursor.execute("DELETE FROM bot_instances WHERE symbol = ?", (sym,))
            cursor.execute("DELETE FROM symbols_config WHERE symbol = ?", (sym,))
            conn.commit()
        return len(bot_ids)

    def add_symbol_config(self, symbol: str, max_leverage: int):
        self.update_symbol_leverage(symbol, max_leverage)

    # --- LOGGING & METRICS ---
    def log_trade(self, symbol, side, order_type, price, qty, realized_pnl=0.0, order_id=None, bot_id=None, network="mainnet"):
        net = (network or "mainnet").lower()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO trades (symbol, side, type, price, qty, realized_pnl, order_id, bot_id, network, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (symbol, side, order_type, float(price), float(qty), float(realized_pnl), order_id, bot_id, net))
            conn.commit()

    def get_trades(self, limit=100, bot_id=None, network=None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM trades WHERE 1=1"
            params = []
            if network:
                query += " AND LOWER(network) = ?"
                params.append(network.lower())
            if bot_id is not None:
                query += " AND bot_id = ?"
                params.append(bot_id)
            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def log_pnl(self, total_balance, daily_pnl=0.0, network="mainnet"):
        net = (network or "mainnet").lower()
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO daily_pnl (date, network, total_balance, daily_pnl)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(date, network) DO UPDATE SET
                    total_balance = excluded.total_balance,
                    daily_pnl = excluded.daily_pnl
            """, (date_str, net, float(total_balance), float(daily_pnl)))
            
            hour_str = datetime.utcnow().strftime("%Y-%m-%d %H:00:00")
            cursor.execute("""
                INSERT INTO hourly_pnl (hour, network, total_balance, hourly_pnl)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(hour, network) DO UPDATE SET
                    total_balance = excluded.total_balance,
                    hourly_pnl = excluded.hourly_pnl
            """, (hour_str, net, float(total_balance), float(daily_pnl)))
            conn.commit()

    def get_pnl_history(self, limit=30, network=None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if network:
                cursor.execute("SELECT * FROM daily_pnl WHERE LOWER(network) = ? ORDER BY date DESC LIMIT ?", (network.lower(), limit))
            else:
                cursor.execute("SELECT * FROM daily_pnl ORDER BY date DESC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()][::-1]

    def get_pnl_history_hourly(self, hours_limit=48, network=None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if network:
                cursor.execute("SELECT * FROM hourly_pnl WHERE LOWER(network) = ? ORDER BY hour DESC LIMIT ?", (network.lower(), hours_limit))
            else:
                cursor.execute("SELECT * FROM hourly_pnl ORDER BY hour DESC LIMIT ?", (hours_limit,))
            return [dict(row) for row in cursor.fetchall()][::-1]

    def log_message(self, level, message, bot_id=None, network="mainnet"):
        net = (network or "mainnet").lower()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO system_logs (level, message, bot_id, network, timestamp)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (level, message, bot_id, net))
            conn.commit()

    def get_logs(self, limit=100, bot_id=None, network=None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM system_logs WHERE 1=1"
            params = []
            if network:
                query += " AND LOWER(network) = ?"
                params.append(network.lower())
            if bot_id is not None:
                query += " AND bot_id = ?"
                params.append(bot_id)
            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_state(self, key, default=None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row:
                try:
                    return json.loads(row['value'])
                except json.JSONDecodeError:
                    return row['value']
            return default

    def set_state(self, key, value):
        val_str = json.dumps(value)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO bot_state (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (key, val_str))
            conn.commit()

    def get_all_active_positions_from_state(self):
        """Returns a dict of bot_id -> list of positions for all bots having saved active positions in one fast query."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM bot_state WHERE key LIKE 'active_position_bot_%'")
            res = {}
            for row in cursor.fetchall():
                key = row["key"]
                bot_id_str = key.replace("active_position_bot_", "")
                if bot_id_str.isdigit():
                    bot_id = int(bot_id_str)
                    try:
                        pos = json.loads(row["value"])
                        if pos:
                            res[bot_id] = pos if isinstance(pos, list) else [pos]
                    except Exception:
                        pass
            return res

    def save_backtest_result(self, symbol: str, strategy_name: str, timeframe: str, parameters: dict,
                             total_trades: int, win_trades: int, loss_trades: int, win_rate: float,
                             released_pnl: float, unreleased_pnl: float, open_trades_count: int,
                             total_pnl: float, profit_factor: float, max_drawdown: float, period: str = "30d",
                             closed_trades: int = 0,
                             total_long_trades: int = 0, total_short_trades: int = 0,
                             closed_long_trades: int = 0, closed_short_trades: int = 0,
                             trade_events: list = None):
        param_str = json.dumps(parameters)
        events_str = json.dumps(trade_events) if trade_events is not None else None
        
        for attempt in range(5):
            try:
                with self.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        DELETE FROM backtest_results 
                        WHERE symbol = ? AND strategy_name = ? AND timeframe = ?
                    """, (symbol.upper(), strategy_name, timeframe))
                    cursor.execute("""
                        INSERT INTO backtest_results (
                            symbol, strategy_name, timeframe, parameters,
                            total_trades, closed_trades, total_long_trades, total_short_trades,
                            closed_long_trades, closed_short_trades,
                            win_trades, loss_trades, win_rate,
                            released_pnl, unreleased_pnl, open_trades_count,
                            total_pnl, profit_factor, max_drawdown, period, trade_events
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (symbol.upper(), strategy_name, timeframe, param_str,
                          total_trades, closed_trades, total_long_trades, total_short_trades,
                          closed_long_trades, closed_short_trades,
                          win_trades, loss_trades, round(win_rate, 2),
                          round(released_pnl, 2), round(unreleased_pnl, 2), open_trades_count,
                          round(total_pnl, 2), round(profit_factor, 2), round(max_drawdown, 2), period, events_str))
                    conn.commit()
                break
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < 4:
                    import time
                    time.sleep(0.1 * (attempt + 1))
                else:
                    raise e

    def get_backtest_results(self, strategy_name=None, symbol=None, timeframe=None, sort_by="pnl_desc",
                             only_profitable=False, min_win_rate=None, limit=1000, offset=0, include_events=False):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cols = "*" if include_events else """
                id, symbol, strategy_name, timeframe, parameters,
                total_trades, closed_trades, total_long_trades, total_short_trades,
                closed_long_trades, closed_short_trades,
                win_trades, loss_trades, win_rate,
                released_pnl, unreleased_pnl, open_trades_count,
                total_pnl, profit_factor, max_drawdown, period, created_at
            """
            query = f"SELECT {cols} FROM backtest_results WHERE 1=1"
            params = []
            if strategy_name and strategy_name != "all":
                query += " AND strategy_name = ?"
                params.append(strategy_name)
            if timeframe and timeframe != "all":
                query += " AND timeframe = ?"
                params.append(timeframe)
            if symbol and symbol.strip():
                query += " AND symbol LIKE ?"
                params.append(f"%{symbol.strip().upper()}%")
            if only_profitable:
                query += " AND total_pnl > 0"
            if min_win_rate is not None and float(min_win_rate) > 0:
                query += " AND win_rate >= ?"
                params.append(float(min_win_rate))

            # Ordering
            order_map = {
                "pnl_desc": "total_pnl DESC",
                "pnl_asc": "total_pnl ASC",
                "released_desc": "released_pnl DESC",
                "released_asc": "released_pnl ASC",
                "unreleased_desc": "unreleased_pnl DESC",
                "unreleased_asc": "unreleased_pnl ASC",
                "winrate_desc": "win_rate DESC",
                "winrate_asc": "win_rate ASC",
                "pf_desc": "profit_factor DESC",
                "trades_desc": "total_trades DESC",
                "trades_asc": "total_trades ASC",
                "closed_desc": "closed_trades DESC",
                "closed_asc": "closed_trades ASC",
                "open_desc": "open_trades_count DESC",
                "open_asc": "open_trades_count ASC",
                "symbol_asc": "symbol ASC",
                "id_desc": "id DESC"
            }
            order_clause = order_map.get(sort_by, "total_pnl DESC")
            query += f" ORDER BY {order_clause} LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cursor.execute(query, params)
            rows = cursor.fetchall()
            results = []
            for r in rows:
                item = dict(r)
                try:
                    item['parameters'] = json.loads(item['parameters'])
                except:
                    pass
                results.append(item)
            return results

    def get_backtest_summary_stats(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_tests,
                    COALESCE(SUM(total_trades), 0) as total_trades,
                    COALESCE(SUM(closed_trades), 0) as total_closed_trades,
                    COALESCE(SUM(open_trades_count), 0) as total_open_trades,
                    COALESCE(SUM(win_trades), 0) as total_wins,
                    COALESCE(SUM(loss_trades), 0) as total_losses,
                    COALESCE(ROUND(SUM(released_pnl), 2), 0.0) as total_released_pnl,
                    COALESCE(ROUND(SUM(unreleased_pnl), 2), 0.0) as total_unreleased_pnl,
                    COALESCE(ROUND(SUM(total_pnl), 2), 0.0) as total_pnl,
                    COALESCE(ROUND(AVG(win_rate), 2), 0.0) as avg_win_rate,
                    COUNT(CASE WHEN total_pnl > 0 THEN 1 END) as profitable_tests
                FROM backtest_results
            """)
            row = cursor.fetchone()
            stats = dict(row) if row else {}

            # Best strategy
            cursor.execute("""
                SELECT strategy_name, ROUND(SUM(total_pnl), 2) as strat_pnl, ROUND(AVG(win_rate), 2) as strat_wr, SUM(total_trades) as strat_trades
                FROM backtest_results
                GROUP BY strategy_name
                ORDER BY strat_pnl DESC
                LIMIT 1
            """)
            best_strat = cursor.fetchone()
            stats["best_strategy"] = dict(best_strat) if best_strat else None

            # Best symbol
            cursor.execute("""
                SELECT symbol, strategy_name, timeframe, total_pnl, released_pnl, unreleased_pnl, win_rate, total_trades, profit_factor
                FROM backtest_results
                ORDER BY total_pnl DESC
                LIMIT 1
            """)
            best_sym = cursor.fetchone()
            stats["best_symbol"] = dict(best_sym) if best_sym else None

            # Breakdown by strategy and timeframe
            cursor.execute("""
                SELECT 
                    strategy_name,
                    timeframe,
                    COUNT(*) as symbol_count,
                    SUM(total_trades) as trades,
                    ROUND(AVG(win_rate), 2) as avg_win_rate,
                    ROUND(SUM(released_pnl), 2) as released_pnl,
                    ROUND(SUM(unreleased_pnl), 2) as unreleased_pnl,
                    ROUND(SUM(total_pnl), 2) as net_pnl,
                    COUNT(CASE WHEN total_pnl > 0 THEN 1 END) as profitable_count
                FROM backtest_results
                GROUP BY strategy_name, timeframe
                ORDER BY net_pnl DESC
            """)
            stats["strategies"] = [dict(r) for r in cursor.fetchall()]

            return stats

    def clear_backtest_results(self, strategy_name=None, timeframe=None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            query = "DELETE FROM backtest_results WHERE 1=1"
            params = []
            if strategy_name:
                query += " AND strategy_name = ?"
                params.append(strategy_name)
            if timeframe:
                query += " AND timeframe = ?"
                params.append(timeframe)
            cursor.execute(query, params)
            conn.commit()

    def get_backtest_result_by_id(self, result_id: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM backtest_results WHERE id = ?", (result_id,))
            row = cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            try:
                item['parameters'] = json.loads(item['parameters'])
            except:
                pass
            try:
                if item.get('trade_events'):
                    item['trade_events'] = json.loads(item['trade_events'])
                else:
                    item['trade_events'] = []
            except:
                item['trade_events'] = []
            return item

    def clear_database_except_symbols(self):
        """Clears all operational runtime tables except symbols_config and admin state."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            tables_to_clear = [
                "backtest_results",
                "bot_instances",
                "positions",
                "orders",
                "trades",
                "binance_api_logs",
                "daily_pnl",
                "hourly_pnl",
                "system_logs"
            ]
            for tbl in tables_to_clear:
                try:
                    cursor.execute(f"DELETE FROM {tbl}")
                except Exception:
                    pass
            conn.commit()

