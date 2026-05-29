from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp.cli.tui_commands import parse_slash_command


def test_parse_slash_command_supports_thinking_without_arg() -> None:
    assert parse_slash_command("/thinking") == ("thinking", "")


def test_parse_slash_command_supports_details_toggle() -> None:
    assert parse_slash_command("/details on") == ("details", "on")
