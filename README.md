# AlphaTrader

A professional algorithmic trading desktop application built with Python and PyQt6. Connects to Alpaca's brokerage API for live tick-level market data and order execution, with a built-in backtester, SQLite trade ledger, and an in-app Python code editor for writing and hot-loading custom strategies.

> ⚠️ **Paper trading mode is enabled by default.** No real money is at risk unless you explicitly set `ALPACA_PAPER = False` in `config.py` and connect a funded live account.

---

## Screenshots

| Live Trading | Backtester | Algo Editor |
|---|---|---|
| Multi-ticker charts with σ bands | Equity curve + metrics | Syntax-highlighted Python editor |

---

## Features

### 📈 Live Trading
- **Tick-level data** — subscribes to individual trade events via Alpaca's WebSocket, not 1-minute bars. Prices update on every executed transaction.
- **Auto-reconnect** — if the stream drops, the feed automatically reconnects with a 3-second backoff. No manual restart needed.
- **Multi-ticker** — monitor and trade any number of tickers simultaneously. One algorithm instance sees and acts on all of them at once.
- **Live chart** per ticker with:
  - Price line
  - EMA-9 and EMA-21 overlays
  - Rolling mean line
  - Upper and lower ±2σ band lines (dashed)
  - Green ▲ buy and red ▼ sell signal markers
- **Stop-loss enforcement** — checked on every tick, not just on bar close.
- **Portfolio panel** — real-time equity, cash, open position count, unrealised and realised P&L.

### 🔬 Backtesting
- Fetch historical data from **yfinance** or **Alpaca** historical API.
- Configurable: tickers, date range, interval (1d / 1h / 30m / 15m / 5m), initial capital, position size %, stop loss %.
- Results dashboard:
  - Total return, Sharpe ratio, max drawdown, win rate, trade count
  - Equity curve with area fill
  - Full trades table

### 🧠 Algo Editor
- Write any Python class in the built-in editor and click **⚡ Load Algorithm** to register it instantly — no restart required.
- Python syntax highlighting.
- The loaded algorithm appears immediately in both the live trading and backtest dropdowns.
- Pre-filled template with docstring guidance to get started quickly.

### 📋 Trade Ledger
- Every trade (live and backtest) is persisted to a local SQLite database (`ledger.db`).
- Filter by session type (LIVE / BACKTEST).
- Colour-coded side and P&L columns.
- Win rate and total realised P&L summary bar.

---

## Built-in Algorithms

### Moving Average Crossover
Generates a **BUY** when the fast EMA crosses above the slow EMA, and a **SELL** when it crosses below. Defaults to EMA-9 vs EMA-21. Only fires once per cross — no repeated signals.

### Mean Reversion (Z-Score)
Computes a rolling z-score of the price series. Fires a **BUY** when `z < −threshold` (price unusually low) and a **SELL** when `z > +threshold` (price unusually high). Default threshold is ±2σ; set to 3.0 for the classic 3σ variant.

**Re-arm logic:** after a signal fires, that direction is disarmed until the z-score recovers back inside `±exit_z` (default 0.5σ). This prevents repeated signals while price stays stretched outside the bands.

---

## Project Structure

```
trading_platform/
├── main.py                          # Application entry point + MainWindow
├── run.py                           # Alternate launcher (sets sys.path first)
├── config.py                        # Global settings and defaults
├── requirements.txt
│
├── algorithms/
│   ├── base_algorithm.py            # Abstract BaseAlgorithm, Signal, SignalType
│   ├── moving_average_crossover.py  # EMA crossover strategy
│   └── mean_reversion.py            # Z-score mean reversion with re-arm logic
│
├── core/
│   ├── alpaca_client.py             # Alpaca REST API wrapper (account, orders, history)
│   ├── data_feed.py                 # WebSocket tick feed thread with auto-reconnect
│   ├── backtester.py                # Historical simulation engine
│   ├── order_manager.py             # Signal → order execution + stop-loss monitoring
│   ├── ledger.py                    # SQLite trade ledger
│   └── portfolio.py                 # In-memory mark-to-market portfolio tracker
│
└── ui/
    ├── chart_widget.py              # pyqtgraph live chart with bands and markers
    ├── backtest_widget.py           # Backtest config form + results dashboard
    ├── algo_editor.py               # Python code editor with syntax highlighter
    └── ledger_widget.py             # Trade history table with filtering
```

---

## Requirements

- Python 3.10+
- An [Alpaca](https://alpaca.markets) account (paper account is free, no approval needed)

---

## Installation

```bash
git clone https://github.com/your-username/trading_platform.git
cd trading_platform
pip install -r requirements.txt
```

### Dependencies

| Package | Purpose |
|---|---|
| `PyQt6` | Desktop UI framework |
| `pyqtgraph` | GPU-accelerated chart rendering |
| `alpaca-py` | Alpaca brokerage REST + WebSocket client |
| `yfinance` | Historical OHLCV data for backtesting |
| `pandas` | Data manipulation |
| `numpy` | Numerical computation |

---

## Configuration

API credentials are read from environment variables at startup:

```bash
export ALPACA_API_KEY="your_key_here"
export ALPACA_SECRET_KEY="your_secret_here"
```

You can also paste them directly into the toolbar fields when the app launches — they are saved to local settings and restored on next open.

Key settings in `config.py`:

| Setting | Default | Description |
|---|---|---|
| `ALPACA_PAPER` | `True` | Set `False` to use a live funded account |
| `DEFAULT_POSITION_SIZE_PCT` | `0.10` | 10% of portfolio value per trade |
| `DEFAULT_STOP_LOSS_PCT` | `0.02` | 2% stop loss below entry price |
| `DEFAULT_INITIAL_CAPITAL` | `100,000` | Default backtest starting capital |
| `MAX_BARS_DISPLAYED` | `500` | Max ticks kept in memory per ticker |
| `DB_PATH` | `ledger.db` | SQLite ledger file location |

---

## Running

```bash
# From inside the trading_platform directory
python main.py

# Or from any directory using the launcher
python run.py

# Or from the parent directory
python trading_platform/main.py
```

---

## Writing a Custom Algorithm

1. Open the **🧠 Algo Editor** tab.
2. Write a class that inherits `BaseAlgorithm` and implements `on_bar(bars)`.
3. Click **⚡ Load Algorithm**.

The method signature:

```python
def on_bar(self, bars: dict[str, pd.DataFrame]) -> list[Signal]:
    """
    bars  : dict mapping ticker symbol → DataFrame
            Each DataFrame has columns: [timestamp, close]
            Rows are sorted ascending. Latest tick = df.iloc[-1]

    Return a list of Signal objects (may be empty).
    """
```

Example skeleton:

```python
from algorithms.base_algorithm import BaseAlgorithm, Signal, SignalType

class MyStrategy(BaseAlgorithm):
    NAME        = "My Strategy"
    DESCRIPTION = "One-line description shown in dropdowns."
    PARAMETERS  = {"window": 20}

    def on_bar(self, bars):
        signals = []
        for ticker, df in bars.items():
            if len(df) < self.get_param("window"):
                continue
            # ... your logic here ...
            signals.append(Signal(
                ticker      = ticker,
                signal_type = SignalType.BUY,
                price       = float(df["close"].iloc[-1]),
                notes       = "reason string shown in ledger",
            ))
        return signals

    def reset(self):
        super().reset()
        # clear any state before a backtest run
```

---

## Data & Latency

| Mode | Data | Frequency |
|---|---|---|
| **Live trading** | Alpaca WebSocket (IEX feed) | Every individual trade tick |
| **Backtest (yfinance)** | Yahoo Finance OHLCV | 1d / 1h / 30m / 15m / 5m |
| **Backtest (Alpaca)** | Alpaca historical bars | 1Day / 1Hour / 30Min / 15Min / 5Min |

The live feed subscribes to `subscribe_trades` (individual transactions), not `subscribe_bars` (1-minute aggregates). For liquid large-cap stocks this can mean hundreds of ticks per minute during market hours.

---

## Limitations & Known Notes

- **US markets only.** Alpaca's Trading API supports US-listed equities and ETFs. Asian and European exchanges are not supported.
- **Market hours.** Tick data is only available when US markets are open (9:30am–4:00pm ET, Mon–Fri), plus extended hours if your Alpaca plan includes them. Alpaca offers 24/5 trading for supported instruments.
- **Paper mode default.** `ALPACA_PAPER = True` in `config.py`. Double-check this before connecting a live account.
- **Fractional shares.** Position sizing uses `% of portfolio / current price`. For low-priced stocks this may result in fractional quantities — ensure fractional trading is enabled on your Alpaca account.
- **Stop-losses are software-enforced.** They are checked on every incoming tick by the `OrderManager`. They are not native exchange stop orders, so a gap open could result in a fill worse than the stop price.

---

## Disclaimer

This software is provided for educational and research purposes only. It is not financial advice. Algorithmic trading involves significant risk of loss. Always test thoroughly in paper trading mode before using real funds. The authors accept no liability for financial losses incurred through use of this software.
