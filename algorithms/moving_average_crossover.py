"""
Moving Average Crossover Algorithm
────────────────────────────────────
• BUY  when the fast EMA crosses **above** the slow EMA
• SELL when the fast EMA crosses **below** the slow EMA

The algorithm is stateless between bars except for remembering
the *previous* crossover direction so it only fires once per cross.
"""
from __future__ import annotations
import pandas as pd
from .base_algorithm import BaseAlgorithm, Signal, SignalType


class MovingAverageCrossover(BaseAlgorithm):
    NAME        = "Moving Average Crossover"
    DESCRIPTION = (
        "Generates BUY when the fast EMA crosses above the slow EMA "
        "and SELL when it crosses below. "
        "Default: EMA-9 vs EMA-21."
    )
    PARAMETERS = {
        "fast_period": 9,
        "slow_period": 21,
        "min_bars":    30,
    }

    def __init__(self, params=None):
        super().__init__(params)
        self._prev_state: dict[str, int] = {}   # ticker → {-1, 0, 1}

    # ── Algorithm logic ──────────────────────────────────────────────────────

    def on_bar(self, bars: dict[str, pd.DataFrame]) -> list[Signal]:
        signals: list[Signal] = []

        fast     = self.get_param("fast_period")
        slow     = self.get_param("slow_period")
        min_bars = self.get_param("min_bars")

        for ticker, df in bars.items():
            if len(df) < max(fast, slow, min_bars):
                continue

            close    = df["close"].astype(float)
            ema_fast = close.ewm(span=fast, adjust=False).mean()
            ema_slow = close.ewm(span=slow, adjust=False).mean()

            curr_state = 1 if ema_fast.iloc[-1] > ema_slow.iloc[-1] else -1
            prev_state = self._prev_state.get(ticker, curr_state)

            if prev_state == -1 and curr_state == 1:
                signals.append(Signal(
                    ticker      = ticker,
                    signal_type = SignalType.BUY,
                    price       = float(close.iloc[-1]),
                    notes       = f"EMA{fast} crossed above EMA{slow}",
                ))
            elif prev_state == 1 and curr_state == -1:
                signals.append(Signal(
                    ticker      = ticker,
                    signal_type = SignalType.SELL,
                    price       = float(close.iloc[-1]),
                    notes       = f"EMA{fast} crossed below EMA{slow}",
                ))

            self._prev_state[ticker] = curr_state

        return signals

    def reset(self) -> None:
        super().reset()
        self._prev_state = {}
