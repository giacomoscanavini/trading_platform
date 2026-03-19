"""
In-memory portfolio tracker.

Maintains the current mark-to-market value of all open positions
and accumulates realised P&L.  Thread-safe.
"""
from __future__ import annotations
import threading
from dataclasses import dataclass


@dataclass
class PortfolioPosition:
    ticker:        str
    qty:           float
    entry_price:   float
    current_price: float = 0.0

    @property
    def unrealized_pnl(self) -> float:
        return (self.current_price - self.entry_price) * self.qty

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        return (self.current_price - self.entry_price) / self.entry_price * 100.0


class Portfolio:
    def __init__(self):
        self._positions:   dict[str, PortfolioPosition] = {}
        self._realized_pnl: float = 0.0
        self._lock = threading.Lock()

    # ── Mutations ─────────────────────────────────────────────────────────────

    def update_position(self, ticker: str, qty: float, entry_price: float) -> None:
        with self._lock:
            self._positions[ticker] = PortfolioPosition(
                ticker        = ticker,
                qty           = qty,
                entry_price   = entry_price,
                current_price = entry_price,
            )

    def update_price(self, ticker: str, price: float) -> None:
        with self._lock:
            if ticker in self._positions:
                self._positions[ticker].current_price = price

    def close_position(self, ticker: str, close_price: float) -> None:
        with self._lock:
            if ticker in self._positions:
                pos = self._positions.pop(ticker)
                self._realized_pnl += (close_price - pos.entry_price) * pos.qty

    # ── Reads ─────────────────────────────────────────────────────────────────

    def get_positions(self) -> list[PortfolioPosition]:
        with self._lock:
            return list(self._positions.values())

    def get_summary(self) -> dict:
        with self._lock:
            unrealized = sum(p.unrealized_pnl for p in self._positions.values())
            return {
                "positions":     len(self._positions),
                "unrealized_pnl": round(unrealized, 4),
                "realized_pnl":   round(self._realized_pnl, 4),
                "total_pnl":      round(unrealized + self._realized_pnl, 4),
            }

    def reset(self) -> None:
        with self._lock:
            self._positions.clear()
            self._realized_pnl = 0.0
