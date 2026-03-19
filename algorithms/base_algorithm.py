"""
Abstract base class for all trading algorithms.

Every algorithm must:
  1. Inherit from BaseAlgorithm
  2. Set class-level NAME, DESCRIPTION, PARAMETERS
  3. Implement on_bar(bars) → list[Signal]
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import pandas as pd


class SignalType(Enum):
    BUY  = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class Signal:
    ticker:      str
    signal_type: SignalType
    price:       float
    size_pct:    Optional[float] = None   # override default position size
    notes:       str = ""


class BaseAlgorithm(ABC):
    """
    Base class for all trading algorithms.

    ``on_bar`` receives a snapshot dict of DataFrames — one per
    tracked ticker — up to and including the current bar.
    It returns a (possibly empty) list of Signal objects.

    The algorithm is free to maintain internal state across calls.
    Call ``reset()`` before replaying historical data in a backtest.
    """

    NAME        = "Base Algorithm"
    DESCRIPTION = "Override this in your subclass."
    PARAMETERS: dict = {}   # {param_name: default_value}

    def __init__(self, params: Optional[dict] = None):
        self.params = {**self.PARAMETERS, **(params or {})}

    @abstractmethod
    def on_bar(self, bars: dict[str, pd.DataFrame]) -> list[Signal]:
        """
        Called on every new bar (live or simulated).

        Parameters
        ----------
        bars : dict[ticker → DataFrame]
            Each DataFrame has columns:
            [timestamp, open, high, low, close, volume]
            Rows are sorted ascending; latest bar is ``.iloc[-1]``.

        Returns
        -------
        list[Signal]  — may be empty.
        """

    def reset(self) -> None:
        """Reset any internal state. Called before each backtest run."""

    def get_param(self, name: str):
        return self.params.get(name, self.PARAMETERS.get(name))
