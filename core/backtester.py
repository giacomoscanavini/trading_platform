"""
Backtesting engine.

Fetches historical OHLCV data (Alpaca or yfinance), then replays
bars chronologically through any BaseAlgorithm, simulating orders,
stop-losses, and portfolio equity.
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class BacktestResult:
    trades:       list[dict]       = field(default_factory=list)
    equity_curve: Optional[pd.Series] = None
    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate:     float = 0.0
    total_trades: int   = 0


class Backtester:
    def __init__(self, alpaca_client=None):
        self._alpaca = alpaca_client

    # ── Data fetching ────────────────────────────────────────────────────────

    def fetch_data_yfinance(
        self,
        tickers:  list[str],
        start:    datetime,
        end:      datetime,
        interval: str = "1d",
    ) -> dict[str, pd.DataFrame]:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise ImportError(
                "yfinance is not installed. Run:  pip install yfinance"
            ) from exc

        result: dict[str, pd.DataFrame] = {}
        for ticker in tickers:
            df = yf.download(
                ticker, start=start, end=end,
                interval=interval, auto_adjust=True, progress=False,
            )
            if df.empty:
                result[ticker] = pd.DataFrame(
                    columns=["timestamp", "open", "high", "low", "close", "volume"]
                )
                continue

            df.columns = [c.lower() for c in df.columns]
            df = df.reset_index()
            # Normalise date column
            for col in ("date", "datetime", "index"):
                if col in df.columns:
                    df = df.rename(columns={col: "timestamp"})
                    break
            result[ticker] = df

        return result

    def fetch_data_alpaca(
        self,
        tickers:   list[str],
        start:     datetime,
        end:       datetime,
        timeframe: str = "1Day",
    ) -> dict[str, pd.DataFrame]:
        if self._alpaca is None or not self._alpaca.is_connected():
            raise RuntimeError(
                "Alpaca client is not connected. "
                "Connect via the toolbar first."
            )
        return self._alpaca.get_historical_bars(tickers, start, end, timeframe)

    # ── Simulation ───────────────────────────────────────────────────────────

    def run(
        self,
        algorithm,
        bars_data:         dict[str, pd.DataFrame],
        initial_capital:   float = 100_000.0,
        position_size_pct: float = 0.10,
        stop_loss_pct:     float = 0.02,
        commission:        float = 0.0,
    ) -> BacktestResult:
        from algorithms.base_algorithm import SignalType

        algorithm.reset()

        # Build a unified, sorted timeline across all tickers
        all_ts = sorted({
            ts
            for df in bars_data.values()
            if len(df) > 0 and "timestamp" in df.columns
            for ts in df["timestamp"].tolist()
        })

        if not all_ts:
            return BacktestResult()

        capital: float = initial_capital
        # positions: ticker → {qty, entry_price, stop_price}
        positions: dict[str, dict] = {}
        trades:    list[dict]      = []
        equity_pts: list[dict]     = []
        current_prices: dict[str, float] = {}

        for ts in all_ts:
            # ── Slice data up to current timestamp ───────────────────────────
            snapshot: dict[str, pd.DataFrame] = {}
            for ticker, df in bars_data.items():
                if "timestamp" not in df.columns:
                    continue
                sub = df[df["timestamp"] <= ts]
                if len(sub) > 0:
                    snapshot[ticker] = sub.copy().reset_index(drop=True)
                    current_prices[ticker] = float(sub["close"].iloc[-1])

            if not snapshot:
                continue

            # ── Check stop-losses ─────────────────────────────────────────────
            for ticker in list(positions.keys()):
                price = current_prices.get(ticker)
                pos   = positions[ticker]
                if price is not None and price <= pos["stop_price"]:
                    pnl     = (price - pos["entry_price"]) * pos["qty"]
                    revenue = pos["qty"] * price * (1 - commission)
                    capital += revenue
                    trades.append({
                        "timestamp": ts,
                        "ticker":    ticker,
                        "side":      "SELL",
                        "qty":       pos["qty"],
                        "price":     price,
                        "pnl":       round(pnl, 4),
                        "reason":    "Stop Loss",
                    })
                    del positions[ticker]

            # ── Algorithm signals ─────────────────────────────────────────────
            for signal in algorithm.on_bar(snapshot):
                ticker = signal.ticker
                price  = current_prices.get(ticker, signal.price)

                if signal.signal_type == SignalType.BUY and ticker not in positions:
                    size      = signal.size_pct or position_size_pct
                    trade_val = capital * size
                    qty       = trade_val / price
                    cost      = qty * price * (1 + commission)

                    if cost <= capital and qty > 0:
                        capital -= cost
                        positions[ticker] = {
                            "qty":         qty,
                            "entry_price": price,
                            "stop_price":  price * (1 - stop_loss_pct),
                        }
                        trades.append({
                            "timestamp": ts,
                            "ticker":    ticker,
                            "side":      "BUY",
                            "qty":       round(qty, 6),
                            "price":     price,
                            "pnl":       None,
                            "reason":    signal.notes,
                        })

                elif signal.signal_type == SignalType.SELL and ticker in positions:
                    pos     = positions[ticker]
                    pnl     = (price - pos["entry_price"]) * pos["qty"]
                    revenue = pos["qty"] * price * (1 - commission)
                    capital += revenue
                    trades.append({
                        "timestamp": ts,
                        "ticker":    ticker,
                        "side":      "SELL",
                        "qty":       round(pos["qty"], 6),
                        "price":     price,
                        "pnl":       round(pnl, 4),
                        "reason":    signal.notes,
                    })
                    del positions[ticker]

            # ── Mark-to-market equity ─────────────────────────────────────────
            pos_value = sum(
                positions[t]["qty"] * current_prices.get(t, positions[t]["entry_price"])
                for t in positions
            )
            equity_pts.append({"timestamp": ts, "equity": capital + pos_value})

        # ── Close any remaining open positions at final bar ───────────────────
        for ticker, pos in positions.items():
            price = current_prices.get(ticker, pos["entry_price"])
            pnl   = (price - pos["entry_price"]) * pos["qty"]
            capital += pos["qty"] * price
            trades.append({
                "timestamp": all_ts[-1],
                "ticker":    ticker,
                "side":      "SELL",
                "qty":       round(pos["qty"], 6),
                "price":     price,
                "pnl":       round(pnl, 4),
                "reason":    "End of backtest",
            })

        # ── Build equity curve ────────────────────────────────────────────────
        eq_df = pd.DataFrame(equity_pts)
        if len(eq_df) > 0:
            eq_series = eq_df.set_index("timestamp")["equity"]
        else:
            eq_series = pd.Series([initial_capital])

        sell_trades = [t for t in trades if t["side"] == "SELL" and t["pnl"] is not None]

        return BacktestResult(
            trades       = trades,
            equity_curve = eq_series,
            total_return = (eq_series.iloc[-1] - initial_capital) / initial_capital,
            sharpe_ratio = self._sharpe(eq_series),
            max_drawdown = self._max_drawdown(eq_series),
            win_rate     = self._win_rate(sell_trades),
            total_trades = len(sell_trades),
        )

    # ── Metrics helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _sharpe(equity: pd.Series) -> float:
        if len(equity) < 2:
            return 0.0
        returns = equity.pct_change().dropna()
        std = returns.std()
        return float(returns.mean() / std * (252 ** 0.5)) if std != 0 else 0.0

    @staticmethod
    def _max_drawdown(equity: pd.Series) -> float:
        if len(equity) < 2:
            return 0.0
        roll_max  = equity.cummax()
        drawdown  = (equity - roll_max) / roll_max
        return float(drawdown.min())

    @staticmethod
    def _win_rate(sell_trades: list[dict]) -> float:
        if not sell_trades:
            return 0.0
        wins = sum(1 for t in sell_trades if (t["pnl"] or 0) > 0)
        return wins / len(sell_trades)
