from __future__ import annotations

import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp.cli.tui import _extract_codex_tool_signal, _parse_codex_json_events


class TUIEventParsingTests(unittest.TestCase):
    def test_extract_codex_tool_signal_from_item_started(self) -> None:
        event = {
            "type": "item.started",
            "item": {
                "type": "function_call",
                "name": "calculator.add",
                "summary": "Need arithmetic before final answer",
            },
        }
        name, reasoning = _extract_codex_tool_signal(event, "item.started")
        self.assertEqual(name, "calculator.add")
        self.assertEqual(reasoning, "Need arithmetic before final answer")

    def test_parse_codex_json_events_includes_tool_reasoning_summary(self) -> None:
        lines = [
            json.dumps(
                {
                    "type": "item.started",
                    "item": {
                        "type": "function_call",
                        "name": "calculator.add",
                        "summary": "Compute the math part first",
                    },
                }
            ),
            json.dumps({"type": "response.output_text.delta", "delta": "Done"}),
        ]
        final_text, tool_events, warnings, _usage = _parse_codex_json_events("\n".join(lines), "gpt-5.3-codex")
        self.assertEqual(final_text, "Done")
        self.assertEqual(warnings, [])
        self.assertTrue(tool_events)
        self.assertIn("calculator.add: Compute the math part first", tool_events)


if __name__ == "__main__":
    unittest.main()

