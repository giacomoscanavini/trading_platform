"""Utility helpers"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime

import numpy as np


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime"""

    return datetime.now(tz=UTC)


def dt_to_epoch_seconds(value: datetime) -> float:
    """Convert a timezone-aware datetime to epoch seconds"""

    return value.timestamp()


def parse_alpaca_timestamp(value: str) -> datetime:
    """Parse Alpaca timestamps into timezone-aware UTC datetimes"""

    normalized = value.replace("Z", "+00:00")

    if "." in normalized:
        head, tail = normalized.split(".", maxsplit=1)
        frac, suffix = tail.split("+", maxsplit=1)
        frac = frac[:6].ljust(6, "0")
        normalized = f"{head}.{frac}+{suffix}"

    return datetime.fromisoformat(normalized).astimezone(UTC)


def safe_float_std(values: deque[float]) -> float:
    """Compute a population standard deviation for a deque of floats"""

    if len(values) < 2:
        return 0.0

    array = np.asarray(values, dtype=np.float64)
    return float(np.std(array, ddof=0))