import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener.config import load_config  # noqa: E402
from screener.data import Bar  # noqa: E402

DATA = Path(__file__).parent / "data"


@pytest.fixture
def cfg():
    return load_config()


def make_bars(closes, start=date(2025, 1, 6), wick=0.01):
    """Daily bars from closes; highs/lows a small % around the open/close."""
    bars = []
    prev = closes[0]
    for i, c in enumerate(closes):
        hi = max(prev, c) * (1 + wick)
        lo = min(prev, c) * (1 - wick)
        bars.append(Bar(start + timedelta(days=i), prev, hi, lo, c, 1e6))
        prev = c
    return bars


def zigzag(n, drift, amp=0.06, period=10, start=100.0):
    """Trending series with regular swings: price * (1+drift)^i * (1 + amp*sin)."""
    return [start * (1 + drift) ** i * (1 + amp * math.sin(2 * math.pi * i / period)) for i in range(n)]
