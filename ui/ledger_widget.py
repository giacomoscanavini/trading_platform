"""
LedgerWidget — sortable table of all recorded trades.

Reads from the SQLite Ledger and renders rows with colour-coded
Side (BUY=green, SELL=red) and P&L columns.
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QHeaderView, QComboBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui  import QColor
from core.ledger  import Ledger


_COLUMNS = [
    "Timestamp", "Ticker", "Side", "Qty",
    "Price", "Value", "Algorithm", "P&L", "Notes", "Session",
]


class LedgerWidget(QWidget):
    def __init__(self, ledger: Ledger, parent: QWidget | None = None):
        super().__init__(parent)
        self.ledger = ledger
        self._build_ui()
        self.refresh()

    # ── Construction ──────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ── Toolbar ───────────────────────────────────────────────────────────
        toolbar = QHBoxLayout()

        toolbar.addWidget(QLabel("Filter:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All", "LIVE", "BACKTEST"])
        self.filter_combo.currentTextChanged.connect(self.refresh)
        toolbar.addWidget(self.filter_combo)

        toolbar.addStretch()

        refresh_btn = QPushButton("🔄  Refresh")
        refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(refresh_btn)

        layout.addLayout(toolbar)

        # ── Table ─────────────────────────────────────────────────────────────
        self.table = QTableWidget()
        self.table.setColumnCount(len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setStyleSheet("""
            QTableWidget            { gridline-color: #0f3460; }
            QHeaderView::section    { background: #16213e; color: #aaa;
                                      padding: 4px; border: none; }
            QTableWidget::item:alternate { background: #1a1a2e; }
        """)
        layout.addWidget(self.table)

        # ── Summary bar ───────────────────────────────────────────────────────
        self.summary_lbl = QLabel("")
        self.summary_lbl.setStyleSheet("color:#aaa; font-size:12px; padding:2px 4px;")
        layout.addWidget(self.summary_lbl)

    # ── Data loading ──────────────────────────────────────────────────────────

    def refresh(self) -> None:
        sel = self.filter_combo.currentText()
        if sel == "All":
            df = self.ledger.get_all_trades()
        else:
            df = self.ledger.get_trades_by_session(sel)

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(df))

        for i, (_, row) in enumerate(df.iterrows()):
            pnl_val = row.get("pnl")
            cells = [
                str(row.get("timestamp", ""))[:19],
                str(row.get("ticker",    "")),
                str(row.get("side",      "")),
                f"{float(row.get('qty',   0)):.4f}",
                f"${float(row.get('price', 0)):,.2f}",
                f"${float(row.get('value', 0)):,.2f}",
                str(row.get("algorithm",  "")),
                (f"${float(pnl_val):+,.2f}" if pnl_val is not None else "—"),
                str(row.get("notes",      "")),
                str(row.get("session_type", "")),
            ]
            for j, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                if j == 2:   # Side column
                    color = "#69f0ae" if text == "BUY" else "#ff5252"
                    item.setForeground(QColor(color))

                if j == 7 and pnl_val is not None:   # P&L column
                    color = "#69f0ae" if float(pnl_val) >= 0 else "#ff5252"
                    item.setForeground(QColor(color))

                self.table.setItem(i, j, item)

        self.table.setSortingEnabled(True)
        self._update_summary(df)

    # ── Summary ───────────────────────────────────────────────────────────────

    def _update_summary(self, df: pd.DataFrame) -> None:
        if df.empty:
            self.summary_lbl.setText("No trades recorded.")
            return

        sells     = df[df["side"] == "SELL"]
        total_pnl = df["pnl"].fillna(0).sum()
        n_trades  = len(sells)
        wins      = len(sells[sells["pnl"] > 0]) if n_trades else 0
        win_rate  = wins / n_trades * 100 if n_trades else 0.0
        color     = "#69f0ae" if total_pnl >= 0 else "#ff5252"

        self.summary_lbl.setText(
            f"Rows: {len(df)}  |  "
            f"Closed trades: {n_trades}  |  "
            f"Win rate: {win_rate:.1f}%  |  "
            f"<span style='color:{color}'>Realised P&L: ${total_pnl:+,.2f}</span>"
        )
