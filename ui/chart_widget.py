"""
ChartWidget — live price chart with buy/sell signal overlays.

Uses pyqtgraph for GPU-accelerated rendering.
Displays:
  • Close/trade price line
  • EMA-9 / EMA-21 overlays
  • Rolling mean line
  • Upper / lower ±σ band lines (dashed red)
  • Green ▲ buy markers
  • Red   ▼ sell markers
  • Current price + % change in header
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pandas as pd
import pyqtgraph as pg
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel
from PyQt6.QtCore    import Qt


class ChartWidget(QWidget):
    def __init__(self, ticker: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.ticker  = ticker
        self._buy_x:  list[float] = []
        self._buy_y:  list[float] = []
        self._sell_x: list[float] = []
        self._sell_y: list[float] = []
        self._first_close: float | None = None

        # Tick-level price buffer
        self._tick_prices: list[float] = []
        self._band_window: int = 120

        self._build_ui()

    # ── Construction ──────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 2)
        layout.setSpacing(2)

        # ── Header ────────────────────────────────────────────────────────────
        header = QWidget()
        header.setFixedHeight(30)
        header.setStyleSheet("background:#16213e; border-radius:4px;")
        hrow = QHBoxLayout(header)
        hrow.setContentsMargins(10, 0, 10, 0)

        self.ticker_lbl = QLabel(self.ticker)
        self.ticker_lbl.setStyleSheet(
            "font-size:13px; font-weight:bold; color:#4fc3f7; letter-spacing:1px;"
        )
        hrow.addWidget(self.ticker_lbl)

        legend = QLabel(
            "  <span style='color:#ffb74d'>─ EMA 9</span>"
            "  <span style='color:#ce93d8'>─ EMA 21</span>"
            "  <span style='color:#ffffff'>── Mean</span>"
            "  <span style='color:#ef5350'>┄ ±σ bands</span>"
            "  <span style='color:#00e676'>▲ BUY</span>"
            "  <span style='color:#ff1744'>▼ SELL</span>"
        )
        legend.setStyleSheet("font-size:11px;")
        hrow.addWidget(legend)
        hrow.addStretch()

        self.price_lbl = QLabel("—")
        self.price_lbl.setStyleSheet("font-size:15px; font-weight:bold; color:#e0e0e0;")
        hrow.addWidget(self.price_lbl)

        self.change_lbl = QLabel("")
        self.change_lbl.setStyleSheet("font-size:12px; padding-left:6px;")
        hrow.addWidget(self.change_lbl)
        layout.addWidget(header)

        # ── Plot ──────────────────────────────────────────────────────────────
        pg.setConfigOption("background", "#1a1a2e")
        pg.setConfigOption("foreground", "#555")

        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.12)
        self.plot.getAxis("left").setStyle(tickTextOffset=6)
        self.plot.getAxis("bottom").setStyle(showValues=False)
        self.plot.setMinimumHeight(220)
        layout.addWidget(self.plot)

        # Price line
        self._price_curve = self.plot.plot(pen=pg.mkPen("#4fc3f7", width=2))
        # EMA-9
        self._ema_fast = self.plot.plot(pen=pg.mkPen("#ffb74d", width=1))
        # EMA-21
        self._ema_slow = self.plot.plot(pen=pg.mkPen("#ce93d8", width=1))
        # Rolling mean
        self._mean_curve = self.plot.plot(pen=pg.mkPen("#ffffff", width=1))
        # Upper σ band (dashed red)
        self._upper_curve = self.plot.plot(
            pen=pg.mkPen("#ef5350", width=1,
                         style=pg.QtCore.Qt.PenStyle.DashLine)
        )
        # Lower σ band (dashed red)
        self._lower_curve = self.plot.plot(
            pen=pg.mkPen("#ef5350", width=1,
                         style=pg.QtCore.Qt.PenStyle.DashLine)
        )

        # Buy markers ▲
        self._buy_scatter = pg.ScatterPlotItem(
            symbol="t1", size=14,
            brush=pg.mkBrush("#00e676"),
            pen=pg.mkPen(None),
        )
        self.plot.addItem(self._buy_scatter)

        # Sell markers ▼
        self._sell_scatter = pg.ScatterPlotItem(
            symbol="t", size=14,
            brush=pg.mkBrush("#ff1744"),
            pen=pg.mkPen(None),
        )
        self.plot.addItem(self._sell_scatter)

    # ── Public API ────────────────────────────────────────────────────────────

    def update_data(self, df: pd.DataFrame) -> None:
        """Update from a bar DataFrame (backtest / bar mode)."""
        if df is None or len(df) == 0:
            return
        close = df["close"].values.astype(float)
        self._render(close)

    def update_tick(self, price: float) -> None:
        """Append a single trade tick and re-render (live tick mode)."""
        self._tick_prices.append(price)
        if len(self._tick_prices) > 2000:
            self._tick_prices = self._tick_prices[-2000:]
        self._render(np.array(self._tick_prices, dtype=float))

    def set_band_window(self, window: int) -> None:
        self._band_window = max(2, window)

    # ── Internal render ───────────────────────────────────────────────────────

    def _render(self, close: np.ndarray) -> None:
        x = np.arange(len(close), dtype=float)

        self._price_curve.setData(x, close)

        s = pd.Series(close)
        self._ema_fast.setData(x, s.ewm(span=9,  adjust=False).mean().values)
        self._ema_slow.setData(x, s.ewm(span=21, adjust=False).mean().values)

        # Rolling mean + ±σ bands
        w         = min(self._band_window, len(close))
        roll_mean = s.rolling(w, min_periods=1).mean().values
        roll_std  = s.rolling(w, min_periods=2).std().fillna(0).values
        thresh    = 2.0

        self._mean_curve.setData(x, roll_mean)
        self._upper_curve.setData(x, roll_mean + thresh * roll_std)
        self._lower_curve.setData(x, roll_mean - thresh * roll_std)

        # Header
        current = float(close[-1])
        if self._first_close is None:
            self._first_close = current

        chg_pct = (current - self._first_close) / self._first_close * 100
        color   = "#69f0ae" if chg_pct >= 0 else "#ff5252"
        sign    = "+" if chg_pct >= 0 else ""
        self.price_lbl.setText(f"${current:,.2f}")
        self.change_lbl.setText(
            f"<span style='color:{color}'>{sign}{chg_pct:.2f}%</span>"
        )

        # Scroll to last 300 points
        n = len(close)
        self.plot.setXRange(max(0.0, float(n - 300)), float(n), padding=0.02)

    def add_buy_marker(self, bar_index: int, price: float) -> None:
        self._buy_x.append(float(bar_index))
        self._buy_y.append(price * 0.997)
        self._buy_scatter.setData(x=self._buy_x, y=self._buy_y)

    def add_sell_marker(self, bar_index: int, price: float) -> None:
        self._sell_x.append(float(bar_index))
        self._sell_y.append(price * 1.003)
        self._sell_scatter.setData(x=self._sell_x, y=self._sell_y)

    def clear_markers(self) -> None:
        self._buy_x, self._buy_y   = [], []
        self._sell_x, self._sell_y = [], []
        self._buy_scatter.setData(x=[], y=[])
        self._sell_scatter.setData(x=[], y=[])
        self._first_close  = None
        self._tick_prices  = []
        self._mean_curve.setData([], [])
        self._upper_curve.setData([], [])
        self._lower_curve.setData([], [])
