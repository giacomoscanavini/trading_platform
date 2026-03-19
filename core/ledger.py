"""
SQLite-backed transaction ledger.

Records every BUY and SELL event with full metadata:
algorithm, P&L, order ID, session type (LIVE / BACKTEST), and notes.
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3
from datetime import datetime, timezone
from typing import Optional
import pandas as pd
import config


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT    NOT NULL,
    ticker       TEXT    NOT NULL,
    side         TEXT    NOT NULL,
    qty          REAL    NOT NULL,
    price        REAL    NOT NULL,
    value        REAL    NOT NULL,
    algorithm    TEXT    NOT NULL,
    order_id     TEXT,
    pnl          REAL,
    notes        TEXT,
    session_type TEXT    NOT NULL DEFAULT 'LIVE'
)
"""


class Ledger:
    def __init__(self, db_path: str = config.DB_PATH):
        self.db_path = db_path
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(_CREATE_SQL)
            conn.commit()

    # ── Write ─────────────────────────────────────────────────────────────────

    def record_trade(
        self,
        ticker:       str,
        side:         str,
        qty:          float,
        price:        float,
        algorithm:    str,
        order_id:     Optional[str] = None,
        pnl:          Optional[float] = None,
        notes:        str = "",
        session_type: str = "LIVE",
    ) -> None:
        ts = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO trades
                    (timestamp, ticker, side, qty, price, value,
                     algorithm, order_id, pnl, notes, session_type)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (ts, ticker, side, qty, price, qty * price,
                 algorithm, order_id, pnl, notes, session_type),
            )
            conn.commit()

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_all_trades(self) -> pd.DataFrame:
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql(
                "SELECT * FROM trades ORDER BY timestamp DESC", conn
            )

    def get_trades_by_session(self, session_type: str) -> pd.DataFrame:
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql(
                "SELECT * FROM trades WHERE session_type = ? ORDER BY timestamp DESC",
                conn, params=(session_type,),
            )

    def clear_session(self, session_type: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM trades WHERE session_type = ?", (session_type,)
            )
            conn.commit()
