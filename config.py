"""Global configuration and environment settings."""
import os

# ── Alpaca ──────────────────────────────────────────────────────────────────
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY",    "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_PAPER      = True   # Set False to use live account

# ── Trading defaults ─────────────────────────────────────────────────────────
DEFAULT_POSITION_SIZE_PCT = 0.10   # 10 % of portfolio per trade
DEFAULT_STOP_LOSS_PCT     = 0.02   # 2 % stop loss
DEFAULT_COMMISSION        = 0.0    # Alpaca charges no commission

# ── Backtesting ───────────────────────────────────────────────────────────────
DEFAULT_INITIAL_CAPITAL   = 100_000.0
DEFAULT_BACKTEST_INTERVAL = "1d"

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH = "ledger.db"

# ── UI ────────────────────────────────────────────────────────────────────────
MAX_BARS_DISPLAYED = 500   # max candles kept in memory per ticker
APP_NAME           = "AlphaTrader"
