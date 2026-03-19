"""
AlgoEditorWidget — in-platform Python code editor.

Features:
  • QPlainTextEdit with custom Python syntax highlighter
  • Pre-filled template inheriting from BaseAlgorithm
  • "Load Algorithm" button: dynamically imports the code,
    finds the BaseAlgorithm subclass, and emits algo_loaded(cls)
  • "Reset Template" button
  • Status / error output pane
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib.util
import re
import tempfile

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QPlainTextEdit,
)
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui  import (
    QSyntaxHighlighter, QTextCharFormat,
    QColor, QFont, QTextDocument,
)
from algorithms.base_algorithm import BaseAlgorithm


# ── Code template shown on first launch ──────────────────────────────────────

TEMPLATE = '''\
"""
Custom Trading Algorithm
────────────────────────
Inherit from BaseAlgorithm and implement on_bar().
Click ⚡ Load Algorithm to register it in the platform.
"""
import pandas as pd
import numpy as np
from algorithms.base_algorithm import BaseAlgorithm, Signal, SignalType


class MyCustomAlgorithm(BaseAlgorithm):
    NAME        = "My Custom Algorithm"
    DESCRIPTION = "Describe your strategy here."
    PARAMETERS  = {
        "window": 14,
    }

    def __init__(self, params=None):
        super().__init__(params)
        # Initialise any state variables here
        self._prev_signal: dict[str, SignalType] = {}

    def on_bar(self, bars: dict) -> list:
        """
        Called on every new bar for ALL tracked tickers.

        bars : dict[ticker → pd.DataFrame]
               Each DataFrame has columns:
               [timestamp, open, high, low, close, volume]
               Latest bar = bars[ticker].iloc[-1]
        """
        signals = []
        window  = self.get_param("window")

        for ticker, df in bars.items():
            if len(df) < window:
                continue

            close = df["close"].astype(float)

            # ── YOUR LOGIC HERE ─────────────────────────────────────────────
            mean  = close.rolling(window).mean().iloc[-1]
            price = close.iloc[-1]

            prev = self._prev_signal.get(ticker)

            if price > mean and prev != SignalType.BUY:
                signals.append(Signal(
                    ticker      = ticker,
                    signal_type = SignalType.BUY,
                    price       = price,
                    notes       = f"Price {price:.2f} > {window}-bar mean {mean:.2f}",
                ))
                self._prev_signal[ticker] = SignalType.BUY

            elif price < mean and prev == SignalType.BUY:
                signals.append(Signal(
                    ticker      = ticker,
                    signal_type = SignalType.SELL,
                    price       = price,
                    notes       = f"Price {price:.2f} < {window}-bar mean {mean:.2f}",
                ))
                self._prev_signal[ticker] = SignalType.SELL
            # ────────────────────────────────────────────────────────────────

        return signals

    def reset(self):
        """Called before each backtest run to clear state."""
        super().reset()
        self._prev_signal = {}
'''


# ── Syntax highlighter ────────────────────────────────────────────────────────

class _PythonHighlighter(QSyntaxHighlighter):
    def __init__(self, doc: QTextDocument):
        super().__init__(doc)
        self._rules: list[tuple[re.Pattern, QTextCharFormat]] = []

        def fmt(hex_color: str, bold: bool = False) -> QTextCharFormat:
            f = QTextCharFormat()
            f.setForeground(QColor(hex_color))
            if bold:
                f.setFontWeight(QFont.Weight.Bold)
            return f

        kw_fmt = fmt("#cc99cd", bold=True)
        for kw in (
            "def", "class", "import", "from", "return", "yield",
            "if", "elif", "else", "for", "while", "in", "not",
            "and", "or", "True", "False", "None", "self", "super",
            "pass", "break", "continue", "raise", "try", "except",
            "finally", "with", "as", "lambda", "assert", "del",
        ):
            self._rules.append((re.compile(rf"\b{kw}\b"), kw_fmt))

        self._rules += [
            (re.compile(r'#[^\n]*'),                    fmt("#6a9955")),   # comment
            (re.compile(r'""".*?"""', re.DOTALL),       fmt("#ce9178")),   # docstring
            (re.compile(r"'''.*?'''", re.DOTALL),       fmt("#ce9178")),   # docstring
            (re.compile(r'"[^"\n]*"'),                  fmt("#ce9178")),   # string
            (re.compile(r"'[^'\n]*'"),                  fmt("#ce9178")),   # string
            (re.compile(r"\b\d+\.?\d*([eE][+-]?\d+)?\b"), fmt("#b5cea8")),# number
            (re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b"),    fmt("#4ec9b0")),   # CONSTANT
            (re.compile(r"\b[A-Z][a-zA-Z0-9_]+\b"),    fmt("#4ec9b0")),   # ClassName
            (re.compile(r"\bdef\s+(\w+)"),              fmt("#dcdcaa")),   # function name
        ]

    def highlightBlock(self, text: str) -> None:
        for pattern, fmt in self._rules:
            for m in pattern.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)


# ── Widget ────────────────────────────────────────────────────────────────────

class AlgoEditorWidget(QWidget):
    """Emits ``algo_loaded(cls)`` when the user successfully loads an algo."""
    algo_loaded = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Info banner
        info = QLabel(
            "Write a class that inherits <b>BaseAlgorithm</b> and implements "
            "<b>on_bar(bars)</b>. "
            "Click <b>⚡ Load Algorithm</b> to register it and add it to the "
            "Algorithm dropdown."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#aaa; padding:4px; font-size:12px;")
        layout.addWidget(info)

        # Code editor
        self.editor = QPlainTextEdit()
        font = QFont("Consolas", 12)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.editor.setFont(font)
        self.editor.setStyleSheet(
            "background:#0d1117; color:#c9d1d9; "
            "border:1px solid #30363d; border-radius:4px;"
        )
        self.editor.setTabStopDistance(32)
        self.editor.setPlainText(TEMPLATE)
        self._hl = _PythonHighlighter(self.editor.document())
        layout.addWidget(self.editor)

        # Button row
        btn_row = QHBoxLayout()

        load_btn = QPushButton("⚡  Load Algorithm")
        load_btn.setStyleSheet(
            "background:#0f3460; color:#4fc3f7; font-weight:bold; "
            "padding:6px 16px; border-radius:4px;"
        )
        load_btn.clicked.connect(self._load_algorithm)
        btn_row.addWidget(load_btn)

        reset_btn = QPushButton("🔄  Reset Template")
        reset_btn.setStyleSheet("padding:6px 12px; border-radius:4px;")
        reset_btn.clicked.connect(lambda: self.editor.setPlainText(TEMPLATE))
        btn_row.addWidget(reset_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Output pane
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFixedHeight(90)
        self.output.setFont(QFont("Consolas", 11))
        self.output.setStyleSheet(
            "background:#0d1117; color:#69f0ae; border:1px solid #30363d; border-radius:4px;"
        )
        layout.addWidget(self.output)

    # ── Load ──────────────────────────────────────────────────────────────────

    def _load_algorithm(self) -> None:
        self.output.clear()
        code = self.editor.toPlainText()

        try:
            # Write to a temporary file and import it
            with tempfile.NamedTemporaryFile(
                suffix=".py", mode="w", delete=False, encoding="utf-8"
            ) as fh:
                fh.write(code)
                tmp = fh.name

            spec   = importlib.util.spec_from_file_location("_user_algo", tmp)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

        except SyntaxError as exc:
            self.output.appendPlainText(f"❌  Syntax error: {exc}")
            return
        except Exception as exc:
            self.output.appendPlainText(f"❌  Import error: {exc}")
            return
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

        # Find the first BaseAlgorithm subclass
        found = None
        for name, obj in vars(module).items():
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseAlgorithm)
                and obj is not BaseAlgorithm
            ):
                found = obj
                break

        if found is None:
            self.output.appendPlainText(
                "❌  No class inheriting BaseAlgorithm found in the editor."
            )
            return

        self.output.appendPlainText(
            f"✅  Loaded: {found.NAME!r}\n"
            f"    {found.DESCRIPTION}"
        )
        self.algo_loaded.emit(found)
