"""
Live market data feed via Alpaca WebSocket — tick level.

Subscribes to individual trade events (not 1-minute bars) so the
algorithm sees every executed transaction in real time.  This matches
the latency of the reference single-file platform.

Auto-reconnect: if the stream drops for any reason, the thread waits
RECONNECT_DELAY seconds and re-establishes the connection automatically,
without any user intervention needed.

Emits:
  tick_received(ticker, dict)  — one dict per trade: {timestamp, price, size}
  feed_error(str)              — non-fatal error messages
  feed_connected()             — fires each time the stream comes up
"""
from __future__ import annotations
import time
from PyQt6.QtCore import QThread, pyqtSignal

RECONNECT_DELAY = 3   # seconds to wait before reconnecting after a drop


class DataFeedThread(QThread):
    """Background thread hosting the Alpaca streaming WebSocket (trade ticks)."""

    tick_received  = pyqtSignal(str, dict)   # ticker, {timestamp, price, size}
    feed_error     = pyqtSignal(str)          # non-fatal error string
    feed_connected = pyqtSignal()             # fires on each successful connect

    def __init__(
        self,
        api_key:    str,
        secret_key: str,
        tickers:    list[str],
        parent=None,
    ):
        super().__init__(parent)
        self.api_key    = api_key
        self.secret_key = secret_key
        self.tickers    = list(tickers)
        self._stream    = None
        self._running   = True

    # ── Thread entry point ───────────────────────────────────────────────────

    def run(self) -> None:
        try:
            from alpaca.data.live import StockDataStream
        except ImportError:
            self.feed_error.emit(
                "alpaca-py is not installed. Run:  pip install alpaca-py"
            )
            return

        while self._running:
            try:
                self._stream = StockDataStream(self.api_key, self.secret_key)

                async def on_trade(trade) -> None:
                    self.tick_received.emit(
                        trade.symbol,
                        {
                            "timestamp": trade.timestamp,
                            "price":     float(trade.price),
                            "size":      int(trade.size),
                        },
                    )

                self._stream.subscribe_trades(on_trade, *self.tickers)
                self.feed_connected.emit()
                self._stream.run()   # blocks until the stream drops or stop() called

            except Exception as exc:
                if not self._running:
                    break
                self.feed_error.emit(
                    f"Stream error: {exc}. Reconnecting in {RECONNECT_DELAY}s…"
                )
                # Wait before retrying, but check _running each second
                for _ in range(RECONNECT_DELAY):
                    if not self._running:
                        return
                    time.sleep(1)

    # ── Stop ─────────────────────────────────────────────────────────────────

    def stop(self) -> None:
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:
                pass
        self.quit()
        self.wait(3_000)

