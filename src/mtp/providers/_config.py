from __future__ import annotations

import math


def positive_timeout_seconds(value: float, *, field: str = "timeout_seconds") -> float:
    """Normalize a finite, positive provider request timeout."""
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(f"{field} must be a finite number greater than zero")
    return timeout


def optional_positive_int(value: int | None, *, field: str) -> int | None:
    """Validate optional token limits without accepting bool as an integer."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer or None")
    return value
