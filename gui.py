"""PySide6 GUI."""

from __future__ import annotations

import argparse
import queue
from typing import Optional

import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from models import ChartState, PortfolioSnapshot
from utils import dt_to_epoch_seconds


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
        self.mean_curve = self.plot_widget.plot(
            name="Mean",
            pen=pg.mkPen(style=QtCore.Qt.PenStyle.DashLine),
        )
        self.upper_curve = self.plot_widget.plot(
            name="Upper 3σ",
            pen=pg.mkPen(style=QtCore.Qt.PenStyle.DotLine),
        )
        self.lower_curve = self.plot_widget.plot(
            name="Lower 3σ",
            pen=pg.mkPen(style=QtCore.Qt.PenStyle.DotLine),
        )

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