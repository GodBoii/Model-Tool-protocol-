from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp.cli.tui_model_context import format_context_usage, get_context_window


def test_xiaomi_pro_context_window_is_one_million() -> None:
    size, source = get_context_window("xiaomi", "mimo-v2.5-pro")

    assert size == 1_000_000
    assert source == "model_exact"


def test_xiaomi_flash_context_window_is_256k() -> None:
    size, source = get_context_window("xiaomi", "mimo-v2-flash")

    assert size == 256_000
    assert source == "model_exact"


def test_xiaomi_context_usage_formats_against_correct_window() -> None:
    formatted, pct = format_context_usage(146_550, "xiaomi", "mimo-v2.5-pro")

    assert formatted == "146,550/1,000,000"
    assert 14.6 < pct < 14.7
