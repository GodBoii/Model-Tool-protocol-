from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp.protocol import ToolResult, ToolSpec
from mtp.providers.groq_provider import GroqToolCallingProvider


class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self.id = call_id
        self.function = _FakeFunction(name=name, arguments=arguments)


class _FakeMessage:
    def __init__(self, content: str = "", tool_calls: list[_FakeToolCall] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return _FakeResponse(
                _FakeMessage(
                    content="",
                    tool_calls=[
                        _FakeToolCall(
                            call_id="tc_1",
                            name="github.list_repos",
                            arguments='{"username":"demo-user"}',
                        )
                    ],
                )
            )
        return _FakeResponse(_FakeMessage(content="Done from fake Groq"))


class _FakeChat:
    def __init__(self) -> None:
        self.completions = _FakeCompletions()


class _FakeClient:
    def __init__(self) -> None:
        self.chat = _FakeChat()


class GroqProviderTests(unittest.TestCase):
    def test_next_action_builds_plan_from_tool_calls(self) -> None:
        provider = GroqToolCallingProvider(client=_FakeClient())
        action = provider.next_action(
            messages=[{"role": "user", "content": "list repos for demo-user"}],
            tools=[
                ToolSpec(
                    name="github.list_repos",
                    description="List repos",
                    input_schema={"type": "object"},
                )
            ],
        )
        self.assertIsNotNone(action.plan)
        self.assertEqual(action.plan.batches[0].calls[0].name, "github.list_repos")
        self.assertEqual(
            action.plan.batches[0].calls[0].arguments["username"],
            "demo-user",
        )

    def test_finalize_returns_text(self) -> None:
        provider = GroqToolCallingProvider(client=_FakeClient())
        provider.next_action(
            messages=[{"role": "user", "content": "list repos"}],
            tools=[ToolSpec(name="github.list_repos", description="x")],
        )
        text = provider.finalize(
            messages=[
                {"role": "assistant", "content": "", "tool_calls": []},
                {
                    "role": "tool",
                    "tool_call_id": "tc_1",
                    "tool_name": "github.list_repos",
                    "content": {"repos": ["a"]},
                },
            ],
            tool_results=[
                ToolResult(
                    call_id="tc_1",
                    tool_name="github.list_repos",
                    output={"repos": ["a"]},
                )
            ],
        )
        self.assertIn("Done", text)


if __name__ == "__main__":
    unittest.main()
