from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp.cli.tui_mtp_backend import run_mtp_prompt
from mtp.cli.tui_theme import SYM_OK


class _StreamingAgent:
    def run_events(self, **kwargs):
        yield {
            "type": "llm_response",
            "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12, "reasoning_tokens": 1},
        }
        yield {"type": "reasoning_chunk", "chunk": "Need "}
        yield {"type": "reasoning_chunk", "chunk": "to inspect first."}
        yield {
            "type": "plan_received",
            "tool_call_source": "native_tool_calls",
            "raw_tool_call_count": 1,
            "derived_batch_count": 1,
            "derived_batch_modes": ["sequential"],
            "batches": [{"mode": "sequential", "calls": ["fs.read_text"], "call_ids": ["call_1"]}],
        }
        yield {
            "type": "tool_started",
            "tool_name": "fs.read_text",
            "call_id": "call_1",
            "reasoning": "Read the target file",
            "arguments": {"path": "README.md"},
            "depends_on": [],
            "batch_index": 1,
        }
        yield {"type": "tool_finished", "tool_name": "fs.read_text", "call_id": "call_1", "success": True}
        yield {"type": "text_chunk", "chunk": "Done", "source": "finalize_stream"}
        yield {"type": "run_completed", "final_text": "Done"}


class _PlanningChatterAgent:
    def run_events(self, **kwargs):
        yield {"type": "llm_response", "usage": {"input_tokens": 4, "output_tokens": 5, "total_tokens": 9}}
        yield {"type": "text_chunk", "chunk": "I'll inspect first. ", "source": "direct"}
        yield {
            "type": "plan_received",
            "tool_call_source": "native_tool_calls",
            "raw_tool_call_count": 2,
            "derived_batch_count": 1,
            "derived_batch_modes": ["parallel"],
            "batches": [{"mode": "parallel", "calls": ["fs.search", "fs.search"], "call_ids": ["call_1", "call_2"]}],
        }
        yield {"type": "tool_started", "tool_name": "fs.search", "call_id": "call_1", "success": True}
        yield {"type": "tool_finished", "tool_name": "fs.search", "call_id": "call_1", "success": True}
        yield {"type": "text_chunk", "chunk": "Final answer.", "source": "finalize_stream"}
        yield {"type": "run_completed", "final_text": "Final answer."}


class TuiMtpBackendTests(unittest.TestCase):
    def test_run_mtp_prompt_streams_reasoning_and_tools_live(self) -> None:
        emitted: list[tuple[str, object]] = []

        result = run_mtp_prompt(
            agent=_StreamingAgent(),
            prompt="inspect README",
            max_rounds=3,
            emit_live=lambda kind, message: emitted.append((kind, message)),
            provider_name="xiaomi",
            model_name="mimo-v2.5-pro",
        )

        self.assertEqual(
            emitted,
            [
                ("status", "Sending request to provider..."),
                ("reasoning", "Need "),
                ("reasoning", "to inspect first."),
                (
                    "tool_detail",
                    {
                        "type": "plan_received",
                        "tool_call_source": "native_tool_calls",
                        "raw_tool_call_count": 1,
                        "derived_batch_count": 1,
                        "derived_batch_modes": ["sequential"],
                        "batches": [{"mode": "sequential", "calls": ["fs.read_text"], "call_ids": ["call_1"]}],
                    },
                ),
                ("tool", "🔧 fs.read_text: Read the target file"),
                (
                    "tool_detail",
                    {
                        "type": "tool_started",
                        "tool_name": "fs.read_text",
                        "call_id": "call_1",
                        "batch_index": 1,
                        "depends_on": [],
                        "arguments": {"path": "README.md"},
                        "reasoning": "Read the target file",
                    },
                ),
                ("tool_end", f"{SYM_OK} fs.read_text completed"),
                (
                    "tool_detail",
                    {
                        "type": "tool_finished",
                        "tool_name": "fs.read_text",
                        "call_id": "call_1",
                        "success": True,
                        "cached": None,
                        "approval": None,
                        "media_counts": None,
                        "reasoning": None,
                    },
                ),
                ("text", "Done"),
                ("status", "Processing response..."),
            ],
        )
        self.assertEqual(result.text, "Done")
        self.assertEqual(
            result.tool_events,
            [
                "🔧 fs.read_text: Read the target file",
                f"  {SYM_OK} fs.read_text completed",
            ],
        )
        self.assertIn("thinking=Need to inspect first.", result.usage_lines)
        self.assertEqual(result.tool_details[0]["tool_call_source"], "native_tool_calls")

    def test_run_mtp_prompt_drops_pre_tool_streamed_planning_text(self) -> None:
        result = run_mtp_prompt(
            agent=_PlanningChatterAgent(),
            prompt="inspect project",
            max_rounds=3,
        )

        self.assertEqual(result.text, "Final answer.")
        self.assertNotIn("I'll inspect first.", result.text)
        self.assertEqual(result.tool_details[0]["raw_tool_call_count"], 2)


if __name__ == "__main__":
    unittest.main()
