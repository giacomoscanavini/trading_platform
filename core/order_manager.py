"""
Order manager.

Translates algorithm Signal objects into real Alpaca market orders,
tracks open positions, and enforces stop-loss rules.

Thread-safety note: all mutations to ``_positions`` are protected by
a threading.Lock, but this module is designed to be called from the
main Qt thread (receiving signals from DataFeedThread).
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import threading
from dataclasses import dataclass
from typing import Optional

from algorithms.base_algorithm import Signal, SignalType
import config


@dataclass
class ManagedPosition:
    ticker:       str
    qty:          float
    entry_price:  float
    stop_price:   float
    algorithm:    str


class OrderManager:
    def __init__(self, alpaca_client, ledger, portfolio):
        self.client    = alpaca_client
        self.ledger    = ledger
        self.portfolio = portfolio
        self._positions: dict[str, ManagedPosition] = {}
        self._lock      = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    def execute_signal(
        self,
        signal:       Signal,
        algo_name:    str,
        stop_loss_pct: float = config.DEFAULT_STOP_LOSS_PCT,
    ) -> None:
        """Execute a signal from an algorithm."""
        try:
            if signal.signal_type == SignalType.BUY:
                self._open_position(signal, algo_name, stop_loss_pct)
            elif signal.signal_type == SignalType.SELL:
                self._close_position(signal, algo_name)
        except Exception as exc:
            print(f"[OrderManager] Error executing {signal}: {exc}")

    def check_stop_losses(self, current_prices: dict[str, float]) -> None:
        """
        Called on every new bar. Closes any position whose price has
        fallen through its stop-loss level.
        """
        with self._lock:
            for ticker, pos in list(self._positions.items()):
                price = current_prices.get(ticker)
                if price is not None and price <= pos.stop_price:
                    sl_signal = Signal(
                        ticker      = ticker,
                        signal_type = SignalType.SELL,
                        price       = price,
                        notes       = "Stop loss triggered",
                    )
                    self._close_position(
                        sl_signal,
                        pos.algorithm + " [STOP]",
                        _already_locked=True,
                    )

    def get_open_positions(self) -> list[ManagedPosition]:
        with self._lock:
            return list(self._positions.values())

    # ── Internal ─────────────────────────────────────────────────────────────

    def _open_position(
        self,
        signal:       Signal,
        algo_name:    str,
        stop_loss_pct: float,
    ) -> None:
        ticker = signal.ticker
        with self._lock:
            if ticker in self._positions:
                return   # already long

            # Size the trade
            acct       = self.client.get_account()
            port_value = acct["portfolio_value"]
            size_pct   = signal.size_pct or config.DEFAULT_POSITION_SIZE_PCT
            qty        = (port_value * size_pct) / signal.price
            qty        = round(qty, 6)

            if qty < 0.001:
                return

            order = self.client.place_market_order(ticker, qty, "BUY")

            self._positions[ticker] = ManagedPosition(
                ticker      = ticker,
                qty         = qty,
                entry_price = signal.price,
                stop_price  = signal.price * (1 - stop_loss_pct),
                algorithm   = algo_name,
            )
            self.portfolio.update_position(ticker, qty, signal.price)
            self.ledger.record_trade(
                ticker     = ticker,
                side       = "BUY",
                qty        = qty,
                price      = signal.price,
                algorithm  = algo_name,
                order_id   = order.get("order_id"),
                notes      = signal.notes,
            )

    def _close_position(
        self,
        signal:           Signal,
        algo_name:        str,
        _already_locked:  bool = False,
    ) -> None:
        ticker = signal.ticker

        def _do_close():
            pos = self._positions.get(ticker)
            if pos is None:
                return

            order = self.client.place_market_order(ticker, pos.qty, "SELL")
            pnl   = (signal.price - pos.entry_price) * pos.qty

            self.ledger.record_trade(
                ticker    = ticker,
                side      = "SELL",
                qty       = pos.qty,
                price     = signal.price,
                algorithm = algo_name,
                order_id  = order.get("order_id"),
                pnl       = round(pnl, 4),
                notes     = signal.notes,
            )
            self.portfolio.close_position(ticker, signal.price)
            del self._positions[ticker]

        if _already_locked:
            _do_close()
        else:
            with self._lock:
                _do_close()
