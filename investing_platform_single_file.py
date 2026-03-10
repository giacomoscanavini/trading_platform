"""Single-file investing platform MVP.

This script provides:
- Live stock trade ingestion from Alpaca's market-data WebSocket stream
- Historical minute bars to seed the chart and the rolling mean / sigma window
- A swappable strategy interface
- A 3-sigma mean-reversion strategy with buy / sell markers on the chart
- Configurable position sizing (fixed fraction or a capped fractional-Kelly proxy)
- A paper-trading style portfolio tracker
- A PySide6 + pyqtgraph live chart UI

Notes:
- This version is intentionally stock-first and aimed at Alpaca's free IEX feed.
- It does not place real orders. It generates signals and updates a paper portfolio.
- The Kelly-based sizing path is a proxy only. Treat it as an experiment, not gospel.

Environment variables:
- APCA_API_KEY_ID
- APCA_API_SECRET_KEY

Install:
    pip install PySide6 pyqtgraph numpy websockets uvloop

Run:
    python investing_platform_single_file.py --symbol AAPL
    python investing_platform_single_file.py --symbol FAKEPACA --feed test
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import queue
import signal
import sys
import threading
import time
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets
from websockets.asyncio.client import connect

try:
    import uvloop
except ImportError:  # pragma: no cover - optional dependency
    uvloop = None


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PricePoint:
    """Normalized market-data point for one symbol."""

    symbol: str
    event_time: datetime
    price: float
    size: int
    source: str


@dataclass(slots=True)
class SignalEvent:
    """Trading signal emitted by a strategy."""

    symbol: str
    event_time: datetime
    side: str
    price: float
    z_score: float
    mean: float
    stdev: float
    reason: str


@dataclass(slots=True)
class OrderDecision:
    """Paper-trade style order decision generated from a signal."""

    symbol: str
    event_time: datetime
    side: str
    price: float
    quantity: int
    allocation: float
    note: str


@dataclass(slots=True)
class PortfolioSnapshot:
    """Small snapshot of the paper portfolio state."""

    cash: float
    position_qty: int
    avg_cost: float
    last_price: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float


@dataclass(slots=True)
class ChartSignalMarker:
    """Signal marker used by the GUI chart."""

    event_time: datetime
    price: float
    side: str
    z_score: float
    quantity: int


@dataclass(slots=True)
class ChartState:
    """State sent from the runtime thread to the GUI thread."""

    symbol: str
    times: list[float] = field(default_factory=list)
    prices: list[float] = field(default_factory=list)
    means: list[float] = field(default_factory=list)
    upper_band: list[float] = field(default_factory=list)
    lower_band: list[float] = field(default_factory=list)
    markers: list[ChartSignalMarker] = field(default_factory=list)
    portfolio: Optional[PortfolioSnapshot] = None
    status_text: str = "Starting..."


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""

    return datetime.now(tz=UTC)


def dt_to_epoch_seconds(value: datetime) -> float:
    """Convert a timezone-aware datetime to epoch seconds."""

    return value.timestamp()


def parse_alpaca_timestamp(value: str) -> datetime:
    """Parse Alpaca timestamps into timezone-aware UTC datetimes."""

    # Alpaca timestamps arrive like: 2026-03-10T14:30:01.123456789Z
    normalized = value.replace("Z", "+00:00")

    # Python's built-in parser cannot always digest nanosecond precision cleanly.
    if "." in normalized:
        head, tail = normalized.split(".", maxsplit=1)
        frac, suffix = tail.split("+", maxsplit=1)
        frac = frac[:6].ljust(6, "0")
        normalized = f"{head}.{frac}+{suffix}"

    return datetime.fromisoformat(normalized).astimezone(UTC)


def safe_float_std(values: deque[float]) -> float:
    """Compute a population standard deviation for a deque of floats."""

    if len(values) < 2:
        return 0.0

    array = np.asarray(values, dtype=np.float64)
    return float(np.std(array, ddof=0))


# ---------------------------------------------------------------------------
# Rolling statistics
# ---------------------------------------------------------------------------


class RollingWindow:
    """Fixed-size rolling window for mean / standard deviation statistics."""

    def __init__(self, window_size: int) -> None:
        if window_size < 2:
            raise ValueError("window_size must be at least 2")

        self.window_size = window_size
        self.values: deque[float] = deque(maxlen=window_size)

    def append(self, value: float) -> None:
        """Append a new observation to the rolling window."""

        self.values.append(float(value))

    @property
    def ready(self) -> bool:
        """Return True when enough data exists to evaluate the strategy."""

        return len(self.values) >= self.window_size

    @property
    def mean(self) -> float:
        """Return the rolling mean."""

        if not self.values:
            return 0.0
        return float(np.mean(np.asarray(self.values, dtype=np.float64)))

    @property
    def stdev(self) -> float:
        """Return the rolling standard deviation."""

        return safe_float_std(self.values)


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------


class PositionSizer(ABC):
    """Abstract base class for position sizing rules."""

    @abstractmethod
    def size_order(
        self,
        signal: SignalEvent,
        portfolio: PortfolioSnapshot,
        entry_z: float,
    ) -> OrderDecision:
        """Return the order decision for the given signal."""


class FixedFractionSizer(PositionSizer):
    """Allocate a fixed fraction of current equity to each BUY signal."""

    def __init__(self, allocation: float = 0.02) -> None:
        if not 0 < allocation <= 1:
            raise ValueError("allocation must be in (0, 1]")
        self.allocation = allocation

    def size_order(
        self,
        signal: SignalEvent,
        portfolio: PortfolioSnapshot,
        entry_z: float,
    ) -> OrderDecision:
        if signal.side == "SELL":
            quantity = max(0, portfolio.position_qty)
            allocation = 0.0
            note = "Sell existing position quantity."
        else:
            budget = portfolio.equity * self.allocation
            quantity = int(budget // signal.price)
            allocation = self.allocation
            note = "Fixed-fraction entry sizing."

        return OrderDecision(
            symbol=signal.symbol,
            event_time=signal.event_time,
            side=signal.side,
            price=signal.price,
            quantity=quantity,
            allocation=allocation,
            note=note,
        )


class FractionalKellyProxySizer(PositionSizer):
    """Capped fractional-Kelly proxy.

    This is intentionally conservative. It converts z-score magnitude into a crude
    probability edge estimate and then applies a fractional Kelly multiplier and an
    explicit cap. This is *not* a true Kelly implementation because a true Kelly bet
    requires a measured edge and payoff ratio from real backtests or live stats.
    """

    def __init__(
        self,
        fractional_kelly: float = 0.25,
        max_allocation: float = 0.10,
        payoff_ratio: float = 1.0,
    ) -> None:
        if not 0 < fractional_kelly <= 1:
            raise ValueError("fractional_kelly must be in (0, 1]")
        if not 0 < max_allocation <= 1:
            raise ValueError("max_allocation must be in (0, 1]")
        if payoff_ratio <= 0:
            raise ValueError("payoff_ratio must be positive")

        self.fractional_kelly = fractional_kelly
        self.max_allocation = max_allocation
        self.payoff_ratio = payoff_ratio

    def size_order(
        self,
        signal: SignalEvent,
        portfolio: PortfolioSnapshot,
        entry_z: float,
    ) -> OrderDecision:
        if signal.side == "SELL":
            quantity = max(0, portfolio.position_qty)
            return OrderDecision(
                symbol=signal.symbol,
                event_time=signal.event_time,
                side=signal.side,
                price=signal.price,
                quantity=quantity,
                allocation=0.0,
                note="Exit existing position quantity.",
            )

        excess_sigma = max(0.0, abs(signal.z_score) - entry_z)

        # Map the strength of the excursion into a cautious edge estimate.
        # The range is deliberately narrow to avoid absurd sizing.
        win_probability = min(0.60, 0.50 + 0.03 * excess_sigma)
        lose_probability = 1.0 - win_probability
        raw_kelly = (
            (self.payoff_ratio * win_probability - lose_probability)
            / self.payoff_ratio
        )
        raw_kelly = max(0.0, raw_kelly)
        allocation = min(self.max_allocation, raw_kelly * self.fractional_kelly)

        budget = portfolio.equity * allocation
        quantity = int(budget // signal.price)

        note = (
            "Fractional-Kelly proxy entry sizing. Use fixed-fraction sizing for "
            "safer early experiments."
        )

        return OrderDecision(
            symbol=signal.symbol,
            event_time=signal.event_time,
            side=signal.side,
            price=signal.price,
            quantity=quantity,
            allocation=allocation,
            note=note,
        )


# ---------------------------------------------------------------------------
# Strategy plug-ins
# ---------------------------------------------------------------------------


class Strategy(ABC):
    """Abstract strategy interface."""

    @abstractmethod
    def warmup(self, prices: list[float]) -> None:
        """Warm the strategy with historical data before the live stream starts."""

    @abstractmethod
    def on_price(self, point: PricePoint) -> tuple[Optional[SignalEvent], dict[str, float]]:
        """Consume one price update and possibly return a signal."""


class MeanReversionThreeSigmaStrategy(Strategy):
    """Mean-reversion strategy with separate re-arm logic for buy and sell signals.

    BUY when price <= mean - entry_z * stdev
    SELL when price >= mean + entry_z * stdev

    The strategy rearms only when the z-score moves back inside +/- exit_z.
    That prevents repeated signals while the market stays stretched.
    """

    def __init__(self, window: int = 120, entry_z: float = 3.0, exit_z: float = 0.5) -> None:
        if window < 2:
            raise ValueError("window must be at least 2")
        if exit_z < 0:
            raise ValueError("exit_z must be non-negative")
        if entry_z <= exit_z:
            raise ValueError("entry_z must be larger than exit_z")

        self.window = RollingWindow(window)
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.buy_armed = True
        self.sell_armed = True

    def warmup(self, prices: list[float]) -> None:
        for price in prices[-self.window.window_size :]:
            self.window.append(price)

    def on_price(self, point: PricePoint) -> tuple[Optional[SignalEvent], dict[str, float]]:
        self.window.append(point.price)

        if not self.window.ready:
            return None, {
                "mean": 0.0,
                "stdev": 0.0,
                "upper": 0.0,
                "lower": 0.0,
                "z_score": 0.0,
            }

        mean = self.window.mean
        stdev = self.window.stdev

        if stdev <= 0:
            return None, {
                "mean": mean,
                "stdev": stdev,
                "upper": mean,
                "lower": mean,
                "z_score": 0.0,
            }

        z_score = (point.price - mean) / stdev
        upper = mean + self.entry_z * stdev
        lower = mean - self.entry_z * stdev

        if z_score >= -self.exit_z:
            self.buy_armed = True
        if z_score <= self.exit_z:
            self.sell_armed = True

        signal: Optional[SignalEvent] = None

        if z_score <= -self.entry_z and self.buy_armed:
            signal = SignalEvent(
                symbol=point.symbol,
                event_time=point.event_time,
                side="BUY",
                price=point.price,
                z_score=z_score,
                mean=mean,
                stdev=stdev,
                reason=f"Price is {abs(z_score):.2f} sigmas below the rolling mean.",
            )
            self.buy_armed = False
        elif z_score >= self.entry_z and self.sell_armed:
            signal = SignalEvent(
                symbol=point.symbol,
                event_time=point.event_time,
                side="SELL",
                price=point.price,
                z_score=z_score,
                mean=mean,
                stdev=stdev,
                reason=f"Price is {abs(z_score):.2f} sigmas above the rolling mean.",
            )
            self.sell_armed = False

        return signal, {
            "mean": mean,
            "stdev": stdev,
            "upper": upper,
            "lower": lower,
            "z_score": z_score,
        }


def build_strategy(name: str, args: argparse.Namespace) -> Strategy:
    """Factory for swappable strategy blocks."""

    if name == "mean_reversion_3sigma":
        return MeanReversionThreeSigmaStrategy(
            window=args.mean_window,
            entry_z=args.entry_z,
            exit_z=args.exit_z,
        )

    raise ValueError(f"Unknown strategy: {name}")


# ---------------------------------------------------------------------------
# Paper portfolio
# ---------------------------------------------------------------------------


class PaperPortfolio:
    """Very small long-only paper portfolio used for signal evaluation."""

    def __init__(self, starting_cash: float = 100_000.0) -> None:
        self.cash = float(starting_cash)
        self.position_qty = 0
        self.avg_cost = 0.0
        self.last_price = 0.0
        self.realized_pnl = 0.0

    def mark_price(self, price: float) -> None:
        """Update the marked price for unrealized PnL calculations."""

        self.last_price = float(price)

    def execute(self, decision: OrderDecision) -> None:
        """Apply a paper-trade decision to the portfolio."""

        self.last_price = decision.price

        if decision.quantity <= 0:
            return

        if decision.side == "BUY":
            cost = decision.quantity * decision.price
            if cost > self.cash:
                # Clip to the largest affordable quantity.
                affordable_qty = int(self.cash // decision.price)
                if affordable_qty <= 0:
                    return
                cost = affordable_qty * decision.price
                filled_qty = affordable_qty
            else:
                filled_qty = decision.quantity

            new_total_qty = self.position_qty + filled_qty
            if new_total_qty <= 0:
                return

            if self.position_qty == 0:
                self.avg_cost = decision.price
            else:
                weighted_cost = self.avg_cost * self.position_qty + decision.price * filled_qty
                self.avg_cost = weighted_cost / new_total_qty

            self.position_qty = new_total_qty
            self.cash -= cost

        elif decision.side == "SELL":
            filled_qty = min(self.position_qty, decision.quantity)
            if filled_qty <= 0:
                return

            proceeds = filled_qty * decision.price
            self.cash += proceeds
            self.realized_pnl += (decision.price - self.avg_cost) * filled_qty
            self.position_qty -= filled_qty

            if self.position_qty == 0:
                self.avg_cost = 0.0

    def snapshot(self) -> PortfolioSnapshot:
        """Return the current portfolio snapshot."""

        market_value = self.position_qty * self.last_price
        equity = self.cash + market_value
        unrealized_pnl = (self.last_price - self.avg_cost) * self.position_qty

        return PortfolioSnapshot(
            cash=self.cash,
            position_qty=self.position_qty,
            avg_cost=self.avg_cost,
            last_price=self.last_price,
            equity=equity,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=unrealized_pnl,
        )


# ---------------------------------------------------------------------------
# Alpaca market-data client
# ---------------------------------------------------------------------------


class AlpacaMarketDataClient:
    """HTTP + WebSocket client for Alpaca market data."""

    BASE_HTTP_URL = "https://data.alpaca.markets"
    BASE_WS_URL = "wss://stream.data.alpaca.markets"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        feed: str,
        history_bars: int,
        use_test_stream: bool,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.symbol = symbol.upper()
        self.feed = feed
        self.history_bars = history_bars
        self.use_test_stream = use_test_stream

    def fetch_history(self) -> list[PricePoint]:
        """Fetch historical minute bars to seed the chart and strategy state."""

        end_dt = utc_now() - timedelta(minutes=16)
        start_dt = end_dt - timedelta(minutes=max(30, self.history_bars + 10))

        query = urllib.parse.urlencode(
            {
                "symbols": self.symbol,
                "timeframe": "1Min",
                "start": start_dt.isoformat().replace("+00:00", "Z"),
                "end": end_dt.isoformat().replace("+00:00", "Z"),
                "limit": str(self.history_bars),
                "feed": self.feed,
                "sort": "asc",
            }
        )
        url = f"{self.BASE_HTTP_URL}/v2/stocks/bars?{query}"

        request = urllib.request.Request(
            url,
            headers={
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.api_secret,
            },
        )

        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))

        bars = payload.get("bars", {}).get(self.symbol, [])
        history: list[PricePoint] = []

        for bar in bars:
            history.append(
                PricePoint(
                    symbol=self.symbol,
                    event_time=parse_alpaca_timestamp(bar["t"]),
                    price=float(bar["c"]),
                    size=int(bar.get("v", 0)),
                    source="history_bar",
                )
            )

        return history

    async def stream_trades(self, on_point) -> None:
        """Stream live trades over WebSocket and pass them to a callback."""

        if self.use_test_stream:
            ws_url = f"{self.BASE_WS_URL}/v2/test"
            subscribe_symbol = "FAKEPACA"
        else:
            ws_url = f"{self.BASE_WS_URL}/v2/{self.feed}"
            subscribe_symbol = self.symbol

        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
        }

        async with connect(ws_url, additional_headers=headers, max_queue=512) as websocket:
            # Welcome message.
            await websocket.recv()

            # Authenticate again via message as a second line of defense.
            await websocket.send(
                json.dumps(
                    {
                        "action": "auth",
                        "key": self.api_key,
                        "secret": self.api_secret,
                    }
                )
            )
            await websocket.recv()

            await websocket.send(
                json.dumps(
                    {
                        "action": "subscribe",
                        "trades": [subscribe_symbol],
                    }
                )
            )
            await websocket.recv()

            async for raw_message in websocket:
                messages = json.loads(raw_message)
                for message in messages:
                    if message.get("T") != "t":
                        continue

                    point = PricePoint(
                        symbol=message["S"],
                        event_time=parse_alpaca_timestamp(message["t"]),
                        price=float(message["p"]),
                        size=int(message.get("s", 0)),
                        source="live_trade",
                    )
                    await on_point(point)


# ---------------------------------------------------------------------------
# Runtime engine
# ---------------------------------------------------------------------------


class PlatformEngine:
    """Runtime engine that owns market data, strategy, and portfolio state."""

    def __init__(self, args: argparse.Namespace, gui_queue: queue.Queue[ChartState]) -> None:
        self.args = args
        self.gui_queue = gui_queue
        self.state = ChartState(symbol=args.symbol.upper())
        self.portfolio = PaperPortfolio(starting_cash=args.starting_cash)
        self.strategy = build_strategy(args.strategy, args)
        self.sizer = self._build_sizer(args)
        self.client = AlpacaMarketDataClient(
            api_key=args.api_key,
            api_secret=args.api_secret,
            symbol=args.symbol,
            feed=args.feed,
            history_bars=args.history_bars,
            use_test_stream=(args.feed == "test"),
        )
        self.stop_event = asyncio.Event()

    @staticmethod
    def _build_sizer(args: argparse.Namespace) -> PositionSizer:
        if args.position_sizing == "fixed_fraction":
            return FixedFractionSizer(allocation=args.fixed_allocation)

        if args.position_sizing == "fractional_kelly_proxy":
            return FractionalKellyProxySizer(
                fractional_kelly=args.fractional_kelly,
                max_allocation=args.max_allocation,
                payoff_ratio=args.payoff_ratio,
            )

        raise ValueError(f"Unknown position sizing method: {args.position_sizing}")

    async def initialize(self) -> None:
        """Warm up history and seed the GUI before live data begins."""

        history = self.client.fetch_history()
        self.strategy.warmup([point.price for point in history])

        for point in history:
            self.state.times.append(dt_to_epoch_seconds(point.event_time))
            self.state.prices.append(point.price)
            self.state.means.append(np.nan)
            self.state.upper_band.append(np.nan)
            self.state.lower_band.append(np.nan)
            self.portfolio.mark_price(point.price)

        self.state.portfolio = self.portfolio.snapshot()
        self.state.status_text = (
            f"Loaded {len(history)} historical bars. Waiting for live trades..."
        )
        self._publish_state()

    async def on_price(self, point: PricePoint) -> None:
        """Handle one live price point."""

        self.portfolio.mark_price(point.price)
        signal_event, stats = self.strategy.on_price(point)

        self.state.times.append(dt_to_epoch_seconds(point.event_time))
        self.state.prices.append(point.price)
        self.state.means.append(stats["mean"])
        self.state.upper_band.append(stats["upper"])
        self.state.lower_band.append(stats["lower"])

        if len(self.state.times) > self.args.max_points:
            self.state.times = self.state.times[-self.args.max_points :]
            self.state.prices = self.state.prices[-self.args.max_points :]
            self.state.means = self.state.means[-self.args.max_points :]
            self.state.upper_band = self.state.upper_band[-self.args.max_points :]
            self.state.lower_band = self.state.lower_band[-self.args.max_points :]

        if signal_event is not None:
            decision = self.sizer.size_order(
                signal=signal_event,
                portfolio=self.portfolio.snapshot(),
                entry_z=self.args.entry_z,
            )
            self.portfolio.execute(decision)

            self.state.markers.append(
                ChartSignalMarker(
                    event_time=signal_event.event_time,
                    price=signal_event.price,
                    side=signal_event.side,
                    z_score=signal_event.z_score,
                    quantity=decision.quantity,
                )
            )
            self.state.markers = self.state.markers[-200:]
            self.state.status_text = (
                f"{signal_event.side} signal | price={signal_event.price:.2f} | "
                f"z={signal_event.z_score:.2f} | qty={decision.quantity}"
            )
        else:
            self.state.status_text = (
                f"Live {point.symbol} | price={point.price:.2f} | "
                f"window={self.args.mean_window} | strategy={self.args.strategy}"
            )

        self.state.portfolio = self.portfolio.snapshot()
        self._publish_state()

    async def run(self) -> None:
        """Run the platform engine until interrupted."""

        await self.initialize()

        while not self.stop_event.is_set():
            try:
                await self.client.stream_trades(self.on_price)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - network dependent
                self.state.status_text = f"Stream error: {exc}. Reconnecting in 3 seconds..."
                self._publish_state()
                await asyncio.sleep(3)

    def stop(self) -> None:
        """Signal the runtime loop to stop."""

        self.stop_event.set()

    def _publish_state(self) -> None:
        """Publish the latest chart state to the GUI without blocking forever."""

        try:
            self.gui_queue.put_nowait(self._copy_state())
        except queue.Full:
            try:
                self.gui_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.gui_queue.put_nowait(self._copy_state())
            except queue.Full:
                pass

    def _copy_state(self) -> ChartState:
        """Make a detached copy so the GUI thread sees immutable snapshots."""

        return ChartState(
            symbol=self.state.symbol,
            times=list(self.state.times),
            prices=list(self.state.prices),
            means=list(self.state.means),
            upper_band=list(self.state.upper_band),
            lower_band=list(self.state.lower_band),
            markers=list(self.state.markers),
            portfolio=self.state.portfolio,
            status_text=self.state.status_text,
        )


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------


class MainWindow(QtWidgets.QMainWindow):
    """Main chart window."""

    def __init__(self, gui_queue: queue.Queue[ChartState], args: argparse.Namespace) -> None:
        super().__init__()
        self.gui_queue = gui_queue
        self.args = args
        self.latest_state: Optional[ChartState] = None

        self.setWindowTitle(f"Investing Platform MVP - {args.symbol.upper()}")
        self.resize(1300, 850)

        self._build_ui()
        self._build_timer()

    def _build_ui(self) -> None:
        """Construct the chart and side-panel widgets."""

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root_layout = QtWidgets.QVBoxLayout(central)

        top_layout = QtWidgets.QHBoxLayout()
        root_layout.addLayout(top_layout)

        self.status_label = QtWidgets.QLabel("Waiting for market data...")
        self.status_label.setWordWrap(True)
        top_layout.addWidget(self.status_label, stretch=2)

        self.portfolio_label = QtWidgets.QLabel("Portfolio: n/a")
        self.portfolio_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        self.portfolio_label.setWordWrap(True)
        top_layout.addWidget(self.portfolio_label, stretch=1)

        self.plot_widget = pg.PlotWidget()
        self.plot_widget.showGrid(x=True, y=True, alpha=0.25)
        self.plot_widget.addLegend()
        self.plot_widget.setLabel("left", "Price")
        self.plot_widget.setLabel("bottom", "Time")
        self.plot_widget.setMouseEnabled(x=True, y=False)
        root_layout.addWidget(self.plot_widget)

        axis = pg.DateAxisItem(orientation="bottom")
        self.plot_widget.setAxisItems({"bottom": axis})

        self.price_curve = self.plot_widget.plot(name="Price", pen=pg.mkPen(width=2))
        self.mean_curve = self.plot_widget.plot(name="Mean", pen=pg.mkPen(style=QtCore.Qt.PenStyle.DashLine))
        self.upper_curve = self.plot_widget.plot(name="Upper 3σ", pen=pg.mkPen(style=QtCore.Qt.PenStyle.DotLine))
        self.lower_curve = self.plot_widget.plot(name="Lower 3σ", pen=pg.mkPen(style=QtCore.Qt.PenStyle.DotLine))

        self.buy_scatter = pg.ScatterPlotItem(size=12, symbol="t")
        self.sell_scatter = pg.ScatterPlotItem(size=12, symbol="t1")
        self.plot_widget.addItem(self.buy_scatter)
        self.plot_widget.addItem(self.sell_scatter)

    def _build_timer(self) -> None:
        """Start the periodic GUI update timer."""

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._drain_queue)
        self.timer.start()

    @QtCore.Slot()
    def _drain_queue(self) -> None:
        """Consume the latest chart state and redraw the chart."""

        drained = False
        while True:
            try:
                self.latest_state = self.gui_queue.get_nowait()
                drained = True
            except queue.Empty:
                break

        if not drained or self.latest_state is None:
            return

        state = self.latest_state
        self.price_curve.setData(state.times, state.prices)
        self.mean_curve.setData(state.times, state.means)
        self.upper_curve.setData(state.times, state.upper_band)
        self.lower_curve.setData(state.times, state.lower_band)

        buy_points = []
        sell_points = []

        for marker in state.markers:
            tooltip = (
                f"{marker.side}\n"
                f"Price: {marker.price:.2f}\n"
                f"Z-score: {marker.z_score:.2f}\n"
                f"Qty: {marker.quantity}"
            )
            spot = {
                "pos": (dt_to_epoch_seconds(marker.event_time), marker.price),
                "data": tooltip,
                "brush": "g" if marker.side == "BUY" else "r",
            }
            if marker.side == "BUY":
                buy_points.append(spot)
            else:
                sell_points.append(spot)

        self.buy_scatter.setData(buy_points)
        self.sell_scatter.setData(sell_points)

        self.status_label.setText(state.status_text)
        self.portfolio_label.setText(self._format_portfolio(state.portfolio))

    @staticmethod
    def _format_portfolio(snapshot: Optional[PortfolioSnapshot]) -> str:
        """Format the portfolio panel text."""

        if snapshot is None:
            return "Portfolio: n/a"

        return (
            f"Cash: ${snapshot.cash:,.2f}\n"
            f"Position: {snapshot.position_qty} sh @ ${snapshot.avg_cost:,.2f}\n"
            f"Last price: ${snapshot.last_price:,.2f}\n"
            f"Equity: ${snapshot.equity:,.2f}\n"
            f"Realized PnL: ${snapshot.realized_pnl:,.2f}\n"
            f"Unrealized PnL: ${snapshot.unrealized_pnl:,.2f}"
        )


# ---------------------------------------------------------------------------
# Thread bootstrap
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser."""

    parser = argparse.ArgumentParser(description="Single-file investing platform MVP")
    parser.add_argument("--symbol", default="AAPL", help="Ticker symbol, e.g. AAPL")
    parser.add_argument(
        "--feed",
        default="iex",
        choices=["iex", "test"],
        help="Use 'iex' for Alpaca's free live IEX feed or 'test' for FAKEPACA.",
    )
    parser.add_argument(
        "--strategy",
        default="mean_reversion_3sigma",
        choices=["mean_reversion_3sigma"],
        help="Strategy block to run.",
    )
    parser.add_argument(
        "--position-sizing",
        default="fixed_fraction",
        choices=["fixed_fraction", "fractional_kelly_proxy"],
        help="Position sizing rule.",
    )
    parser.add_argument("--history-bars", type=int, default=240, help="Number of warm-start minute bars.")
    parser.add_argument("--mean-window", type=int, default=120, help="Rolling window length for mean and stdev.")
    parser.add_argument("--entry-z", type=float, default=3.0, help="Entry threshold in sigmas.")
    parser.add_argument("--exit-z", type=float, default=0.5, help="Re-arm threshold in sigmas.")
    parser.add_argument("--fixed-allocation", type=float, default=0.02, help="Fixed-fraction allocation.")
    parser.add_argument("--fractional-kelly", type=float, default=0.25, help="Fractional Kelly multiplier.")
    parser.add_argument("--max-allocation", type=float, default=0.10, help="Maximum Kelly-style allocation.")
    parser.add_argument("--payoff-ratio", type=float, default=1.0, help="Assumed payoff ratio for Kelly proxy.")
    parser.add_argument("--starting-cash", type=float, default=100_000.0, help="Paper portfolio starting cash.")
    parser.add_argument("--max-points", type=int, default=1500, help="Maximum points kept on the live chart.")

    return parser


def start_runtime_thread(
    args: argparse.Namespace,
    gui_queue: queue.Queue[ChartState],
) -> tuple[threading.Thread, PlatformEngine, asyncio.AbstractEventLoop]:
    """Start the asyncio runtime on a dedicated thread."""

    if uvloop is not None and sys.platform != "win32":
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

    loop_ready = threading.Event()
    holder: dict[str, object] = {}

    def runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        engine = PlatformEngine(args=args, gui_queue=gui_queue)

        holder["loop"] = loop
        holder["engine"] = engine
        loop_ready.set()

        task = loop.create_task(engine.run())
        try:
            loop.run_until_complete(task)
        finally:
            pending = asyncio.all_tasks(loop)
            for pending_task in pending:
                pending_task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    thread = threading.Thread(target=runner, name="platform-runtime", daemon=True)
    thread.start()
    loop_ready.wait()

    return thread, holder["engine"], holder["loop"]


def main() -> None:
    """Program entry point."""

    parser = build_arg_parser()
    args = parser.parse_args()

    args.api_key = os.getenv("APCA_API_KEY_ID", "")
    args.api_secret = os.getenv("APCA_API_SECRET_KEY", "")

    if not args.api_key or not args.api_secret:
        raise SystemExit(
            "Set APCA_API_KEY_ID and APCA_API_SECRET_KEY before running the app."
        )

    gui_queue: queue.Queue[ChartState] = queue.Queue(maxsize=8)
    runtime_thread, engine, loop = start_runtime_thread(args=args, gui_queue=gui_queue)

    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow(gui_queue=gui_queue, args=args)
    window.show()

    def shutdown() -> None:
        loop.call_soon_threadsafe(engine.stop)

    def handle_signal(*_) -> None:
        shutdown()
        app.quit()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    app.aboutToQuit.connect(shutdown)

    exit_code = app.exec()

    shutdown()
    runtime_thread.join(timeout=5)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
