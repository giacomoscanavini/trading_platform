"""
BacktestWidget — configuration form + results dashboard.

Layout (QSplitter, vertical):
  top:     configuration form (tickers, algo, source, dates, capital)
  bottom:  results (metric cards, equity curve chart, trades table)
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pandas as pd
from datetime import datetime
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QPushButton, QLabel, QLineEdit,
    QComboBox, QDateEdit, QSplitter, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox,
    QProgressBar,
)
from PyQt6.QtCore import Qt, QDate, QThread, pyqtSignal
from PyQt6.QtGui  import QColor
import pyqtgraph as pg

from core.backtester import Backtester, BacktestResult


# ── Background worker thread ──────────────────────────────────────────────────

class _BacktestWorker(QThread):
    finished = pyqtSignal(object)   # BacktestResult
    error    = pyqtSignal(str)

    def __init__(self, backtester, algo, bars_data, run_kwargs):
        super().__init__()
        self.backtester  = backtester
        self.algo        = algo
        self.bars_data   = bars_data
        self.run_kwargs  = run_kwargs

    def run(self) -> None:
        try:
            result = self.backtester.run(self.algo, self.bars_data, **self.run_kwargs)
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


# ── Main widget ───────────────────────────────────────────────────────────────

class BacktestWidget(QWidget):
    def __init__(
        self,
        alpaca_client=None,
        algo_registry: dict | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._alpaca    = alpaca_client
        self._algo_reg  = algo_registry or {}
        self._backtester = Backtester(alpaca_client)
        self._worker: _BacktestWorker | None = None
        self._build_ui()

    # ── Public ────────────────────────────────────────────────────────────────

    def set_alpaca_client(self, client) -> None:
        self._alpaca     = client
        self._backtester = Backtester(client)

    def refresh_algos(self, registry: dict) -> None:
        self._algo_reg = registry
        current = self.algo_combo.currentText()
        self.algo_combo.clear()
        for name in registry:
            self.algo_combo.addItem(name)
        idx = self.algo_combo.findText(current)
        if idx >= 0:
            self.algo_combo.setCurrentIndex(idx)

    # ── Construction ──────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Vertical)
        outer.addWidget(splitter)

        splitter.addWidget(self._build_config_panel())
        splitter.addWidget(self._build_results_panel())
        splitter.setSizes([210, 700])

    def _build_config_panel(self) -> QWidget:
        box = QGroupBox("Backtest Configuration")
        box.setFixedHeight(210)
        g = QGridLayout(box)
        g.setSpacing(8)

        # Row 0: tickers / algo
        g.addWidget(QLabel("Tickers (comma-sep):"), 0, 0)
        self.ticker_input = QLineEdit("AAPL,MSFT")
        g.addWidget(self.ticker_input, 0, 1)

        g.addWidget(QLabel("Algorithm:"), 0, 2)
        self.algo_combo = QComboBox()
        for name in self._algo_reg:
            self.algo_combo.addItem(name)
        g.addWidget(self.algo_combo, 0, 3)

        # Row 1: source / interval
        g.addWidget(QLabel("Data Source:"), 1, 0)
        self.source_combo = QComboBox()
        self.source_combo.addItems(["yfinance", "Alpaca"])
        g.addWidget(self.source_combo, 1, 1)

        g.addWidget(QLabel("Interval:"), 1, 2)
        self.interval_combo = QComboBox()
        self.interval_combo.addItems(["1d", "1h", "30m", "15m", "5m"])
        g.addWidget(self.interval_combo, 1, 3)

        # Row 2: dates
        g.addWidget(QLabel("Start:"), 2, 0)
        self.start_date = QDateEdit(QDate.currentDate().addMonths(-3))
        self.start_date.setCalendarPopup(True)
        g.addWidget(self.start_date, 2, 1)

        g.addWidget(QLabel("End:"), 2, 2)
        self.end_date = QDateEdit(QDate.currentDate())
        self.end_date.setCalendarPopup(True)
        g.addWidget(self.end_date, 2, 3)

        # Row 3: capital / position size
        g.addWidget(QLabel("Initial Capital $:"), 3, 0)
        self.capital_input = QLineEdit("100000")
        g.addWidget(self.capital_input, 3, 1)

        g.addWidget(QLabel("Position Size %:"), 3, 2)
        self.pos_size_input = QLineEdit("10")
        g.addWidget(self.pos_size_input, 3, 3)

        # Row 4: stop loss / run
        g.addWidget(QLabel("Stop Loss %:"), 4, 0)
        self.sl_input = QLineEdit("2")
        g.addWidget(self.sl_input, 4, 1)

        self.run_btn = QPushButton("▶  Run Backtest")
        self.run_btn.setStyleSheet(
            "background:#1b5e20; color:#69f0ae; font-weight:bold; "
            "padding:7px 20px; border-radius:4px;"
        )
        self.run_btn.clicked.connect(self._run)
        g.addWidget(self.run_btn, 4, 2)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        g.addWidget(self.progress, 4, 3)

        return box

    def _build_results_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(6)

        # ── Metric cards ───────────────────────────────────────────────────────
        metrics_row = QHBoxLayout()
        self._metrics: dict[str, QLabel] = {}
        for title in ["Total Return", "Sharpe Ratio", "Max Drawdown", "Win Rate", "# Trades"]:
            card = QGroupBox(title)
            card.setStyleSheet("QGroupBox { color:#888; font-size:11px; }")
            cl = QVBoxLayout(card)
            lbl = QLabel("—")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("font-size:20px; font-weight:bold; color:#4fc3f7;")
            cl.addWidget(lbl)
            self._metrics[title] = lbl
            metrics_row.addWidget(card)
        lay.addLayout(metrics_row)

        # ── Equity curve ───────────────────────────────────────────────────────
        self.eq_plot = pg.PlotWidget()
        self.eq_plot.setBackground("#1a1a2e")
        self.eq_plot.showGrid(x=True, y=True, alpha=0.12)
        self.eq_plot.setLabel("left",   "Equity ($)", color="#888")
        self.eq_plot.setLabel("bottom", "Bar #",      color="#888")
        self._eq_curve = self.eq_plot.plot(pen=pg.mkPen("#4fc3f7", width=2))
        self._eq_fill  = pg.FillBetweenItem(
            self._eq_curve,
            self.eq_plot.plot([0], [0]),
            brush=pg.mkBrush("#4fc3f720"),
        )
        self.eq_plot.addItem(self._eq_fill)
        lay.addWidget(self.eq_plot)

        # ── Trades table ───────────────────────────────────────────────────────
        self.trade_table = QTableWidget()
        self.trade_table.setColumnCount(7)
        self.trade_table.setHorizontalHeaderLabels(
            ["Timestamp", "Ticker", "Side", "Qty", "Price", "P&L", "Reason"]
        )
        self.trade_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.trade_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.trade_table.setMaximumHeight(180)
        self.trade_table.setStyleSheet(
            "QHeaderView::section { background:#16213e; color:#aaa; border:none; }"
        )
        lay.addWidget(self.trade_table)

        return w

    # ── Run ───────────────────────────────────────────────────────────────────

    def _run(self) -> None:
        tickers = [t.strip().upper() for t in self.ticker_input.text().split(",") if t.strip()]
        if not tickers:
            QMessageBox.warning(self, "Input Error", "Enter at least one ticker symbol.")
            return

        algo_name = self.algo_combo.currentText()
        algo_cls  = self._algo_reg.get(algo_name)
        if not algo_cls:
            QMessageBox.warning(self, "No Algorithm", "Select a valid algorithm.")
            return

        algo     = algo_cls()
        start_dt = datetime.combine(
            self.start_date.date().toPyDate(), datetime.min.time()
        )
        end_dt   = datetime.combine(
            self.end_date.date().toPyDate(), datetime.min.time()
        )

        source   = self.source_combo.currentText()
        interval = self.interval_combo.currentText()

        try:
            capital  = float(self.capital_input.text())
            pos_size = float(self.pos_size_input.text()) / 100.0
            sl_pct   = float(self.sl_input.text()) / 100.0
        except ValueError:
            QMessageBox.warning(self, "Input Error", "Invalid numeric parameter.")
            return

        # ── Fetch data ────────────────────────────────────────────────────────
        try:
            if source == "yfinance":
                bars_data = self._backtester.fetch_data_yfinance(
                    tickers, start_dt, end_dt, interval
                )
            else:
                if not self._alpaca or not self._alpaca.is_connected():
                    QMessageBox.warning(self, "Not Connected", "Connect to Alpaca first.")
                    return
                tf_map = {
                    "1d": "1Day", "1h": "1Hour",
                    "30m": "30Min", "15m": "15Min", "5m": "5Min",
                }
                bars_data = self._backtester.fetch_data_alpaca(
                    tickers, start_dt, end_dt, tf_map.get(interval, "1Day")
                )
        except Exception as exc:
            QMessageBox.critical(self, "Data Error", str(exc))
            return

        # Check we got something
        if all(df.empty for df in bars_data.values()):
            QMessageBox.warning(
                self, "No Data",
                "No data returned. Try a wider date range or different tickers."
            )
            return

        self.run_btn.setEnabled(False)
        self.progress.setVisible(True)

        self._worker = _BacktestWorker(
            self._backtester, algo, bars_data,
            {
                "initial_capital":   capital,
                "position_size_pct": pos_size,
                "stop_loss_pct":     sl_pct,
            },
        )
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    # ── Results ───────────────────────────────────────────────────────────────

    def _on_done(self, result: BacktestResult) -> None:
        self.run_btn.setEnabled(True)
        self.progress.setVisible(False)

        # Metric cards
        ret_color = "#69f0ae" if result.total_return >= 0 else "#ff5252"
        dd_color  = "#ff5252"

        self._metrics["Total Return"].setText(f"{result.total_return * 100:+.2f}%")
        self._metrics["Total Return"].setStyleSheet(
            f"font-size:20px; font-weight:bold; color:{ret_color};"
        )
        self._metrics["Sharpe Ratio"].setText(f"{result.sharpe_ratio:.2f}")
        self._metrics["Max Drawdown"].setText(f"{result.max_drawdown * 100:.2f}%")
        self._metrics["Max Drawdown"].setStyleSheet(
            f"font-size:20px; font-weight:bold; color:{dd_color};"
        )
        self._metrics["Win Rate"].setText(f"{result.win_rate * 100:.1f}%")
        self._metrics["# Trades"].setText(str(result.total_trades))

        # Equity curve
        if result.equity_curve is not None and len(result.equity_curve) > 1:
            eq = result.equity_curve.values.astype(float)
            x  = np.arange(len(eq), dtype=float)
            self._eq_curve.setData(x, eq)
            base_curve = self.eq_plot.plot(x, np.full_like(eq, eq[0]), pen=None)
            self._eq_fill.setCurves(self._eq_curve, base_curve)

        # Trades table
        trades = result.trades
        self.trade_table.setRowCount(len(trades))
        for i, t in enumerate(trades):
            pnl_val = t.get("pnl")
            cells = [
                str(t.get("timestamp", ""))[:19],
                str(t.get("ticker", "")),
                str(t.get("side", "")),
                f"{float(t.get('qty', 0)):.4f}",
                f"${float(t.get('price', 0)):,.2f}",
                (f"${float(pnl_val):+,.2f}" if pnl_val is not None else "—"),
                str(t.get("reason", "")),
            ]
            for j, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if j == 2:
                    item.setForeground(QColor("#69f0ae" if text == "BUY" else "#ff5252"))
                if j == 5 and pnl_val is not None:
                    item.setForeground(QColor("#69f0ae" if float(pnl_val) >= 0 else "#ff5252"))
                self.trade_table.setItem(i, j, item)

    def _on_error(self, msg: str) -> None:
        self.run_btn.setEnabled(True)
        self.progress.setVisible(False)
        QMessageBox.critical(self, "Backtest Error", msg)
