"""Shared data models for the trading platform"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class PricePoint:
    """Normalized market-data point for one symbol"""

    symbol: str
    event_time: datetime
    price: float
    size: int
    source: str


@dataclass(slots=True)
class SignalEvent:
    """Trading signal emitted by a strategy"""

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
    """Paper-trade style order decision generated from a signal"""

    symbol: str
    event_time: datetime
    side: str
    price: float
    quantity: int
    allocation: float
    note: str


@dataclass(slots=True)
class PortfolioSnapshot:
    """Small snapshot of the paper portfolio state"""

    cash: float
    position_qty: int
    avg_cost: float
    last_price: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float


@dataclass(slots=True)
class ChartSignalMarker:
    """Signal marker used by the GUI chart"""

    event_time: datetime
    price: float
    side: str
    z_score: float
    quantity: int


@dataclass(slots=True)
class ChartState:
    """State sent from the runtime thread to the GUI thread"""

    symbol: str
    times: list[float] = field(default_factory=list)
    prices: list[float] = field(default_factory=list)
    means: list[float] = field(default_factory=list)
    upper_band: list[float] = field(default_factory=list)
    lower_band: list[float] = field(default_factory=list)
    markers: list[ChartSignalMarker] = field(default_factory=list)
    portfolio: Optional[PortfolioSnapshot] = None
    status_text: str = "Starting..."