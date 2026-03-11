"""Position sizing rules."""

from __future__ import annotations

from abc import ABC, abstractmethod

from models import OrderDecision, PortfolioSnapshot, SignalEvent


class PositionSizer(ABC):
    """Abstract base class for position sizing rules"""

    @abstractmethod
    def size_order(
        self,
        signal: SignalEvent,
        portfolio: PortfolioSnapshot,
        entry_z: float,
    ) -> OrderDecision:
        """Return the order decision for the given signal"""


class FixedFractionSizer(PositionSizer):
    """Allocate a fixed fraction of current equity to each BUY signal"""

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
            note = "Sell existing position quantity"
        else:
            budget = portfolio.equity * self.allocation
            quantity = int(budget // signal.price)
            allocation = self.allocation
            note = "Fixed-fraction entry sizing"

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
    """Capped fractional-Kelly proxy

    This is intentionally conservative
    It converts z-score magnitude into a crude probability edge estimate and then 
    applies a fractional Kelly multiplier and an explicit cap
    This is not a true Kelly implementation because a true Kelly bet
    requires a measured edge and payoff ratio from real backtests or live stats
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
                note="Exit existing position quantity",
            )

        excess_sigma = max(0.0, abs(signal.z_score) - entry_z)
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
            "safer early experiments"
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