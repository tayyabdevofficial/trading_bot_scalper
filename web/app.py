import os
import sys
import asyncio
import json
import logging
import websockets
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Any, Optional
from datetime import datetime

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from web.auth import check_admin_auth

from bot_manager import BotManager
from database import Database
from config import Config
from strategy import STRATEGY_MAP
import global_state

logger = logging.getLogger("LiquidationListener")

app = FastAPI(title="Multi-Bot Cryptocurrency Trading Dashboard")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Unified Single Database
db = Database(os.path.join(os.path.dirname(os.path.dirname(__file__)), "trading_bot.db"))
db_live = db
db_testnet = db

# Seed/Init admin user in database states if not existing
def seed_admin_user(db_inst):
    email = db_inst.get_state("admin_email")
    if not email:
        from web.auth import hash_password
        env_email = os.getenv("ADMIN_EMAIL", "admin@tradingbot.com")
        env_pass = os.getenv("ADMIN_PASSWORD", "admin_secure_pass_123")
        db_inst.set_state("admin_email", env_email)
        db_inst.set_state("admin_password_hash", hash_password(env_pass))

seed_admin_user(db)

# Unified Bot Manager Instance
manager = BotManager(db=db)
live_manager = manager

class BotCreateRequest(BaseModel):
    symbol: str
    strategy_name: str
    parameters: Dict[str, Any]
    network: Optional[str] = "mainnet"

class AddBalanceRequest(BaseModel):
    amount: float

@app.on_event("startup")
async def startup_event():
    # Bots do not auto-start on server launch or restart.
    # Users can manually start individual bots or click "Start All" from the dashboard.
    pass

@app.on_event("shutdown")
async def shutdown_event():
    global manager
    if manager:
        await manager.stop_all_bots(close_positions=False)

class LoginRequest(BaseModel):
    email: str
    password: str
    remember_me: bool = False

def inject_widgets(html: str) -> str:
    widgets_dir = os.path.join(os.path.dirname(__file__), "templates")
    import re
    # Inject header if backward placeholder is used
    header_path = os.path.join(widgets_dir, "header.html")
    if "<!-- HEADER_PLACEHOLDER -->" in html and os.path.exists(header_path):
        with open(header_path, "r", encoding="utf-8") as f:
            html = html.replace("<!-- HEADER_PLACEHOLDER -->", f.read())
            
    def replacer(match):
        filename = match.group(1).strip()
        widget_path = os.path.join(widgets_dir, filename)
        if os.path.exists(widget_path):
            with open(widget_path, "r", encoding="utf-8") as f:
                return f.read()
        return f"<!-- Widget {filename} not found -->"
    
    return re.sub(r'<!--\s*WIDGET:\s*([a-zA-Z0-9_\-\.]+)\s*-->', replacer, html)

def serve_page(filename: str, is_admin: bool = False) -> str:
    path = os.path.join(os.path.dirname(__file__), "templates", filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"{filename} template not found")
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    
    # Inject all widgets
    html = inject_widgets(html)
    
    if is_admin:
        html = html.replace('const IS_ADMIN = false;', 'const IS_ADMIN = true;')
    return html



@app.get("/login", response_class=HTMLResponse)
async def get_login_page(request: Request):
    # Check if already authenticated
    session_id = request.cookies.get("session_id")
    from web.auth import ACTIVE_SESSIONS
    if session_id and session_id in ACTIVE_SESSIONS:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/")
    return serve_page("login.html", is_admin=False)

@app.post("/api/login")
async def api_login(req: LoginRequest):
    stored_email = db_live.get_state("admin_email", "admin@tradingbot.com")
    stored_hash = db_live.get_state("admin_password_hash")
    if not stored_hash:
        from web.auth import hash_password
        stored_hash = hash_password(os.getenv("ADMIN_PASSWORD", "admin_secure_pass_123"))
        db_live.set_state("admin_password_hash", stored_hash)
    from web.auth import verify_password, create_session
    if req.email == stored_email and verify_password(stored_hash, req.password):
        session_id = create_session(req.email, db=db_live, persistent=req.remember_me)
        from fastapi.responses import JSONResponse
        response = JSONResponse(content={"status": "success"})
        if req.remember_me:
            response.set_cookie(key="session_id", value=session_id, httponly=True,
                                max_age=31536000, samesite="lax")  # 1 year
        else:
            response.set_cookie(key="session_id", value=session_id, httponly=True,
                                samesite="lax")  # session cookie
        return response
    else:
        raise HTTPException(status_code=401, detail="Invalid email or password")

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

@app.post("/api/change-password")
async def change_admin_password(req: ChangePasswordRequest, request: Request):
    await check_admin_auth(request, db=db_live)
    stored_hash = db_live.get_state("admin_password_hash")
    from web.auth import verify_password, hash_password
    if not verify_password(stored_hash, req.current_password):
        raise HTTPException(status_code=400, detail="Invalid current password")
    new_hash = hash_password(req.new_password)
    db_live.set_state("admin_password_hash", new_hash)
    db_live.log_message("INFO", "Admin password successfully updated.")
    return {"status": "success"}

@app.post("/api/logout")
async def api_logout(request: Request):
    session_id = request.cookies.get("session_id")
    if session_id:
        from web.auth import destroy_session
        destroy_session(session_id, db=db_live)
    from fastapi.responses import JSONResponse
    response = JSONResponse(content={"status": "success"})
    response.delete_cookie(key="session_id")
    return response

# ── APPLICATION PAGE ROUTES ──────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def get_index(request: Request):
    try:
        await check_admin_auth(request, db=db)
    except HTTPException:
        return RedirectResponse(url="/login")
    return serve_page("index.html", is_admin=True)

@app.get("/bots", response_class=HTMLResponse)
async def get_bots_page(request: Request):
    try:
        await check_admin_auth(request, db=db)
    except HTTPException:
        return RedirectResponse(url="/login")
    return serve_page("bots.html", is_admin=True)

@app.get("/positions", response_class=HTMLResponse)
async def get_positions_page(request: Request):
    try:
        await check_admin_auth(request, db=db)
    except HTTPException:
        return RedirectResponse(url="/login")
    return serve_page("positions.html", is_admin=True)

@app.get("/closed-positions", response_class=HTMLResponse)
async def get_closed_positions_page(request: Request):
    try:
        await check_admin_auth(request, db=db)
    except HTTPException:
        return RedirectResponse(url="/login")
    return serve_page("closed_positions.html", is_admin=True)

@app.get("/binance-logs", response_class=HTMLResponse)
async def get_binance_logs_page(request: Request):
    try:
        await check_admin_auth(request, db=db)
    except HTTPException:
        return RedirectResponse(url="/login")
    return serve_page("binance_logs.html", is_admin=True)

@app.get("/scanner", response_class=HTMLResponse)
async def get_scanner_page(request: Request):
    try:
        await check_admin_auth(request, db=db)
    except HTTPException:
        return RedirectResponse(url="/login")
    return serve_page("scanner.html", is_admin=True)

@app.get("/backtest", response_class=HTMLResponse)
async def get_backtest_page(request: Request):
    try:
        await check_admin_auth(request, db=db)
    except HTTPException:
        return RedirectResponse(url="/login")
    return serve_page("backtest.html", is_admin=True)

@app.get("/bot-info", response_class=HTMLResponse)
async def get_bot_info_page(request: Request, bot_id: Optional[int] = None):
    try:
        await check_admin_auth(request, db=db)
    except HTTPException:
        return RedirectResponse(url="/login")
    return serve_page("bot_info.html", is_admin=True)

@app.get("/backtest/detail", response_class=HTMLResponse)
@app.get("/backtest-detail", response_class=HTMLResponse)
async def get_backtest_detail_page(request: Request, id: Optional[int] = None):
    try:
        await check_admin_auth(request, db=db)
    except HTTPException:
        return RedirectResponse(url="/login")
    return serve_page("backtest_detail.html", is_admin=True)

def _enrich_bots_list(target_bots, target_mgr, target_db, default_network="mainnet"):
    enriched_bots = []
    shared_balance = float(target_db.get_state("virtual_balance", 500.0))
    total_margin = 0.0
    
    if target_mgr and target_mgr.active_bots:
        for b_id, act_b in target_mgr.active_bots.items():
            pos_val = getattr(act_b.execution, "active_position", None)
            if pos_val:
                positions = pos_val if isinstance(pos_val, list) else [pos_val]
                leverage = int(getattr(act_b, "parameters", {}).get("leverage", 20))
                for pos in positions:
                    position_value = pos["entry_price"] * pos["qty"]
                    total_margin += (position_value / leverage)
            
    available_balance = max(0.0, shared_balance - total_margin)
    
    for bot in target_bots:
        bot_id = bot["id"]
        is_running = target_mgr and bot_id in target_mgr.active_bots
        
        params = bot["parameters"].copy()
        if "trade_amount_usd" not in params and "trade_amount" in params:
            params["trade_amount_usd"] = params["trade_amount"]
        
        position = []
        latest_price = 0.0
        
        if is_running:
            active_bot = target_mgr.active_bots[bot_id]
            raw_pos = active_bot.execution.active_position
            if raw_pos:
                position = raw_pos if isinstance(raw_pos, list) else [raw_pos]
            if active_bot.data_engine.klines:
                latest_price = active_bot.data_engine.klines[-1]["close"]
        else:
            saved_pos = target_db.get_state(f"active_position_bot_{bot_id}")
            if saved_pos:
                position = saved_pos if isinstance(saved_pos, list) else [saved_pos]
                
        for p in position:
            if "timestamp" in p and p["timestamp"]:
                p["timestamp"] = format_utc_timestamp(p["timestamp"])
                
        net = bot.get("network") or default_network
        enriched_bots.append({
            "id": bot_id,
            "network": net,
            "symbol": bot["symbol"],
            "strategy_name": bot["strategy_name"],
            "parameters": params,
            "is_running": is_running,
            "position": position,
            "latest_price": latest_price,
            "shared_balance": shared_balance,
            "available_balance": available_balance
        })
        
    return enriched_bots

@app.get("/api/bots")
async def get_bots(
    network: Optional[str] = None,
    strategy: Optional[str] = None,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: Optional[str] = "id_asc",
    page: Optional[int] = None,
    page_size: int = 20,
    all_bots: bool = False
):
    net = None if network in ("all", None) else network.lower()
    is_run = None
    if status == "running":
        is_run = True
    elif status == "stopped":
        is_run = False

    if page is not None and not all_bots:
        p = max(1, page)
        offset = (p - 1) * page_size
        bots_raw, total = db.get_bots_paginated(
            network=net,
            strategy=strategy,
            symbol=symbol,
            timeframe=timeframe,
            is_running=is_run,
            search=search,
            sort_by=sort_by or "id_asc",
            limit=page_size,
            offset=offset
        )
        enriched = _enrich_bots_list(bots_raw, manager, db, default_network="mainnet")
        total_pages = max(1, (total + page_size - 1) // page_size) if total > 0 else 1
        return {
            "items": enriched,
            "total": total,
            "page": p,
            "page_size": page_size,
            "total_pages": total_pages
        }

    # Backward compatibility fallback
    bots = db.get_bots(network=net)
    return _enrich_bots_list(bots, manager, db, default_network="mainnet")

@app.get("/api/bots/performance")
async def get_bots_performance_api(
    page: int = 1,
    page_size: int = 20,
    network: Optional[str] = None,
    strategy: Optional[str] = None,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = "pnl_desc"
):
    net = None if network in ("all", None) else network.lower()
    is_run = None
    if status == "running":
        is_run = True
    elif status == "stopped":
        is_run = False

    p = max(1, page)
    offset = (p - 1) * page_size
    bots, total = db.get_bot_performance_paginated(
        network=net,
        strategy=strategy,
        symbol=symbol,
        timeframe=timeframe,
        is_running=is_run,
        search=search,
        sort_by=sort_by,
        limit=page_size,
        offset=offset
    )
    total_pages = max(1, (total + page_size - 1) // page_size) if total > 0 else 1
    return {
        "items": bots,
        "total": total,
        "page": p,
        "page_size": page_size,
        "total_pages": total_pages
    }

@app.get("/api/positions")
async def get_positions_api(
    page: Optional[int] = None,
    page_size: int = 20,
    network: Optional[str] = None,
    side: Optional[str] = None,
    search: Optional[str] = None,
    all_items: bool = False
):
    net = (network or "all").lower()
    all_open_positions = []
    
    # 1. Collect positions from active running bots in manager
    if manager and manager.active_bots:
        for bot_id, active_bot in manager.active_bots.items():
            b_net = getattr(active_bot, "network", "mainnet").lower()
            if net not in ("all", None) and b_net != net:
                continue
            pos = getattr(active_bot.execution, "active_position", None)
            if pos:
                positions = pos if isinstance(pos, list) else [pos]
                latest_price = 0.0
                if active_bot.data_engine.klines:
                    latest_price = active_bot.data_engine.klines[-1]["close"]
                bot_info = {
                    "id": bot_id,
                    "symbol": active_bot.symbol,
                    "strategy_name": active_bot.strategy_name,
                    "network": b_net,
                    "parameters": getattr(active_bot, "parameters", {}),
                    "latest_price": latest_price,
                    "is_running": True
                }
                for p in positions:
                    p["entries_count"] = int(p.get("entries_count") or 1)
                    p["dca_count"] = max(0, p["entries_count"] - 1)
                    all_open_positions.append({"bot": bot_info, "pos": p})

    # 2. Also check DB active position states for bots not currently in memory
    saved_positions_map = db.get_all_active_positions_from_state()
    missing_bot_ids = [bid for bid in saved_positions_map if not (manager and bid in manager.active_bots)]
    if missing_bot_ids:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            placeholders = ",".join("?" for _ in missing_bot_ids)
            cursor.execute(f"SELECT * FROM bot_instances WHERE id IN ({placeholders})", missing_bot_ids)
            for row in cursor.fetchall():
                b = dict(row)
                b_net = b.get("network", "mainnet").lower()
                if net not in ("all", None) and b_net != net:
                    continue
                try:
                    params = json.loads(b["parameters"])
                except Exception:
                    params = {}
                bot_info = {
                    "id": b["id"],
                    "symbol": b["symbol"],
                    "strategy_name": b["strategy_name"],
                    "network": b.get("network", "mainnet"),
                    "parameters": params,
                    "latest_price": 0.0,
                    "is_running": False
                }
                positions = saved_positions_map.get(b["id"], [])
                for p in positions:
                    p["entries_count"] = int(p.get("entries_count") or 1)
                    p["dca_count"] = max(0, p["entries_count"] - 1)
                    all_open_positions.append({"bot": bot_info, "pos": p})

    # Filter side & search
    filtered = []
    for item in all_open_positions:
        b = item["bot"]
        p = item["pos"]
        if side and side != "all" and (p.get("side") or "").upper() != side.upper():
            continue
        if search and search.strip():
            q = search.strip().lower()
            sym_match = q in (b.get("symbol") or "").lower()
            id_match = q in str(b.get("id"))
            strat_match = q in (b.get("strategy_name") or "").lower()
            if not (sym_match or id_match or strat_match):
                continue
        filtered.append(item)

    # Sort by timestamp desc
    filtered.sort(key=lambda x: str(x["pos"].get("timestamp") or ""), reverse=True)

    if all_items or page is None:
        return filtered

    total = len(filtered)
    total_pages = max(1, (total + page_size - 1) // page_size) if total > 0 else 1
    p = min(max(1, page), total_pages)
    start_idx = (p - 1) * page_size
    end_idx = start_idx + page_size
    page_items = filtered[start_idx:end_idx]

    return {
        "items": page_items,
        "total": total,
        "page": p,
        "page_size": page_size,
        "total_pages": total_pages
    }

@app.post("/api/bots")
async def create_bot(req: BotCreateRequest):
    network = (req.network or "mainnet").lower()
    if not manager:
        raise HTTPException(status_code=500, detail="Bot manager not initialized")
        
    if req.strategy_name not in STRATEGY_MAP:
        raise HTTPException(status_code=400, detail=f"Strategy {req.strategy_name} not available")
        
    # Prevent duplicate bot configurations in chosen network
    existing_bots = db.get_bots(network=network)
    target_interval = req.parameters.get("candle_interval", "5m")
    for bot in existing_bots:
        if (bot["symbol"].upper() == req.symbol.upper() and 
            bot["strategy_name"] == req.strategy_name and 
            bot["parameters"].get("candle_interval", "5m") == target_interval):
            raise HTTPException(
                status_code=400, 
                detail=f"A bot with symbol '{req.symbol.upper()}', strategy '{req.strategy_name}', and timeframe '{target_interval}' already exists on {network.upper()}."
            )
        
    bot_id = await manager.add_bot(
        symbol=req.symbol,
        strategy_name=req.strategy_name,
        parameters_dict=req.parameters,
        network=network
    )
    return {"status": "success", "bot_id": bot_id, "network": network}

class DeployBacktestRequest(BaseModel):
    network: str = "testnet"
    top_n: Optional[int] = None
    from_backtest_only: bool = True

@app.post("/api/bots/deploy-backtest")
async def deploy_backtest_bots_api(req: DeployBacktestRequest, request: Request = None):
    from scripts.deploy_bots import deploy_bots
    net = (req.network or "testnet").lower()
    created, updated = deploy_bots(
        network=net,
        from_backtest_only=req.from_backtest_only,
        top_n=req.top_n
    )
    return {
        "status": "success",
        "message": f"Successfully deployed backtest configurations to {net.upper()}: {created} created, {updated} updated.",
        "created": created,
        "updated": updated,
        "network": net
    }

@app.delete("/api/bots/{bot_id}")
async def delete_bot(bot_id: int):
    if not manager:
        raise HTTPException(status_code=500, detail="Bot manager not initialized")
    bot = db.get_bot(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    success = await manager.delete_bot(bot_id)
    if success:
        return {"status": "success"}
    raise HTTPException(status_code=500, detail="Failed to delete bot")

@app.post("/api/bots/{bot_id}/toggle")
async def toggle_bot(bot_id: int):
    if not manager:
        raise HTTPException(status_code=500, detail="Bot manager not initialized")
    bot = db.get_bot(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
        
    is_running = bot_id in manager.active_bots
    if is_running:
        success = await manager.stop_bot(bot_id)
        return {"status": "stopped" if success else "failed"}
    else:
        # If mainnet, test Binance API credentials
        if bot.get("network", "mainnet").lower() == "mainnet":
            from execution_handler import ExecutionHandler
            eh = ExecutionHandler(db=db, bot_id=bot_id, simulation_mode=False, use_testnet=False, network="mainnet")
            api_ok = await eh.test_binance_api()
            if not api_ok:
                raise HTTPException(
                    status_code=400,
                    detail="Binance API is not integrated or credentials are invalid. Please configure your live BINANCE_API_KEY and BINANCE_API_SECRET in the .env file to start this Mainnet bot."
                )
        success = await manager.start_bot(bot_id)
        return {"status": "running" if success else "failed"}

@app.post("/api/bots/start-all")
async def start_all_bots(network: Optional[str] = "all"):
    net = (network or "all").lower()
    started_mainnet = 0
    started_testnet = 0
    errors = []

    target_network = None if net == "all" else net
    candidate_bots = db.get_bots(network=target_network)

    # Check Binance credentials if any unstarted mainnet bot is in scope
    has_unstarted_mainnet = any(b["id"] not in manager.active_bots and b.get("network", "mainnet").lower() == "mainnet" for b in candidate_bots)
    if has_unstarted_mainnet:
        from execution_handler import ExecutionHandler
        eh = ExecutionHandler(db=db, bot_id=None, simulation_mode=False, use_testnet=False, network="mainnet")
        api_ok = await eh.test_binance_api()
        if not api_ok:
            errors.append("Live Binance API credentials invalid or not configured in .env.")

    for bot in candidate_bots:
        b_id = bot["id"]
        b_net = bot.get("network", "mainnet").lower()
        if b_id in manager.active_bots:
            continue
            
        if b_net == "mainnet":
            if errors:
                continue
            success = await manager.start_bot(b_id)
            if success:
                started_mainnet += 1
        elif b_net == "testnet":
            # Pure local database simulation
            success = await manager.start_bot(b_id)
            if success:
                started_testnet += 1

    total = started_mainnet + started_testnet
    if errors and total == 0:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    parts = []
    if net in ("mainnet", "all"):
        parts.append(f"{started_mainnet} Mainnet")
    if net in ("testnet", "all"):
        parts.append(f"{started_testnet} Testnet")
    msg = f"Successfully activated {total} bots ({', '.join(parts)})"
    if errors:
        msg += f" (Note: {'; '.join(errors)})"
    return {"status": "success", "message": msg, "started_count": total, "started_mainnet": started_mainnet, "started_testnet": started_testnet}

@app.post("/api/bots/stop-all")
async def stop_all_bots(network: Optional[str] = "all"):
    net = (network or "all").lower()
    stopped_mainnet = 0
    stopped_testnet = 0

    active_ids = list(manager.active_bots.keys())
    for bot_id in active_ids:
        active_bot = manager.active_bots.get(bot_id)
        bot_net = getattr(active_bot, "network", "mainnet").lower() if active_bot else "mainnet"
        
        if net != "all" and bot_net != net:
            continue
            
        success = await manager.stop_bot(bot_id, close_positions=True)
        if success:
            if bot_net == "mainnet":
                stopped_mainnet += 1
            else:
                stopped_testnet += 1

    total = stopped_mainnet + stopped_testnet
    parts = []
    if net in ("mainnet", "all"):
        parts.append(f"{stopped_mainnet} Mainnet")
    if net in ("testnet", "all"):
        parts.append(f"{stopped_testnet} Testnet")
    msg = f"Successfully deactivated {total} bots ({', '.join(parts)})"
    return {"status": "success", "message": msg, "stopped_count": total, "stopped_mainnet": stopped_mainnet, "stopped_testnet": stopped_testnet}

@app.get("/api/strategies")
async def get_strategies():
    return list(STRATEGY_MAP.keys())

@app.get("/api/balance")
async def get_wallet_balance():
    shared_balance = float(db.get_state("virtual_balance", 500.0))
    total_margin = 0.0
    active_bot_ids = set(manager.active_bots.keys()) if manager and manager.active_bots else set()
    
    if manager and manager.active_bots:
        for b_id, act_b in manager.active_bots.items():
            pos_val = getattr(act_b.execution, "active_position", None)
            if pos_val:
                positions = pos_val if isinstance(pos_val, list) else [pos_val]
                leverage = int(getattr(act_b, "parameters", {}).get("leverage", 20))
                for pos in positions:
                    position_value = float(pos.get("entry_price") or 0.0) * float(pos.get("qty") or 0.0)
                    total_margin += (position_value / leverage)

    saved_positions_map = db.get_all_active_positions_from_state()
    all_bots = db.get_bots()
    for bid, pos_list in saved_positions_map.items():
        if bid not in active_bot_ids:
            bot_meta = next((b for b in all_bots if b["id"] == bid), None)
            leverage = int(bot_meta["parameters"].get("leverage", 20)) if bot_meta and "parameters" in bot_meta else 20
            for pos in pos_list:
                position_value = float(pos.get("entry_price") or 0.0) * float(pos.get("qty") or 0.0)
                total_margin += (position_value / leverage)

    avail = max(0.0, shared_balance - total_margin)
    return {
        "total_balance": shared_balance,
        "available_balance": avail,
        "used_margin": total_margin
    }

@app.get("/api/overview-stats")
async def get_overview_stats():
    # 1. Bots breakdown
    all_bots = db.get_bots()
    total_bots = len(all_bots)
    mainnet_total = sum(1 for b in all_bots if (b.get("network") or "mainnet").lower() == "mainnet")
    testnet_total = sum(1 for b in all_bots if (b.get("network") or "").lower() == "testnet")
    
    active_bot_ids = set(manager.active_bots.keys()) if manager and manager.active_bots else set()
    active_bots_count = len(active_bot_ids)
    mainnet_running = 0
    testnet_running = 0
    if manager and manager.active_bots:
        for bid, ab in manager.active_bots.items():
            bnet = getattr(ab, "network", "mainnet").lower()
            if bnet == "mainnet":
                mainnet_running += 1
            else:
                testnet_running += 1

    # 2. Balance & Margin breakdown
    shared_balance = float(db.get_state("virtual_balance", 500.0))
    total_margin = 0.0

    # 3. Open Positions & Unrealized PnL breakdown
    mainnet_open_count = 0
    testnet_open_count = 0
    mainnet_unrealized_pnl = 0.0
    testnet_unrealized_pnl = 0.0

    # From in-memory active bots
    if manager and manager.active_bots:
        for bot_id, act_b in manager.active_bots.items():
            bnet = getattr(act_b, "network", "mainnet").lower()
            pos_val = getattr(act_b.execution, "active_position", None)
            if pos_val:
                positions = pos_val if isinstance(pos_val, list) else [pos_val]
                leverage = int(getattr(act_b, "parameters", {}).get("leverage", 20))
                latest_price = act_b.data_engine.klines[-1]["close"] if act_b.data_engine.klines else 0.0
                for pos in positions:
                    if bnet == "mainnet":
                        mainnet_open_count += 1
                    else:
                        testnet_open_count += 1
                    
                    pos_entry = float(pos.get("entry_price") or 0.0)
                    pos_qty = float(pos.get("qty") or 0.0)
                    if pos_entry > 0 and pos_qty > 0:
                        position_value = pos_entry * pos_qty
                        total_margin += (position_value / leverage)
                        if latest_price > 0:
                            side = (pos.get("side") or "").upper()
                            pos_pnl = (latest_price - pos_entry) * pos_qty if side == "BUY" else (pos_entry - latest_price) * pos_qty
                            if bnet == "mainnet":
                                mainnet_unrealized_pnl += pos_pnl
                            else:
                                testnet_unrealized_pnl += pos_pnl

    # From DB state for stopped bots
    saved_positions_map = db.get_all_active_positions_from_state()
    for bid, pos_list in saved_positions_map.items():
        if bid not in active_bot_ids:
            bot_meta = next((b for b in all_bots if b["id"] == bid), None)
            bnet = (bot_meta.get("network") or "mainnet").lower() if bot_meta else "mainnet"
            leverage = int(bot_meta["parameters"].get("leverage", 20)) if bot_meta and "parameters" in bot_meta else 20
            for pos in pos_list:
                if bnet == "mainnet":
                    mainnet_open_count += 1
                else:
                    testnet_open_count += 1
                pos_entry = float(pos.get("entry_price") or 0.0)
                pos_qty = float(pos.get("qty") or 0.0)
                if pos_entry > 0 and pos_qty > 0:
                    position_value = pos_entry * pos_qty
                    total_margin += (position_value / leverage)

    available_balance = max(0.0, shared_balance - total_margin)
    total_open_positions = mainnet_open_count + testnet_open_count
    total_unrealized_pnl = mainnet_unrealized_pnl + testnet_unrealized_pnl

    # 4. Closed Positions & Realized PnL breakdown
    raw_closed = _get_closed_positions_unified()
    total_closed = len(raw_closed)
    mainnet_closed_count = 0
    testnet_closed_count = 0
    mainnet_realized_pnl = 0.0
    testnet_realized_pnl = 0.0
    profit_count = 0
    profit_sum = 0.0
    loss_count = 0
    loss_sum = 0.0
    total_realized_pnl = 0.0

    for cp in raw_closed:
        c_net = (cp.get("network") or "mainnet").lower()
        rpnl = float(cp.get("realized_pnl") or 0.0)
        total_realized_pnl += rpnl
        if c_net == "mainnet":
            mainnet_closed_count += 1
            mainnet_realized_pnl += rpnl
        else:
            testnet_closed_count += 1
            testnet_realized_pnl += rpnl

        if rpnl > 0:
            profit_count += 1
            profit_sum += rpnl
        elif rpnl < 0:
            loss_count += 1
            loss_sum += abs(rpnl)

    return {
        "balance": {
            "total_balance": round(shared_balance, 2),
            "available_balance": round(available_balance, 2),
            "used_margin": round(total_margin, 2)
        },
        "bots": {
            "total": total_bots,
            "active": active_bots_count,
            "mainnet_total": mainnet_total,
            "mainnet_running": mainnet_running,
            "testnet_total": testnet_total,
            "testnet_running": testnet_running
        },
        "open_positions": {
            "total": total_open_positions,
            "mainnet": mainnet_open_count,
            "testnet": testnet_open_count,
            "total_unrealized_pnl": round(total_unrealized_pnl, 2),
            "mainnet_unrealized_pnl": round(mainnet_unrealized_pnl, 2),
            "testnet_unrealized_pnl": round(testnet_unrealized_pnl, 2)
        },
        "closed_positions": {
            "total": total_closed,
            "mainnet": mainnet_closed_count,
            "testnet": testnet_closed_count,
            "net_realized_pnl": round(total_realized_pnl, 2),
            "mainnet_realized_pnl": round(mainnet_realized_pnl, 2),
            "testnet_realized_pnl": round(testnet_realized_pnl, 2),
            "profit_count": profit_count,
            "profit_sum": round(profit_sum, 2),
            "loss_count": loss_count,
            "loss_sum": round(loss_sum, 2)
        }
    }

@app.post("/api/balance/add")
async def add_balance(req: AddBalanceRequest):
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    
    current_balance = float(db.get_state("virtual_balance", 500.0))
    new_balance = current_balance + req.amount
    db.set_state("virtual_balance", new_balance)
    
    db.log_message("INFO", f"Added ${req.amount:.2f} to virtual wallet. New balance: ${new_balance:.2f}")
    
    return {"status": "success", "new_balance": new_balance}

@app.get("/api/pnl")
async def get_pnl(period: str = "30d", network: Optional[str] = None):
    if period == "24h":
        data = db.get_pnl_history_hourly(hours_limit=24, network=network)
        return [{"date": d["hour"], "total_balance": d["total_balance"]} for d in data]
    elif period == "48h":
        data = db.get_pnl_history_hourly(hours_limit=48, network=network)
        return [{"date": d["hour"], "total_balance": d["total_balance"]} for d in data]
    else:
        return db.get_pnl_history(limit=30, network=network)

@app.get("/api/logs")
async def get_logs(bot_id: Optional[int] = None, network: Optional[str] = None):
    return db.get_logs(limit=40, bot_id=bot_id, network=network)

def format_utc_timestamp(ts):
    if not ts:
        return ts
    if isinstance(ts, str):
        if not ts.endswith("Z") and "+" not in ts and "-" not in ts[10:]:
            return ts + "Z"
    return ts

def group_trades_into_positions(raw_trades):
    import json
    active_cycles = {}
    closed_cycles = []
    
    def extract_pos_id(trade):
        oid = trade.get("order_id") or ""
        if oid.startswith("POS_"):
            parts = oid.split("_")
            for tag in ("ENTRY", "EXIT", "PARTIAL"):
                if tag in parts:
                    idx = parts.index(tag)
                    return "_".join(parts[1:idx])
        return None

    for t in raw_trades:
        bot_id = t["bot_id"]
        symbol = t["symbol"]
        trade_type = t["type"]
        pos_id = extract_pos_id(t)

        if pos_id:
            key = (bot_id, symbol, pos_id)
        else:
            if trade_type in ("MARKET_ENTRY", "MARKET_DCA"):
                key = (bot_id, symbol, f"legacy_{t.get('id', t['timestamp'])}")
            else:
                legacy_keys = [k for k in active_cycles.keys() if k[0] == bot_id and k[1] == symbol and str(k[2]).startswith("legacy_")]
                key = legacy_keys[-1] if legacy_keys else (bot_id, symbol, f"legacy_orphan_{t.get('id', t['timestamp'])}")

        # Check if it's an entry
        if trade_type in ("MARKET_ENTRY", "MARKET_DCA"):
            if key not in active_cycles:
                active_cycles[key] = {
                    "bot_id": bot_id,
                    "symbol": symbol,
                    "strategy_name": t.get("strategy_name"),
                    "bot_params": t.get("bot_params"),
                    "entry_trades": [],
                    "exit_trades": [],
                    "open_timestamp": t["timestamp"]
                }
            active_cycles[key]["entry_trades"].append(t)
            
        else:
            # It's an exit or partial exit
            if key not in active_cycles:
                # Orphan exit trade
                closed_cycles.append({
                    "order_id": t.get("order_id") or f"TRADE_{t.get('id', '')}",
                    "bot_id": bot_id,
                    "network": t.get("network", "mainnet"),
                    "symbol": symbol,
                    "strategy_name": t.get("strategy_name"),
                    "open_timestamp": format_utc_timestamp(t.get("open_timestamp") or t["timestamp"]),
                    "timestamp": format_utc_timestamp(t["timestamp"]),
                    "created_at": format_utc_timestamp(t.get("open_timestamp") or t["timestamp"]),
                    "updated_at": format_utc_timestamp(t["timestamp"]),
                    "qty": t["qty"],
                    "price": t["price"],
                    "open_price": t["price"],
                    "realized_pnl": t["realized_pnl"],
                    "type": t["type"],
                    "status": t["type"],
                    "side": "SHORT" if t["side"] == "BUY" else "LONG",
                    "tps_hit": 1 if "PARTIAL_TP" in trade_type else 0,
                    "tps_total": 1,
                    "tp_targets": [],
                    "leverage": 20
                })
                continue
                
            cycle = active_cycles[key]
            cycle["exit_trades"].append(t)
            
            is_partial = False
            if trade_type.startswith("PARTIAL_TP_"):
                try:
                    pct_val = int(trade_type.replace("PARTIAL_TP_", "").replace("%", ""))
                    if pct_val < 100:
                        is_partial = True
                except:
                    pass
            
            is_final_exit = not is_partial
            
            if is_final_exit:
                entry_trades = cycle["entry_trades"]
                exit_trades = cycle["exit_trades"]
                
                total_entry_qty = sum(et["qty"] for et in entry_trades)
                if total_entry_qty == 0:
                    total_entry_qty = sum(xt["qty"] for xt in exit_trades)
                    
                avg_entry_price = sum(et["price"] * et["qty"] for et in entry_trades) / sum(et["qty"] for et in entry_trades) if sum(et["qty"] for et in entry_trades) > 0 else entry_trades[0]["price"]
                
                total_exit_qty = sum(xt["qty"] for xt in exit_trades)
                avg_exit_price = sum(xt["price"] * xt["qty"] for xt in exit_trades) / sum(xt["qty"] for xt in exit_trades) if sum(xt["qty"] for xt in exit_trades) > 0 else t["price"]
                
                total_pnl = sum(xt["realized_pnl"] for xt in exit_trades)
                
                take_profit_pct = 2.0
                leverage = 20
                if cycle["bot_params"]:
                    try:
                        params = json.loads(cycle["bot_params"])
                        take_profit_pct = float(params.get("take_profit_pct", 2.0))
                        leverage = int(params.get("leverage", 20))
                    except:
                        pass
                
                tps_hit = sum(1 for xt in exit_trades if xt["type"].startswith("PARTIAL_TP_"))
                tps_total = max(1, int(take_profit_pct))
                
                entry_side = entry_trades[0]["side"]
                
                tp_targets = []
                num_tps = int(take_profit_pct)
                # Dynamically calculate price precision from the entry price
                price_str = f"{avg_entry_price:.8f}".rstrip("0")
                price_decimals = len(price_str.split(".")[-1]) if "." in price_str else 2
                price_precision = max(2, price_decimals)
                
                qty_precision = 4
                if symbol.upper().endswith("USDT"):
                    qty_precision = 3
                
                if num_tps <= 1:
                    tp_price = avg_entry_price * (1 + take_profit_pct/100) if entry_side == "BUY" else avg_entry_price * (1 - take_profit_pct/100)
                    tp_targets = [{
                        "target_price": round(tp_price, price_precision),
                        "qty": round(total_entry_qty, qty_precision),
                        "hit": any(xt["type"].startswith("PARTIAL_TP_") for xt in exit_trades),
                        "pct": 100
                    }]
                else:
                    step = take_profit_pct / num_tps
                    remaining_qty = total_entry_qty
                    for i in range(1, num_tps + 1):
                        tp_pct = step * i
                        tp_price = avg_entry_price * (1 + tp_pct/100) if entry_side == "BUY" else avg_entry_price * (1 - tp_pct/100)
                        
                        if i == num_tps:
                            tp_qty = remaining_qty
                        else:
                            tp_qty = round(total_entry_qty / num_tps, qty_precision)
                            remaining_qty -= tp_qty
                            
                        pct_closed = round((i / num_tps) * 100)
                        
                        was_hit = False
                        for xt in exit_trades:
                            if xt["type"] == f"PARTIAL_TP_{pct_closed}%":
                                was_hit = True
                                break
                        
                        tp_targets.append({
                            "target_price": round(tp_price, price_precision),
                            "qty": round(tp_qty, qty_precision),
                            "hit": was_hit,
                            "pct": pct_closed
                        })
                
                entries_cnt = max(1, len(entry_trades))
                closed_cycles.append({
                    "order_id": t.get("order_id") or f"CYCLE_{cycle['open_timestamp']}",
                    "bot_id": bot_id,
                    "network": t.get("network") or (cycle["entry_trades"][0].get("network") if cycle.get("entry_trades") else "mainnet") or "mainnet",
                    "symbol": symbol,
                    "strategy_name": cycle.get("strategy_name"),
                    "open_timestamp": format_utc_timestamp(cycle["open_timestamp"]),
                    "timestamp": format_utc_timestamp(t["timestamp"]),
                    "created_at": format_utc_timestamp(cycle["open_timestamp"]),
                    "updated_at": format_utc_timestamp(t["timestamp"]),
                    "qty": total_entry_qty,
                    "price": avg_exit_price,
                    "open_price": avg_entry_price,
                    "realized_pnl": total_pnl,
                    "type": t["type"],
                    "status": t["type"],
                    "side": "LONG" if entry_side == "BUY" else "SHORT",
                    "tps_hit": tps_hit,
                    "tps_total": tps_total,
                    "tp_targets": tp_targets,
                    "leverage": leverage,
                    "entries_count": entries_cnt,
                    "dca_count": max(0, entries_cnt - 1)
                })
                
                del active_cycles[key]
                
    closed_cycles.sort(key=lambda x: x["timestamp"], reverse=True)
    return closed_cycles

def _get_closed_positions_unified(bot_id=None, network_filter=None):
    import json
    all_positions = []
    seen_order_ids = set()
    seen_cycles = set()
    
    net = None if network_filter in (None, "all") else network_filter.lower()
    orders = db.get_orders(bot_id=bot_id, network=net, limit=500)
    for ord_row in orders:
        if ord_row["status"] in ("TP_HIT", "SL_HIT", "CLOSED", "OPPOSITE_SIGNAL_CLOSED", "CANCELLED"):
            oid = str(ord_row["order_id"] or "")
            seen_order_ids.add(oid)
            seen_order_ids.add(f"POS_{oid}")
            seen_order_ids.add(f"POS_{oid}_ENTRY")
            
            cycle_key = f"{ord_row.get('bot_id')}_{ord_row.get('symbol')}_{ord_row.get('created_at')}"
            seen_cycles.add(cycle_key)
            
            tp_targets = []
            if ord_row.get("tp_targets"):
                try:
                    tp_targets = json.loads(ord_row["tp_targets"])
                except:
                    pass
                    
            ord_net = (ord_row.get("network") or ("testnet" if "SIM" in oid.upper() else "mainnet")).lower()
            ord_entries = int(ord_row.get("entries_count") or 1)
            all_positions.append({
                "order_id": oid,
                "bot_id": ord_row["bot_id"],
                "network": ord_net,
                "symbol": ord_row["symbol"],
                "strategy_name": "",
                "open_timestamp": format_utc_timestamp(ord_row["created_at"]),
                "timestamp": format_utc_timestamp(ord_row["updated_at"] or ord_row["created_at"]),
                "created_at": format_utc_timestamp(ord_row["created_at"]),
                "updated_at": format_utc_timestamp(ord_row["updated_at"] or ord_row["created_at"]),
                "qty": ord_row["qty"],
                "price": ord_row["close_price"] or ord_row["entry_price"],
                "open_price": ord_row["entry_price"],
                "realized_pnl": ord_row["realized_pnl"] or 0.0,
                "type": ord_row["status"],
                "status": ord_row["status"],
                "side": ord_row["side"],
                "tps_hit": 1 if ord_row["status"] == "TP_HIT" else 0,
                "tps_total": 1,
                "tp_targets": tp_targets,
                "leverage": 20,
                "entries_count": ord_entries,
                "dca_count": max(0, ord_entries - 1)
            })
            
    # 2. Fetch from trades table for legacy trades not already captured in orders
    with db.get_connection() as conn:
        cursor = conn.cursor()
        query = """
            SELECT t.*, b.strategy_name, b.parameters as bot_params, COALESCE(b.network, t.network) as bot_net
            FROM trades t
            LEFT JOIN bot_instances b ON t.bot_id = b.id
        """
        clauses = []
        params = []
        if bot_id is not None:
            clauses.append("t.bot_id = ?")
            params.append(bot_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY t.timestamp ASC"
        cursor.execute(query, params)
        trades = [dict(row) for row in cursor.fetchall()]
        for t in trades:
            t_oid = str(t.get("order_id") or "")
            t_net = t.get("bot_net") or t.get("network")
            if not t_net:
                t_net = "testnet" if ("SIM" in t_oid.upper() or "TESTNET" in t_oid.upper()) else "mainnet"
            t["network"] = str(t_net).lower()

        legacy_cycles = group_trades_into_positions(trades)
        for cyc in legacy_cycles:
            cyc_oid = str(cyc.get("order_id") or "")
            cyc_key = f"{cyc.get('bot_id')}_{cyc.get('symbol')}_{cyc.get('open_timestamp')}"
            
            # Skip if already captured in orders table
            if cyc_oid in seen_order_ids or cyc_key in seen_cycles:
                continue
            # Check if any constituent order id matches
            if any(oid in seen_order_ids for oid in (cyc_oid, cyc_oid.replace("POS_", "").split("_EXIT")[0])):
                continue
                
            all_positions.append(cyc)
                
    if net:
        all_positions = [p for p in all_positions if (p.get("network") or "").lower() == net]
        
    all_positions.sort(key=lambda x: x.get("updated_at") or x.get("timestamp") or "", reverse=True)
    return all_positions

@app.get("/api/closed-positions")
async def get_closed_positions(
    request: Request = None,
    bot_id: Optional[int] = None,
    network: Optional[str] = None,
    symbol: Optional[str] = None,
    status: Optional[str] = None,
    pnl: Optional[str] = None,
    tps_hit: Optional[str] = None,
    search: Optional[str] = None,
    page: Optional[int] = None,
    page_size: int = 20,
    all_items: bool = False
):
    raw_positions = _get_closed_positions_unified(bot_id=bot_id, network_filter=network)

    filtered = []
    total_pnl = 0.0
    profit_count = 0
    profit_sum = 0.0
    loss_count = 0
    loss_sum = 0.0

    for t in raw_positions:
        net = (t.get("network") or "mainnet").lower()
        if network and network != "all" and net != network.lower():
            continue
        if bot_id is not None and str(t.get("bot_id")) != str(bot_id):
            continue
        if symbol and symbol.strip():
            if symbol.strip().upper() not in (t.get("symbol") or "").upper():
                continue
        if search and search.strip():
            q = search.strip().lower()
            sym_match = q in (t.get("symbol") or "").lower()
            bot_match = q in str(t.get("bot_id"))
            strat_match = q in (t.get("strategy_name") or "").lower()
            if not (sym_match or bot_match or strat_match):
                continue
        if status and status != "all":
            st = (t.get("status") or t.get("type") or "").upper()
            if status == "TP_HIT" and "TP" not in st:
                continue
            if status == "SL_HIT" and ("SL" not in st and "STOP_LOSS" not in st):
                continue
            if status == "OPPOSITE_SIGNAL_CLOSED" and "OPPOSITE" not in st:
                continue
            if status == "CLOSED" and any(k in st for k in ("TP", "SL", "STOP_LOSS", "OPPOSITE")):
                continue
        rpnl = float(t.get("realized_pnl") or 0.0)
        if pnl == "profit" and rpnl <= 0:
            continue
        if pnl == "loss" and rpnl >= 0:
            continue
        if tps_hit and tps_hit != "all":
            if tps_hit == "stop_loss":
                if t.get("type") != "STOP_LOSS" and t.get("status") != "SL_HIT":
                    continue
            else:
                if str(t.get("tps_hit")) != str(tps_hit):
                    continue

        total_pnl += rpnl
        if rpnl > 0:
            profit_count += 1
            profit_sum += rpnl
        elif rpnl < 0:
            loss_count += 1
            loss_sum += abs(rpnl)

        filtered.append(t)

    win_rate = round((profit_count / len(filtered) * 100.0), 1) if filtered else 0.0

    stats = {
        "total_pnl": round(total_pnl, 2),
        "win_rate": win_rate,
        "total_trades": len(filtered),
        "profit_count": profit_count,
        "profit_sum": round(profit_sum, 2),
        "loss_count": loss_count,
        "loss_sum": round(loss_sum, 2)
    }

    if all_items or page is None:
        return filtered

    total = len(filtered)
    total_pages = max(1, (total + page_size - 1) // page_size) if total > 0 else 1
    p = max(1, page)
    start_idx = (p - 1) * page_size
    end_idx = start_idx + page_size
    page_items = filtered[start_idx:end_idx]

    return {
        "items": page_items,
        "total": total,
        "page": p,
        "page_size": page_size,
        "total_pages": total_pages,
        "stats": stats
    }

def calculate_rsi(df, period=14):
    if len(df) < period + 1:
        return pd.Series([50.0] * len(df))
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, 1e-9)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50.0)

async def fetch_scanner_cell(symbol: str, timeframe: str):
    try:
        from data_engine import DataEngine
        import pandas as pd
        df = await DataEngine.fetch_external_klines(symbol, timeframe, limit=100)
        if df.empty or len(df) < 50:
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "price": 0.0,
                "trend": "NEUTRAL",
                "chop": 50.0,
                "atr_pct": 0.0,
                "rsi": 50.0
            }
        
        close = df["close"]
        latest_price = float(close.iloc[-1])
        
        # 1. Trend Direction (50 EMA)
        ema50 = close.ewm(span=50, adjust=False).mean()
        latest_ema = float(ema50.iloc[-1])
        trend = "BULLISH" if latest_price > latest_ema else "BEARISH"
        
        # 2. Volatility (ATR %)
        atr_series = DataEngine.calculate_atr(df)
        latest_atr = float(atr_series.iloc[-1])
        atr_pct = (latest_atr / latest_price) * 100 if latest_price > 0 else 0.0
        
        # 3. Chop Index
        chop_series = DataEngine.calculate_chop(df)
        latest_chop = float(chop_series.iloc[-1])
        
        # 4. Momentum (RSI)
        rsi_series = calculate_rsi(df)
        latest_rsi = float(rsi_series.iloc[-1])
        
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "price": latest_price,
            "trend": trend,
            "chop": latest_chop,
            "atr_pct": atr_pct,
            "rsi": latest_rsi
        }
    except Exception as e:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "price": 0.0,
            "trend": "NEUTRAL",
            "chop": 50.0,
            "atr_pct": 0.0,
            "rsi": 50.0
        }

@app.get("/api/scanner")
async def get_scanner_data():
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "LINKUSDT"]
    timeframes = ["1m", "5m", "15m", "1h", "4h"]
    import pandas as pd
    
    tasks = []
    for symbol in symbols:
        for tf in timeframes:
            tasks.append(fetch_scanner_cell(symbol, tf))
            
    results = await asyncio.gather(*tasks)
    return results

class BotUpdateRequest(BaseModel):
    trade_amount_usd: Optional[float] = None
    leverage: Optional[int] = None

def _sync_live_bot_state(active_bot, params: dict) -> None:
    """Keep the running bot instance and its risk/execution state aligned with updated parameters."""
    active_bot.parameters = params

    if hasattr(active_bot, "risk_manager") and active_bot.risk_manager:
        active_bot.risk_manager.leverage = int(params.get("leverage", getattr(active_bot.risk_manager, "leverage", 20)))

        trade_amount_value = params.get("trade_amount_usd")
        if trade_amount_value is None:
            trade_amount_value = params.get("trade_amount")
        if trade_amount_value is not None:
            active_bot.risk_manager.trade_amount_usd = float(trade_amount_value)
        else:
            active_bot.risk_manager.trade_amount_usd = None

        active_bot.risk_manager.stop_loss_pct = float(params.get("stop_loss_pct", getattr(active_bot.risk_manager, "stop_loss_pct", 1.0)))
        active_bot.risk_manager.take_profit_pct = float(params.get("take_profit_pct", getattr(active_bot.risk_manager, "take_profit_pct", 2.0)))

    if hasattr(active_bot, "execution") and active_bot.execution:
        active_bot.execution.parameters = params

@app.patch("/api/bots/{bot_id}")
async def update_bot_params(bot_id: int, req: BotUpdateRequest, request: Request):
    """Update bot trade_amount and/or leverage without stopping/restarting the bot.
    Changes take effect on the NEXT trade signal."""
    await check_admin_auth(request, db=db_live)
    bot = db_live.get_bot(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot {bot_id} not found")

    params = bot["parameters"].copy()
    updated_fields = []

    if req.trade_amount_usd is not None:
        if req.trade_amount_usd <= 0:
            raise HTTPException(status_code=400, detail="trade_amount_usd must be positive")
        params["trade_amount_usd"] = req.trade_amount_usd
        updated_fields.append(f"trade_amount_usd={req.trade_amount_usd}")

    if req.leverage is not None:
        if req.leverage < 1 or req.leverage > 125:
            raise HTTPException(status_code=400, detail="leverage must be between 1 and 125")
        params["leverage"] = req.leverage
        updated_fields.append(f"leverage={req.leverage}x")

    if not updated_fields:
        return {"status": "no_change", "message": "No fields provided to update"}

    # Persist updated parameters
    import json as _json
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE bot_instances SET parameters = ? WHERE id = ?",
            (_json.dumps(params), bot_id)
        )
        conn.commit()

    # Also update in-memory running bot if active
    if manager and bot_id in manager.active_bots:
        _sync_live_bot_state(manager.active_bots[bot_id], params)

    msg = f"Bot {bot_id} updated: {', '.join(updated_fields)}. Effective on next trade."
    db.log_message("INFO", msg)
    return {"status": "success", "message": msg, "parameters": params}

@app.post("/api/bots/{bot_id}/retry-tp")
async def retry_tp_orders(bot_id: int, request: Request = None):
    if request:
        try:
            await check_admin_auth(request)
        except Exception:
            pass
    if not manager:
        raise HTTPException(status_code=500, detail="Manager not initialized")
    if bot_id not in manager.active_bots:
        raise HTTPException(status_code=404, detail="Bot is not currently active/running")
    active_bot = manager.active_bots[bot_id]
    if not hasattr(active_bot, 'execution') or not active_bot.execution:
        raise HTTPException(status_code=400, detail="Bot has no active execution handler")
    success = await active_bot.execution.retry_tp_orders()
    return {"status": "success" if success else "failed"}

@app.post("/api/bots/{bot_id}/close-position")
async def api_close_position(bot_id: int, request: Request = None):
    if request:
        try:
            await check_admin_auth(request)
        except Exception:
            pass
    bot = db.get_bot(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
        
    net = (bot.get("network") or "mainnet").lower()
    is_sim = (net == "testnet")
    
    if not manager:
        raise HTTPException(status_code=500, detail="Manager not initialized")
        
    is_running = bot_id in manager.active_bots
    if is_running:
        active_bot = manager.active_bots[bot_id]
        eh = active_bot.execution
    else:
        from execution_handler import ExecutionHandler
        eh = ExecutionHandler(db=db, bot_id=bot_id, simulation_mode=is_sim, use_testnet=False, network=net)
        
    if not eh.active_position:
        raise HTTPException(status_code=400, detail="No active position found for this bot")
        
    current_price = 0.0
    if is_running and active_bot.data_engine.klines:
        current_price = active_bot.data_engine.klines[-1]["close"]
    else:
        try:
            symbol = eh.active_position[0]["symbol"] if isinstance(eh.active_position, list) else eh.active_position["symbol"]
            ticker = await eh._send_request("GET", "/fapi/v1/ticker/price", {"symbol": symbol.upper()})
            current_price = float(ticker.get("price", 0.0))
        except Exception:
            pass
            
    positions_to_close = list(eh.active_position) if isinstance(eh.active_position, list) else [eh.active_position]
    for pos in positions_to_close:
        if not pos:
            continue
        side = pos["side"]
        entry_price = pos["entry_price"]
        qty = pos["qty"]
        price_to_use = current_price if current_price > 0 else entry_price
        pnl = 0.0
        if price_to_use > 0:
            if side == "BUY":
                pnl = (price_to_use - entry_price) * qty
            else:
                pnl = (entry_price - price_to_use) * qty
        await eh.close_position(
            current_price=price_to_use,
            reason="MANUAL_CLOSE",
            pnl=pnl,
            side=side,
            pos_to_close=pos
        )
        
    if eh.active_position:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to close position on {net.upper()}."
        )
        
    return {"status": "success"}

@app.get("/api/orders")
async def get_orders_api(
    request: Request,
    network: Optional[str] = None,
    status: Optional[str] = None,
    symbol: Optional[str] = None,
    side: Optional[str] = None,
    bot_id: Optional[int] = None,
    limit: int = 100,
    offset: int = 0
):
    net = None if network in ("all", None) else network.lower()
    return db.get_orders(limit=limit, offset=offset, bot_id=bot_id, symbol=symbol, status=status, side=side, network=net)

@app.get("/api/binance-logs")
async def get_binance_logs_api(
    request: Request,
    network: Optional[str] = None,
    action: Optional[str] = None,
    status: Optional[str] = None,
    symbol: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
):
    net = None if network in ("all", None) else network.lower()
    return db.get_binance_api_logs(limit=limit, offset=offset, symbol=symbol, action=action, status=status, network=net)

@app.get("/api/binance-logs/raw")
async def get_binance_logs_raw(request: Request, lines: int = 150):
    log_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "binance_orders.log")
    if not os.path.exists(log_file):
        return {"lines": []}
    try:
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            all_lines = f.readlines()
            return {"lines": [line.rstrip("\r\n") for line in all_lines[-lines:]]}
    except Exception as e:
        return {"lines": [f"Error reading log file: {str(e)}"]}

@app.get("/api/pnl")
async def get_pnl_api(request: Request, period: str = "30d", network: Optional[str] = None):
    return await get_pnl(period=period, network=network)

@app.get("/api/logs")
async def get_logs_api(request: Request, bot_id: Optional[int] = None, network: Optional[str] = None):
    return await get_logs(bot_id=bot_id, network=network)

# --- BACKTEST RESULTS API ---
@app.get("/api/backtest-results")
async def get_backtest_results_api(
    strategy: Optional[str] = None,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    sort_by: str = "pnl_desc",
    only_profitable: bool = False,
    min_win_rate: Optional[float] = None,
    limit: int = 1000,
    offset: int = 0
):
    return db.get_backtest_results(
        strategy_name=strategy,
        symbol=symbol,
        timeframe=timeframe,
        sort_by=sort_by,
        only_profitable=only_profitable,
        min_win_rate=min_win_rate,
        limit=limit,
        offset=offset
    )

@app.get("/api/backtest-stats")
async def get_backtest_stats_api():
    return db.get_backtest_summary_stats()

@app.get("/api/backtest-result/{result_id}")
async def get_backtest_result_api(result_id: int):
    res = db.get_backtest_result_by_id(result_id)
    if not res:
        raise HTTPException(status_code=404, detail="Backtest result not found")
    return res

@app.get("/api/backtest-chart-data/{result_id}")
async def get_backtest_chart_data_api(result_id: int):
    res = db.get_backtest_result_by_id(result_id)
    if not res:
        raise HTTPException(status_code=404, detail="Backtest result not found")
    
    symbol = res["symbol"]
    timeframe = res["timeframe"]
    
    import pandas as pd
    from backtest.data_loader import KlineDataLoader
    df = KlineDataLoader.load_klines(symbol, timeframe, days=30)
    if df.empty:
        return {"result": res, "candles": [], "markers": []}
    
    # Format OHLCV candles for Lightweight Charts
    dt_series = pd.to_datetime(df["timestamp"])
    unix_times = (dt_series.astype('int64') // 10**9).values
    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    volumes = df["volume"].values if "volume" in df.columns else [0] * len(df)
    
    candles = []
    for i in range(len(df)):
        candles.append({
            "time": int(unix_times[i]),
            "open": float(opens[i]),
            "high": float(highs[i]),
            "low": float(lows[i]),
            "close": float(closes[i]),
            "volume": float(volumes[i])
        })
    
    # Build chart markers from trade_events if available
    markers = []
    events = res.get("trade_events", [])
    for ev in events:
        ev_type = ev.get("type", "")
        ev_time = int(ev.get("time", 0))
        ev_price = ev.get("price", ev.get("exit_price", 0))
        ev_pnl = ev.get("pnl", 0)
        ev_num = ev.get("entry_num", 1)
        
        if "LONG_ENTRY" in ev_type or "LONG_DCA" in ev_type:
            markers.append({
                "time": ev_time,
                "position": "belowBar",
                "color": "#10b981",
                "shape": "arrowUp",
                "text": f"BUY #{ev_num} @ {ev_price:.4f}"
            })
        elif "SHORT_ENTRY" in ev_type or "SHORT_DCA" in ev_type:
            markers.append({
                "time": ev_time,
                "position": "aboveBar",
                "color": "#f43f5e",
                "shape": "arrowDown",
                "text": f"SELL #{ev_num} @ {ev_price:.4f}"
            })
        elif "TP_LONG" in ev_type:
            markers.append({
                "time": ev_time,
                "position": "aboveBar",
                "color": "#38bdf8",
                "shape": "circle",
                "text": f"TP Long: {('+$' if ev_pnl >= 0 else '-$')}{abs(ev_pnl):.2f}"
            })
        elif "TP_SHORT" in ev_type:
            markers.append({
                "time": ev_time,
                "position": "belowBar",
                "color": "#38bdf8",
                "shape": "circle",
                "text": f"TP Short: {('+$' if ev_pnl >= 0 else '-$')}{abs(ev_pnl):.2f}"
            })
        elif "CLOSE_EOP" in ev_type:
            side_str = "Long" if "LONG" in ev_type else "Short"
            pos_str = "aboveBar" if "LONG" in ev_type else "belowBar"
            markers.append({
                "time": ev_time,
                "position": pos_str,
                "color": "#e2e8f0",
                "shape": "square",
                "text": f"EOP {side_str}: {('+$' if ev_pnl >= 0 else '-$')}{abs(ev_pnl):.2f}"
            })
    
    # Sort markers chronologically
    markers.sort(key=lambda m: m["time"])
    
    return {
        "result": res,
        "candles": candles,
        "markers": markers
    }

@app.post("/api/clear-database")
async def clear_database_api(request: Request = None):
    db.clear_database_except_symbols()
    return {"status": "success", "message": "All operational tables cleared except symbols configuration."}

# --- SYMBOLS CONFIGURATION API ---

@app.get("/api/symbols")
async def get_symbols(request: Request = None):
    return db.get_symbols_config()

class SymbolConfigUpdateRequest(BaseModel):
    symbol: str
    max_leverage: int

@app.post("/api/symbols")
async def update_symbol(req: SymbolConfigUpdateRequest, request: Request = None):
    db.update_symbol_leverage(req.symbol, req.max_leverage)
    return {"status": "success"}

@app.delete("/api/symbols/{symbol}")
async def delete_symbol(symbol: str, request: Request = None):
    db.delete_symbol_config(symbol)
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web.app:app", host="127.0.0.1", port=8000, reload=True)

