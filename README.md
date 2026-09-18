# 🚀 Binance AI Scalper Trading Bot & Backtest Matrix

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![TradingView Charts](https://img.shields.io/badge/Charts-TradingView_Lightweight-131722.svg)](https://www.tradingview.com/HTML5-stock-forex-bitcoin-charting-library/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

An institutional-grade, high-frequency quantitative scalping bot and multi-pair backtesting suite engineered for **Binance USDT-M Futures**. Features 13 plug-and-play strategies, dynamic Dollar-Cost Averaging (DCA) position management, real-time market scanners, multi-timeframe backtest engine, and a dark glassmorphism web dashboard with interactive TradingView charts.

---

## ✨ Key Features

- ⚡ **12 Built-in Quantitative Scalping Strategies**:
  - `Stochastic_RSI`, `Keltner_Channel_Breakout`, `RSI_Divergence_Scalp`, `Bollinger_Bands`, `Engulfing_Volume_Scalp`, `EMA_RSI_Crossover`, `SuperTrend`, `Hull_MA_Scalp`, `EMA_Ribbon_Scalp`, `Volume_Delta_Scalp`, `MACD_ZeroCross_Scalp`, `Squeeze_Momentum_Scalp`.
- 📊 **Multi-Pair Dual-Timeframe Backtest Engine**:
  - Evaluates **10,660+ strategy combinations** across **410+ Binance Futures symbols** simultaneously on `5m` and `15m` timeframes.
  - Multi-threaded chunk processing with automated local disk kline caching.
- 🎯 **Advanced DCA & Position Management**:
  - Dynamic weighted-average entry recalculation on multi-signal DCA entries.
  - Independent Long and Short trade accounting.
  - Automated Take Profit (TP) order execution and End-of-Period (EOP) settling.
- 📈 **Interactive TradingView Candlestick Visualizer**:
  - Embedded Lightweight Charts rendering 30-day historical candlesticks.
  - Visual execution markers for Long Entries (🟢), Short Entries (🔴), Take Profit Exits (🔵), and Period Settles (⚪).
- 🖥️ **Modern Web Dashboard & Control Center**:
  - Real-time Performance Matrix with filters for profitability, win rates, and strategies.
  - Instant CSV & Excel export for in-depth analysis.
  - Live Market Scanner, Real-time Bot Controller, and Binance API Execution Logs.
- 🔒 **Simulation & Live Trading Modes**:
  - Zero-risk paper trading simulation mode.
  - Binance Futures Testnet and Live Mainnet execution with leverage configuration.

---

## 🏗️ Architecture & Project Structure

```text
trading_bot/
├── backtest/                  # Backtesting engine & suite runners
│   ├── data_loader.py         # Binance historical kline downloader & cache
│   ├── engine.py              # Core execution engine with DCA averaging & TP
│   └── run_chunked_suite.py   # Multi-threaded batch suite runner
├── strategy/                  # 13 Modular quantitative strategy classes
│   ├── base_strategy.py       # Abstract strategy base class
│   ├── ema_rsi_crossover.py   # EMA + RSI trend strategy
│   ├── squeeze_momentum.py    # Squeeze Momentum & Bollinger breakout
│   └── ...                    # (11 other strategy modules)
├── web/                       # FastAPI web backend & frontend templates
│   ├── templates/             # Glassmorphism HTML5/CSS/JS dashboards
│   │   ├── backtest.html      # Backtest Performance Matrix table & filters
│   │   ├── backtest_detail.html # TradingView interactive chart & event log
│   │   ├── bots.html          # Active bot instances & live monitor
│   │   ├── scanner.html       # Real-time multi-pair signal scanner
│   │   └── login.html         # Admin authentication interface
│   ├── app.py                 # FastAPI application routes and endpoints
│   └── auth.py                # Admin session & authentication security
├── bot_manager.py             # Multi-bot instance orchestrator & supervisor
├── config.py                  # Project-wide centralized configuration
├── database.py                # SQLite database interface with WAL mode & locking retries
├── data_engine.py             # Live market WebSocket & REST candle streamer
├── execution_handler.py       # Binance Futures API order execution & simulated broker
├── risk_manager.py            # Position sizing, margin calculations & leverage control
├── .env.example               # Example environment variable template
├── requirements.txt           # Python dependencies
└── README.md                  # Project documentation
```

---

## 🚀 Quick Start & Installation

### 1. Prerequisites
- **Python 3.10+**
- **Git**

### 2. Clone the Repository
```bash
git clone https://github.com/your-username/trading_bot_scalper.git
cd trading_bot_scalper
```

### 3. Create & Activate Virtual Environment
```bash
# Windows
python -m venv venv
.\venv\Scripts\activate

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
```

### 4. Install Dependencies
```bash
pip install -r requirements.txt
```

### 5. Configure Environment Variables
Copy `.env.example` to `.env` and configure your preferences:
```bash
cp .env.example .env
```

Edit `.env`:
```ini
# Binance Futures API (Optional for simulation mode)
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_api_secret
USE_TESTNET=false

# Web Dashboard Login
ADMIN_EMAIL=admin@tradingbot.com
ADMIN_PASSWORD=YourSecurePassword123!
SECRET_KEY=super_secret_session_key

# Bot Defaults
SIMULATION_MODE=true
TRADE_SYMBOL=BTCUSDT
CANDLE_INTERVAL=5m
LEVERAGE=20
```

---

## 🖥️ Running the Application

### 1. Launch the Web Dashboard
Start the FastAPI server:
```bash
uvicorn web.app:app --host 127.0.0.1 --port 8000 --reload
```
Open your browser and navigate to:
- **Dashboard**: [http://127.0.0.1:8000](http://127.0.0.1:8000)
- **Backtest Matrix**: [http://127.0.0.1:8000/backtest](http://127.0.0.1:8000/backtest)
- **Bot Detail & Chart**: [http://127.0.0.1:8000/backtest/detail](http://127.0.0.1:8000/backtest/detail)
- **Market Scanner**: [http://127.0.0.1:8000/scanner](http://127.0.0.1:8000/scanner)

### 2. Run the 30-Day Multi-Pair Backtest Suite
Execute backtesting across all 410+ symbols and 26 strategy/timeframe suites:
```bash
python -c "from backtest.run_chunked_suite import run_chunked_backtest; run_chunked_backtest(days=30)"
```

### 3. Run Live / Simulation Bot Worker
```bash
python main.py
```

---

## 📈 Scalping Strategy Reference

| Strategy | Type | Description |
| :--- | :---: | :--- |
| **Squeeze Momentum Scalp** | Trend / Volatility | John Carter's Squeeze Momentum indicator using Bollinger Bands inside Keltner Channels. |
| **EMA Ribbon Scalp** | Trend Following | Multi-period exponential moving average ribbon alignment with pullback entries. |
| **Stochastic RSI** | Mean Reversion | Oversold/Overbought oscillator reversals filtered by ADX regime. |
| **Keltner Channel Breakout** | Breakout | High-momentum channel envelope breakouts with ATR volatility expansion. |
| **RSI Divergence Scalp** | Reversion | Regular and hidden divergence detector between price swings and RSI peaks. |
| **Bollinger Bands** | Mean Reversion | Standard deviation boundary bounces in low-trend chop markets. |
| **Engulfing Volume Scalp** | Price Action | Bullish/Bearish engulfing candle patterns confirmed by anomalous volume spike. |
| **EMA RSI Crossover** | Trend / Momentum | Fast/Slow EMA crossovers verified by RSI momentum filters. |
| **SuperTrend** | Trend Following | Directional volatility band trailing stops and trend shifts. |
| **Hull MA Scalp** | Fast Trend | Low-lag Hull Moving Average curve inflections for ultra-fast scalping. |
| **Volume Delta Scalp** | Volume Flow | Order-flow approximation using buy vs. sell candle volume imbalance. |
| **MACD ZeroCross Scalp** | Momentum | MACD line zero-axis crossing with signal line confirmation. |

---

## 🛡️ Risk Management & Safety

- **Position Sizing**: Margin per trade (default `$20.00`) multiplied by configured leverage (default `20x`), generating `$400.00` notional position size per signal.
- **DCA Compounding**: Dynamically recalculates weighted average entry prices for multi-signal positions.
- **Strict Data Segregation**: API keys, credentials, and local databases are strictly excluded from version control via `.gitignore`.

---

## 📄 License
This project is open-source and licensed under the [MIT License](LICENSE).
