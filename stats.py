"""Rolling statistical helpers"""

from __future__ import annotations

from collections import deque

import numpy as np

from utils import safe_float_std


class RollingWindow:
    """Fixed-size rolling window for mean and standard deviation statistics"""

    def __init__(self, window_size: int) -> None:
        if window_size < 2:
            raise ValueError("window_size must be at least 2")

        self.window_size = window_size
        self.values: deque[float] = deque(maxlen=window_size)

    def append(self, value: float) -> None:
        """Append a new observation to the rolling window"""

        self.values.append(float(value))

    @property
    def ready(self) -> bool:
        """Return True when enough data exists to evaluate the strategy"""

        return len(self.values) >= self.window_size

    @property
    def mean(self) -> float:
        """Return the rolling mean"""

        if not self.values:
            return 0.0

        return float(np.mean(np.asarray(self.values, dtype=np.float64)))

    @property
    def stdev(self) -> float:
        """Return the rolling standard deviation"""

        return safe_float_std(self.values)