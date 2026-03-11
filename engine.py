"""Runtime engine."""

from __future__ import annotations

import argparse
import asyncio
import queue

import numpy as np

from market_data import AlpacaMarketDataClient
from models import ChartSignalMarker, ChartState
from portfolio import PaperPortfolio
from sizing import FixedFractionSizer, FractionalKellyProxySizer, PositionSizer
from strategy import build_strategy
from utils import dt_to_epoch_seconds


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

    async def on_price(self, point) -> None:
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
            except Exception as exc:
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