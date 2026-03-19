"""
Mean Reversion — Z-Score (configurable σ threshold)
──────────────────────────────────────────────────────
Computes a rolling z-score of the close/trade price:

    z = (price − rolling_mean) / rolling_std

• BUY  when z < −threshold  (price is unusually low → expect mean reversion)
• SELL when z >  threshold  (price is unusually high → expect mean reversion)

Default threshold = 2.0 (±2σ).  Set threshold=3.0 for the 3σ variant.

Re-arm logic:
  After a BUY fires, the buy signal is "disarmed" so it won't fire again
  while price stays below the band.  It only re-arms once z-score climbs
  back above -exit_z (default 0.5σ), meaning the price has meaningfully
  recovered toward the mean.  The same logic applies symmetrically to SELL.
  This prevents signal spam during a sustained deviation.
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from .base_algorithm import BaseAlgorithm, Signal, SignalType


class MeanReversionZScore(BaseAlgorithm):
    NAME        = "Mean Reversion (Z-Score)"
    DESCRIPTION = (
        "Buys when z-score < −threshold and sells when z-score > +threshold. "
        "Re-arm logic prevents repeated signals while price stays stretched. "
        "Set threshold=3.0 for the classic 3σ variant."
    )
    PARAMETERS = {
        "window":    20,
        "threshold": 2.0,   # entry threshold (σ)
        "exit_z":    0.5,   # re-arm when |z| drops back inside this value
        "min_bars":  30,
    }

    def __init__(self, params=None):
        super().__init__(params)
        # Re-arm state per ticker: True = ready to fire, False = waiting to re-arm
        self._buy_armed:  dict[str, bool] = {}
        self._sell_armed: dict[str, bool] = {}

    # ── Algorithm logic ──────────────────────────────────────────────────────

    def on_bar(self, bars: dict[str, pd.DataFrame]) -> list[Signal]:
        signals: list[Signal] = []

        window   = self.get_param("window")
        thresh   = self.get_param("threshold")
        exit_z   = self.get_param("exit_z")
        min_bars = self.get_param("min_bars")

        for ticker, df in bars.items():
            if len(df) < max(window, min_bars):
                continue

            close        = df["close"].astype(float)
            rolling_mean = close.rolling(window).mean()
            rolling_std  = close.rolling(window).std()

            if rolling_std.iloc[-1] == 0:
                continue

            z         = (close - rolling_mean) / rolling_std
            current_z = float(z.iloc[-1])

            # Default to armed on first sight of this ticker
            buy_armed  = self._buy_armed.get(ticker, True)
            sell_armed = self._sell_armed.get(ticker, True)

            # ── Re-arm checks ─────────────────────────────────────────────────
            # Buy re-arms when z climbs back above -exit_z (recovering from low)
            if current_z >= -exit_z:
                buy_armed = True
            # Sell re-arms when z falls back below +exit_z (recovering from high)
            if current_z <= exit_z:
                sell_armed = True

            # ── Signal checks ─────────────────────────────────────────────────
            price = float(close.iloc[-1])

            if current_z < -thresh and buy_armed:
                signals.append(Signal(
                    ticker      = ticker,
                    signal_type = SignalType.BUY,
                    price       = price,
                    notes       = f"z={current_z:.2f} < −{thresh}σ",
                ))
                buy_armed = False   # disarm until z recovers

            elif current_z > thresh and sell_armed:
                signals.append(Signal(
                    ticker      = ticker,
                    signal_type = SignalType.SELL,
                    price       = price,
                    notes       = f"z={current_z:.2f} > +{thresh}σ",
                ))
                sell_armed = False  # disarm until z recovers

            # Persist updated arm state
            self._buy_armed[ticker]  = buy_armed
            self._sell_armed[ticker] = sell_armed

        return signals

    def reset(self) -> None:
        super().reset()
        self._buy_armed  = {}
        self._sell_armed = {}
