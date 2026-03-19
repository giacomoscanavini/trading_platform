"""
Alpaca API client wrapper.

Wraps alpaca-py's TradingClient and StockHistoricalDataClient into a
single, easy-to-use class. Raises clear errors when the library is missing
or credentials are wrong.
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from datetime import datetime
from typing import Optional
import config


class AlpacaClient:
    def __init__(
        self,
        api_key:    str = "",
        secret_key: str = "",
        paper:      bool = True,
    ):
        self.api_key    = api_key    or config.ALPACA_API_KEY
        self.secret_key = secret_key or config.ALPACA_SECRET_KEY
        self.paper      = paper
        self._trading   = None
        self._data      = None

    # ── Connection ───────────────────────────────────────────────────────────

    def connect(self) -> None:
        try:
            from alpaca.trading.client           import TradingClient
            from alpaca.data.historical          import StockHistoricalDataClient
        except ImportError as exc:
            raise ImportError(
                "alpaca-py is not installed. Run:  pip install alpaca-py"
            ) from exc

        self._trading = TradingClient(
            self.api_key, self.secret_key, paper=self.paper
        )
        self._data = StockHistoricalDataClient(self.api_key, self.secret_key)

    def is_connected(self) -> bool:
        return self._trading is not None

    # ── Account ──────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        self._require()
        a = self._trading.get_account()
        return {
            "equity":          float(a.equity),
            "cash":            float(a.cash),
            "buying_power":    float(a.buying_power),
            "portfolio_value": float(a.portfolio_value),
        }

    def get_positions(self) -> list[dict]:
        self._require()
        return [
            {
                "ticker":             p.symbol,
                "qty":                float(p.qty),
                "avg_entry":          float(p.avg_entry_price),
                "current_price":      float(p.current_price or 0),
                "unrealized_pnl":     float(p.unrealized_pl or 0),
                "unrealized_pnl_pct": float(p.unrealized_plpc or 0),
            }
            for p in self._trading.get_all_positions()
        ]

    # ── Orders ───────────────────────────────────────────────────────────────

    def place_market_order(self, ticker: str, qty: float, side: str) -> dict:
        self._require()
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums    import OrderSide, TimeInForce

        req = MarketOrderRequest(
            symbol        = ticker,
            qty           = qty,
            side          = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL,
            time_in_force = TimeInForce.DAY,
        )
        o = self._trading.submit_order(req)
        return {
            "order_id":     str(o.id),
            "ticker":       o.symbol,
            "qty":          float(o.qty),
            "side":         o.side.value,
            "status":       o.status.value,
            "submitted_at": str(o.submitted_at),
        }

    # ── Historical data ───────────────────────────────────────────────────────

    def get_historical_bars(
        self,
        tickers:   list[str],
        start:     datetime,
        end:       datetime,
        timeframe: str = "1Day",
    ) -> dict[str, pd.DataFrame]:
        self._require()
        from alpaca.data.requests  import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        tf_map = {
            "1Min":  TimeFrame.Minute,
            "5Min":  TimeFrame.Minute,
            "15Min": TimeFrame.Minute,
            "30Min": TimeFrame.Minute,
            "1Hour": TimeFrame.Hour,
            "1Day":  TimeFrame.Day,
        }
        req = StockBarsRequest(
            symbol_or_symbols = tickers,
            timeframe         = tf_map.get(timeframe, TimeFrame.Day),
            start             = start,
            end               = end,
        )
        raw    = self._data.get_stock_bars(req)
        result: dict[str, pd.DataFrame] = {}

        for ticker in tickers:
            try:
                if len(tickers) == 1:
                    df = raw.df.reset_index()
                else:
                    df = raw.df.xs(ticker, level=0).reset_index()
                df.columns = [c.lower() for c in df.columns]
                # Normalise timestamp column name
                for col in ("timestamp", "date", "datetime"):
                    if col in df.columns:
                        df = df.rename(columns={col: "timestamp"})
                        break
                result[ticker] = df
            except (KeyError, Exception):
                result[ticker] = pd.DataFrame(
                    columns=["timestamp", "open", "high", "low", "close", "volume"]
                )

        return result

    # ── Internal ─────────────────────────────────────────────────────────────

    def _require(self) -> None:
        if not self.is_connected():
            raise RuntimeError(
                "AlpacaClient is not connected. Call connect() first."
            )
