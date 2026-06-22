from __future__ import annotations

import asyncio
import os
import pytest
from pathlib import Path

from mtp.agent import Agent, AgentAction
from mtp.runtime import ToolRegistry
from mtp.simple_agent import MTPAgent
from mtp.protocol import ToolCall, ToolResult, ToolSpec, ExecutionPlan, ToolBatch
from mtp.config import load_dotenv_if_available

load_dotenv_if_available()

MIMO_API_KEY = os.getenv("MIMO_API_KEY")
XIAOMI_LIVE_ENABLED = os.getenv("RUN_LIVE_XIAOMI") == "1" and bool(MIMO_API_KEY)

pytestmark = [pytest.mark.live, pytest.mark.xiaomi]

skip_no_xiaomi = pytest.mark.skipif(
    not XIAOMI_LIVE_ENABLED,
    reason="Set RUN_LIVE_XIAOMI=1 and MIMO_API_KEY to run live Xiaomi tests.",
)


def _make_xiaomi_provider():
    from mtp.providers.xiaomi_provider import XiaomiToolCallingProvider
    return XiaomiToolCallingProvider(api_key=MIMO_API_KEY)


@skip_no_xiaomi
class TestXiaomiProviderDirect:
    def test_capabilities(self):
        provider = _make_xiaomi_provider()
        caps = provider.capabilities()
        assert caps.provider == "xiaomi"
        assert caps.supports_tool_calling is True

    def test_text_only_response(self):
        provider = _make_xiaomi_provider()
        from mtp.providers.common import USAGE_METRICS_NONE
        agent = Agent(provider=provider, tools=ToolRegistry())
        result = agent.run_loop("Say exactly: hello world")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_finalize(self):
        provider = _make_xiaomi_provider()
        result = provider.finalize(
            [{"role": "user", "content": "Say hi"}],
            [],
        )
        assert isinstance(result, str)
        assert len(result) > 0


@skip_no_xiaomi
class TestXiaomiWithTools:
    def test_calculator_tool(self):
        from mtp.toolkits.calculator import CalculatorToolkit
        provider = _make_xiaomi_provider()
        reg = ToolRegistry()
        reg.register_toolkit_loader("calculator", CalculatorToolkit())
        agent = Agent(provider=provider, tools=reg)
        result = agent.run_loop("What is 15 + 27? Use the calculator.add tool.", max_rounds=5)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_file_tool(self, tmp_path):
        from mtp.toolkits.file_toolkit import FileToolkit
        (tmp_path / "test.txt").write_text("Hello from MTP test")
        provider = _make_xiaomi_provider()
        reg = ToolRegistry()
        reg.register_toolkit_loader("file", FileToolkit(base_dir=tmp_path))
        agent = Agent(provider=provider, tools=reg)
        result = agent.run_loop(
            "Read the file test.txt using the file.read_file tool and tell me its content.",
            max_rounds=5,
        )
        assert isinstance(result, str)
        assert len(result) > 0


@skip_no_xiaomi
class TestXiaomiMTPAgent:
    def test_mtpagent_run(self):
        from mtp.toolkits.calculator import CalculatorToolkit
        provider = _make_xiaomi_provider()
        reg = ToolRegistry()
        reg.register_toolkit_loader("calculator", CalculatorToolkit())
        mtp_agent = MTPAgent(provider=provider, tools=reg)
        result = mtp_agent.run("What is 100 + 200? Use calculator.add.", max_rounds=5)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_mtpagent_run_output(self):
        provider = _make_xiaomi_provider()
        reg = ToolRegistry()
        mtp_agent = MTPAgent(provider=provider, tools=reg)
        output = mtp_agent.run_output("Say hello", max_rounds=3)
        assert output.final_text is not None
        assert len(output.final_text) > 0
        assert output.cancelled is False

    def test_mtpagent_run_events(self):
        provider = _make_xiaomi_provider()
        reg = ToolRegistry()
        mtp_agent = MTPAgent(provider=provider, tools=reg)
        events = list(mtp_agent.run_events("Say hi", max_rounds=3))
        types = [e["type"] for e in events]
        assert "run_started" in types
        assert "run_completed" in types


@skip_no_xiaomi
class TestXiaomiCustomTool:
    def test_custom_function_tool(self):
        @Agent.mtp_tool(name="greet", description="Greet a user by name")
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        provider = _make_xiaomi_provider()
        reg = ToolRegistry()
        toolkit = Agent.toolkit_from_functions("greeter", greet)
        reg.register_toolkit_loader("greeter", toolkit)
        agent = Agent(provider=provider, tools=reg)
        result = agent.run_loop("Use the greeter.greet tool to greet 'Alice'", max_rounds=5)
        assert isinstance(result, str)
        assert len(result) > 0


@skip_no_xiaomi
class TestXiaomiStreaming:
    def test_stream_events(self):
        import time
        time.sleep(2)  # Brief pause to avoid rate limiting
        provider = _make_xiaomi_provider()
        reg = ToolRegistry()
        agent = Agent(provider=provider, tools=reg)
        try:
            events = list(agent.run_loop_events("Say hello", max_rounds=3))
            assert len(events) > 0
            text_chunks = [e for e in events if e["type"] == "text_chunk"]
            assert len(text_chunks) > 0
        except Exception as e:
            if "429" in str(e) or "rate" in str(e).lower():
                pytest.skip("Rate limited by API")
            raise


@skip_no_xiaomi
class TestXiaomiAsync:
    @pytest.mark.asyncio
    async def test_arun(self):
        provider = _make_xiaomi_provider()
        reg = ToolRegistry()
        agent = Agent(provider=provider, tools=reg)
        result = await agent.arun_loop("Say hello")
        assert isinstance(result, str)
        assert len(result) > 0
