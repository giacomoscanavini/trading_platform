"""
AlphaTrader — Main Application Window
══════════════════════════════════════
Tabs:  Live Trading  |  Backtest  |  Algo Editor  |  Ledger

Toolbar:
  • API key + secret inputs (persisted to QSettings)
  • Connect / Disconnect button
  • Ticker list (add / remove)
  • Algorithm selector
  • Position-size % and stop-loss %
  • Start / Stop trading button

Live trading tab:
  • Scrollable grid of ChartWidgets (one per ticker)
  • Portfolio summary panel (equity, cash, open positions, P&L)

Wire-up:
  DataFeedThread ──bar_received──► ChartWidget.update_data
                                 ► Portfolio.update_price
                                 ► OrderManager.check_stop_losses
                                 ► algo.on_bar ──signals──► OrderManager.execute_signal
                                                           ► Ledger.record_trade
  AlgoEditorWidget.algo_loaded ──► algo_registry ──► BacktestWidget.refresh_algos
"""
from __future__ import annotations
import sys
import os
import math
from typing import Optional

# ── Path fix — must happen before ANY project import ─────────────────────────
# os and sys are stdlib so they're always available. We use __file__ (always
# set to the absolute path of the running script) to locate trading_platform/
# and insert it at the front of sys.path so that `from core.x`, `from ui.x`,
# `from algorithms.x` and `import config` all resolve correctly regardless of
# which directory the user launches Python from.
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
# ─────────────────────────────────────────────────────────────────────────────

import pandas as pd

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QTabWidget, QSplitter,
    QVBoxLayout, QHBoxLayout, QGridLayout,
    QToolBar, QLabel, QLineEdit, QPushButton,
    QComboBox, QScrollArea, QFrame,
    QGroupBox, QDoubleSpinBox, QMessageBox,
    QSizePolicy,
)
from PyQt6.QtCore  import Qt, QSettings, pyqtSlot
from PyQt6.QtGui   import QIcon, QAction, QColor, QPalette

import config
from algorithms               import MovingAverageCrossover, MeanReversionZScore
from algorithms.base_algorithm import BaseAlgorithm, SignalType
from core.alpaca_client       import AlpacaClient
from core.data_feed           import DataFeedThread
from core.order_manager       import OrderManager
from core.ledger              import Ledger
from core.portfolio           import Portfolio
from core.backtester          import Backtester
from ui.chart_widget          import ChartWidget
from ui.ledger_widget         import LedgerWidget
from ui.algo_editor           import AlgoEditorWidget
from ui.backtest_widget       import BacktestWidget


# ── Colour palette (dark) ─────────────────────────────────────────────────────
DARK = {
    "bg":      "#1a1a2e",
    "panel":   "#16213e",
    "border":  "#0f3460",
    "accent":  "#4fc3f7",
    "text":    "#e0e0e0",
    "muted":   "#888888",
    "green":   "#69f0ae",
    "red":     "#ff5252",
}


def _set_dark_palette(app: QApplication) -> None:
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,          QColor(DARK["bg"]))
    pal.setColor(QPalette.ColorRole.WindowText,      QColor(DARK["text"]))
    pal.setColor(QPalette.ColorRole.Base,            QColor(DARK["panel"]))
    pal.setColor(QPalette.ColorRole.AlternateBase,   QColor(DARK["bg"]))
    pal.setColor(QPalette.ColorRole.ToolTipBase,     QColor(DARK["panel"]))
    pal.setColor(QPalette.ColorRole.ToolTipText,     QColor(DARK["text"]))
    pal.setColor(QPalette.ColorRole.Text,            QColor(DARK["text"]))
    pal.setColor(QPalette.ColorRole.Button,          QColor(DARK["panel"]))
    pal.setColor(QPalette.ColorRole.ButtonText,      QColor(DARK["text"]))
    pal.setColor(QPalette.ColorRole.Highlight,       QColor(DARK["border"]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(DARK["accent"]))
    app.setPalette(pal)
    app.setStyleSheet(f"""
        QMainWindow, QWidget  {{ background:{DARK['bg']}; color:{DARK['text']}; }}
        QTabWidget::pane      {{ border:1px solid {DARK['border']}; }}
        QTabBar::tab          {{ background:{DARK['panel']}; color:{DARK['muted']};
                                 padding:6px 16px; border-radius:3px 3px 0 0; }}
        QTabBar::tab:selected {{ background:{DARK['border']}; color:{DARK['accent']}; }}
        QGroupBox             {{ border:1px solid {DARK['border']};
                                 border-radius:4px; margin-top:8px;
                                 padding-top:4px; }}
        QGroupBox::title      {{ subcontrol-origin:margin; left:8px; color:{DARK['muted']}; }}
        QLineEdit, QComboBox, QDoubleSpinBox {{
            background:{DARK['panel']}; border:1px solid {DARK['border']};
            border-radius:3px; padding:3px 6px; color:{DARK['text']};
        }}
        QScrollBar:vertical   {{ background:{DARK['bg']}; width:8px; }}
        QScrollBar::handle:vertical {{ background:{DARK['border']}; border-radius:4px; }}
        QToolBar              {{ background:{DARK['panel']}; border:none; spacing:6px;
                                 padding:4px 8px; }}
        QSplitter::handle     {{ background:{DARK['border']}; }}
    """)


# ── Portfolio summary panel ───────────────────────────────────────────────────

class PortfolioPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(80)
        self.setStyleSheet(
            f"background:{DARK['panel']}; border-radius:6px; padding:4px;"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 4)

        self._labels: dict[str, QLabel] = {}
        for title in ["Equity", "Cash", "Open Positions", "Unrealised P&L", "Realised P&L"]:
            col = QVBoxLayout()
            hdr = QLabel(title)
            hdr.setStyleSheet(f"color:{DARK['muted']}; font-size:11px;")
            hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val = QLabel("—")
            val.setStyleSheet(
                f"color:{DARK['accent']}; font-size:16px; font-weight:bold;"
            )
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col.addWidget(hdr)
            col.addWidget(val)
            lay.addLayout(col)
            if title != "Realised P&L":
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.VLine)
                sep.setStyleSheet(f"color:{DARK['border']};")
                lay.addWidget(sep)
            self._labels[title] = val

    def update(
        self,
        equity:    Optional[float] = None,
        cash:      Optional[float] = None,
        positions: int = 0,
        unrealised: float = 0.0,
        realised:   float = 0.0,
    ) -> None:
        if equity is not None:
            self._labels["Equity"].setText(f"${equity:,.2f}")
        if cash is not None:
            self._labels["Cash"].setText(f"${cash:,.2f}")
        self._labels["Open Positions"].setText(str(positions))

        for key, val in [("Unrealised P&L", unrealised), ("Realised P&L", realised)]:
            color = DARK["green"] if val >= 0 else DARK["red"]
            self._labels[key].setText(f"${val:+,.2f}")
            self._labels[key].setStyleSheet(
                f"color:{color}; font-size:16px; font-weight:bold;"
            )


# ── Live trading tab ──────────────────────────────────────────────────────────

class LiveTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)

        self.portfolio_panel = PortfolioPanel()
        lay.addWidget(self.portfolio_panel)

        # Scrollable chart grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border:none;")
        self._grid_widget = QWidget()
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setSpacing(6)
        scroll.setWidget(self._grid_widget)
        lay.addWidget(scroll)

        self._charts: dict[str, ChartWidget] = {}

    def set_tickers(self, tickers: list[str]) -> None:
        # Remove charts that are no longer needed
        for t in list(self._charts.keys()):
            if t not in tickers:
                w = self._charts.pop(t)
                self._grid.removeWidget(w)
                w.deleteLater()

        # Add new charts
        for t in tickers:
            if t not in self._charts:
                self._charts[t] = ChartWidget(t)

        # Re-layout grid (up to 3 columns)
        cols = min(3, max(1, len(tickers)))
        for i, (t, chart) in enumerate(self._charts.items()):
            self._grid.addWidget(chart, i // cols, i % cols)

    def update_chart(self, ticker: str, df: pd.DataFrame) -> None:
        if ticker in self._charts:
            self._charts[ticker].update_data(df)

    def add_signal_marker(self, ticker: str, bar_idx: int, price: float, is_buy: bool) -> None:
        if ticker in self._charts:
            if is_buy:
                self._charts[ticker].add_buy_marker(bar_idx, price)
            else:
                self._charts[ticker].add_sell_marker(bar_idx, price)

    def get_chart(self, ticker: str) -> Optional[ChartWidget]:
        return self._charts.get(ticker)


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(config.APP_NAME)
        self.resize(1_400, 900)

        # ── Core objects ──────────────────────────────────────────────────────
        self._alpaca: Optional[AlpacaClient] = None
        self._ledger    = Ledger()
        self._portfolio = Portfolio()
        self._order_mgr: Optional[OrderManager] = None
        self._feed:      Optional[DataFeedThread] = None

        # Bar history per ticker (in-memory)
        self._bar_history: dict[str, pd.DataFrame] = {}

        # Algorithm registry: {display_name → class}
        self._algo_registry: dict[str, type] = {
            MovingAverageCrossover.NAME: MovingAverageCrossover,
            MeanReversionZScore.NAME:   MeanReversionZScore,
        }
        self._active_algo: Optional[BaseAlgorithm] = None

        # ── Settings persistence ───────────────────────────────────────────────
        self._settings = QSettings("AlphaTrader", "AlphaTrader")

        self._build_toolbar()
        self._build_central()
        self._restore_settings()

    # ── Toolbar ───────────────────────────────────────────────────────────────

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main Toolbar")
        tb.setMovable(False)
        tb.setFloatable(False)
        self.addToolBar(tb)

        # API credentials
        tb.addWidget(QLabel("API Key:"))
        self.key_input = QLineEdit()
        self.key_input.setFixedWidth(180)
        self.key_input.setPlaceholderText("Alpaca API key…")
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        tb.addWidget(self.key_input)

        tb.addWidget(QLabel("  Secret:"))
        self.secret_input = QLineEdit()
        self.secret_input.setFixedWidth(180)
        self.secret_input.setPlaceholderText("Alpaca secret…")
        self.secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        tb.addWidget(self.secret_input)

        self.connect_btn = QPushButton("🔌  Connect")
        self.connect_btn.setStyleSheet(
            f"background:{DARK['border']}; color:{DARK['accent']}; "
            "font-weight:bold; padding:5px 12px; border-radius:4px;"
        )
        self.connect_btn.clicked.connect(self._toggle_connect)
        tb.addWidget(self.connect_btn)

        tb.addSeparator()

        # Tickers
        tb.addWidget(QLabel("Tickers:"))
        self.ticker_input = QLineEdit("AAPL,MSFT,TSLA")
        self.ticker_input.setFixedWidth(200)
        self.ticker_input.setToolTip("Comma-separated list of ticker symbols")
        tb.addWidget(self.ticker_input)

        tb.addSeparator()

        # Algorithm
        tb.addWidget(QLabel("Algorithm:"))
        self.algo_combo = QComboBox()
        self.algo_combo.setMinimumWidth(200)
        for name in self._algo_registry:
            self.algo_combo.addItem(name)
        tb.addWidget(self.algo_combo)

        tb.addSeparator()

        # Risk params
        tb.addWidget(QLabel("Size %:"))
        self.pos_size_spin = QDoubleSpinBox()
        self.pos_size_spin.setRange(1.0, 100.0)
        self.pos_size_spin.setValue(10.0)
        self.pos_size_spin.setSuffix("%")
        self.pos_size_spin.setFixedWidth(80)
        tb.addWidget(self.pos_size_spin)

        tb.addWidget(QLabel("  Stop %:"))
        self.sl_spin = QDoubleSpinBox()
        self.sl_spin.setRange(0.1, 50.0)
        self.sl_spin.setValue(2.0)
        self.sl_spin.setSuffix("%")
        self.sl_spin.setFixedWidth(80)
        tb.addWidget(self.sl_spin)

        tb.addSeparator()

        # Start / Stop
        self.trade_btn = QPushButton("▶  Start Trading")
        self.trade_btn.setStyleSheet(
            f"background:#1b5e20; color:{DARK['green']}; "
            "font-weight:bold; padding:5px 14px; border-radius:4px;"
        )
        self.trade_btn.setEnabled(False)
        self.trade_btn.clicked.connect(self._toggle_trading)
        tb.addWidget(self.trade_btn)

        # Status label (right-aligned)
        spacer = QWidget()
        spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        tb.addWidget(spacer)

        self.status_lbl = QLabel("● Disconnected")
        self.status_lbl.setStyleSheet(f"color:{DARK['red']}; padding-right:8px;")
        tb.addWidget(self.status_lbl)

    # ── Central widget / tabs ─────────────────────────────────────────────────

    def _build_central(self) -> None:
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self.setCentralWidget(self._tabs)

        # Live Trading tab
        self._live_tab = LiveTab()
        self._tabs.addTab(self._live_tab, "📈  Live Trading")

        # Backtest tab
        self._bt_widget = BacktestWidget(
            alpaca_client=self._alpaca,
            algo_registry=self._algo_registry,
        )
        self._tabs.addTab(self._bt_widget, "🔬  Backtest")

        # Algo Editor tab
        self._editor = AlgoEditorWidget()
        self._editor.algo_loaded.connect(self._on_algo_loaded)
        self._tabs.addTab(self._editor, "🧠  Algo Editor")

        # Ledger tab
        self._ledger_widget = LedgerWidget(self._ledger)
        self._tabs.addTab(self._ledger_widget, "📋  Ledger")

    # ── Connection ────────────────────────────────────────────────────────────

    def _toggle_connect(self) -> None:
        if self._alpaca and self._alpaca.is_connected():
            self._disconnect()
        else:
            self._connect()

    def _connect(self) -> None:
        key    = self.key_input.text().strip()
        secret = self.secret_input.text().strip()
        if not key or not secret:
            QMessageBox.warning(self, "Credentials Required",
                                "Enter both API Key and Secret before connecting.")
            return

        try:
            self._alpaca = AlpacaClient(key, secret, paper=config.ALPACA_PAPER)
            self._alpaca.connect()
            acct = self._alpaca.get_account()
        except Exception as exc:
            QMessageBox.critical(self, "Connection Failed", str(exc))
            self._alpaca = None
            return

        self._order_mgr = OrderManager(self._alpaca, self._ledger, self._portfolio)
        self._bt_widget.set_alpaca_client(self._alpaca)

        equity = acct.get("equity", 0)
        cash   = acct.get("cash",   0)
        self._live_tab.portfolio_panel.update(equity=equity, cash=cash)

        self.connect_btn.setText("⏏  Disconnect")
        self.connect_btn.setStyleSheet(
            f"background:#4a1010; color:{DARK['red']}; "
            "font-weight:bold; padding:5px 12px; border-radius:4px;"
        )
        self.status_lbl.setText("● Connected (paper)" if config.ALPACA_PAPER else "● Connected (live)")
        self.status_lbl.setStyleSheet(f"color:{DARK['green']}; padding-right:8px;")
        self.trade_btn.setEnabled(True)
        self._save_settings()

    def _disconnect(self) -> None:
        self._stop_trading()
        self._alpaca     = None
        self._order_mgr  = None

        self.connect_btn.setText("🔌  Connect")
        self.connect_btn.setStyleSheet(
            f"background:{DARK['border']}; color:{DARK['accent']}; "
            "font-weight:bold; padding:5px 12px; border-radius:4px;"
        )
        self.status_lbl.setText("● Disconnected")
        self.status_lbl.setStyleSheet(f"color:{DARK['red']}; padding-right:8px;")
        self.trade_btn.setEnabled(False)

    # ── Trading ───────────────────────────────────────────────────────────────

    def _toggle_trading(self) -> None:
        if self._feed and self._feed.isRunning():
            self._stop_trading()
        else:
            self._start_trading()

    def _start_trading(self) -> None:
        tickers = [t.strip().upper() for t in self.ticker_input.text().split(",") if t.strip()]
        if not tickers:
            QMessageBox.warning(self, "No Tickers", "Enter at least one ticker.")
            return

        algo_name = self.algo_combo.currentText()
        algo_cls  = self._algo_registry.get(algo_name)
        if not algo_cls:
            QMessageBox.warning(self, "No Algorithm", "Select a valid algorithm.")
            return

        self._active_algo = algo_cls()
        # Tick history: one DataFrame per ticker, "close" column holds trade prices
        self._bar_history = {t: pd.DataFrame(columns=["timestamp", "close"])
                             for t in tickers}
        self._live_tab.set_tickers(tickers)

        self._feed = DataFeedThread(
            self.key_input.text().strip(),
            self.secret_input.text().strip(),
            tickers,
        )
        self._feed.tick_received.connect(self._on_tick)
        self._feed.feed_error.connect(
            lambda m: self.status_lbl.setText(f"⚠ {m}")
        )
        self._feed.feed_connected.connect(
            lambda: self.status_lbl.setText("● Streaming live ticks")
        )
        self._feed.start()

        self.trade_btn.setText("⏹  Stop Trading")
        self.trade_btn.setStyleSheet(
            f"background:#4a1010; color:{DARK['red']}; "
            "font-weight:bold; padding:5px 14px; border-radius:4px;"
        )

    def _stop_trading(self) -> None:
        if self._feed:
            self._feed.stop()
            self._feed = None
        self._active_algo = None

        self.trade_btn.setText("▶  Start Trading")
        self.trade_btn.setStyleSheet(
            f"background:#1b5e20; color:{DARK['green']}; "
            "font-weight:bold; padding:5px 14px; border-radius:4px;"
        )
        if self._alpaca and self._alpaca.is_connected():
            self.status_lbl.setText(
                "● Connected (paper)" if config.ALPACA_PAPER else "● Connected (live)"
            )

    # ── Tick handler ─────────────────────────────────────────────────────────

    @pyqtSlot(str, dict)
    def _on_tick(self, ticker: str, tick: dict) -> None:
        """Receive a single trade tick, update chart and run algorithm."""
        price = float(tick.get("price", 0))
        if price <= 0:
            return

        # 1. Append tick to history (stored as a close-price row)
        new_row = pd.DataFrame([{"timestamp": tick.get("timestamp"), "close": price}])
        hist    = self._bar_history.get(ticker, pd.DataFrame(columns=["timestamp", "close"]))
        hist    = pd.concat([hist, new_row], ignore_index=True)
        if len(hist) > config.MAX_BARS_DISPLAYED:
            hist = hist.iloc[-config.MAX_BARS_DISPLAYED:].reset_index(drop=True)
        self._bar_history[ticker] = hist

        # 2. Push tick directly to chart (avoids re-building full array each time)
        chart = self._live_tab.get_chart(ticker)
        if chart:
            chart.update_tick(price)

        # 3. Update portfolio mark-to-market
        self._portfolio.update_price(ticker, price)

        # 4. Check stop-losses on every tick
        if self._order_mgr:
            self._order_mgr.check_stop_losses({ticker: price})

        # 5. Run algorithm — pass the rolling price history for every ticker
        if self._active_algo and all(
            len(df) >= 1 for df in self._bar_history.values()
        ):
            try:
                signals = self._active_algo.on_bar(self._bar_history)
            except Exception as exc:
                print(f"[Algo] Error: {exc}")
                signals = []

            for sig in signals:
                tick_idx = len(self._bar_history.get(sig.ticker, [])) - 1
                is_buy   = sig.signal_type == SignalType.BUY
                self._live_tab.add_signal_marker(sig.ticker, tick_idx, sig.price, is_buy)

                if self._order_mgr:
                    try:
                        self._order_mgr.execute_signal(
                            sig,
                            self._active_algo.NAME,
                            stop_loss_pct=self.sl_spin.value() / 100.0,
                        )
                    except Exception as exc:
                        print(f"[OrderManager] {exc}")

        # 6. Refresh portfolio panel (throttled — only on every 10th tick to
        #    avoid hammering the Alpaca REST API)
        self._tick_count = getattr(self, "_tick_count", 0) + 1
        if self._tick_count % 10 == 0:
            self._refresh_portfolio_panel()

    def _refresh_portfolio_panel(self) -> None:
        summary = self._portfolio.get_summary()
        equity  = None
        cash    = None
        if self._alpaca and self._alpaca.is_connected():
            try:
                acct   = self._alpaca.get_account()
                equity = acct.get("equity")
                cash   = acct.get("cash")
            except Exception:
                pass
        self._live_tab.portfolio_panel.update(
            equity    = equity,
            cash      = cash,
            positions = summary["positions"],
            unrealised = summary["unrealized_pnl"],
            realised   = summary["realized_pnl"],
        )

    # ── Algo editor callback ──────────────────────────────────────────────────

    @pyqtSlot(object)
    def _on_algo_loaded(self, cls: type) -> None:
        """Register a user-defined algorithm loaded from the code editor."""
        self._algo_registry[cls.NAME] = cls

        # Refresh both dropdowns
        current = self.algo_combo.currentText()
        self.algo_combo.clear()
        for name in self._algo_registry:
            self.algo_combo.addItem(name)
        idx = self.algo_combo.findText(cls.NAME)
        if idx >= 0:
            self.algo_combo.setCurrentIndex(idx)

        self._bt_widget.refresh_algos(self._algo_registry)

        # Switch to Live Trading tab
        self._tabs.setCurrentIndex(0)

    # ── Settings ──────────────────────────────────────────────────────────────

    def _save_settings(self) -> None:
        self._settings.setValue("api_key",    self.key_input.text())
        self._settings.setValue("api_secret", self.secret_input.text())
        self._settings.setValue("tickers",    self.ticker_input.text())
        self._settings.setValue("algo",       self.algo_combo.currentText())
        self._settings.setValue("pos_size",   self.pos_size_spin.value())
        self._settings.setValue("stop_loss",  self.sl_spin.value())

    def _restore_settings(self) -> None:
        self.key_input.setText(    self._settings.value("api_key",    ""))
        self.secret_input.setText( self._settings.value("api_secret", ""))
        self.ticker_input.setText( self._settings.value("tickers",    "AAPL,MSFT,TSLA"))
        algo = self._settings.value("algo", "")
        idx  = self.algo_combo.findText(algo)
        if idx >= 0:
            self.algo_combo.setCurrentIndex(idx)
        self.pos_size_spin.setValue(float(self._settings.value("pos_size",  10.0)))
        self.sl_spin.setValue(      float(self._settings.value("stop_loss",  2.0)))

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        self._save_settings()
        self._stop_trading()
        super().closeEvent(event)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    _set_dark_palette(app)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
