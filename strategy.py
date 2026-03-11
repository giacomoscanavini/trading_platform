"""Strategy interfaces and implementations."""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from typing import Optional

from models import PricePoint, SignalEvent
from stats import RollingWindow


class Strategy(ABC):
    """Abstract strategy interface."""

    @abstractmethod
    def warmup(self, prices: list[float]) -> None:
        """Warm the strategy with historical data before the live stream starts."""

    @abstractmethod
    def on_price(self, point: PricePoint) -> tuple[Optional[SignalEvent], dict[str, float]]:
        """Consume one price update and possibly return a signal."""


class MeanReversionThreeSigmaStrategy(Strategy):
    """Mean-reversion strategy with separate re-arm logic for buy and sell signals."""

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