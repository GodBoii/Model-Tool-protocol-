from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp.config import load_dotenv_if_available
from mtp.policy import PolicyDecision, RiskPolicy
from mtp.protocol import ToolCall, ToolRiskLevel, ToolSpec
from mtp.runtime import ToolRegistry


class ConfigRuntimePolicyTests(unittest.TestCase):
    def test_policy_blocks_destructive_tool(self) -> None:
        policy = RiskPolicy(by_risk={ToolRiskLevel.DESTRUCTIVE: PolicyDecision.DENY})
        reg = ToolRegistry(policy=policy)
        reg.register_tool(
            ToolSpec(
                name="fs.delete",
                description="Delete a file",
                risk_level=ToolRiskLevel.DESTRUCTIVE,
            ),
            lambda path: f"deleted:{path}",
        )
        result = asyncio.run(
            reg.execute_call(ToolCall(id="x", name="fs.delete", arguments={"path": "a.txt"}), {})
        )
        self.assertFalse(result.success)
        self.assertTrue(result.skipped)
        self.assertEqual(result.approval, PolicyDecision.DENY.value)

    def test_load_dotenv_from_explicit_path(self) -> None:
        old_value = os.environ.get("MTP_TEST_KEY")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                dotenv_path = pathlib.Path(tmp) / ".env.example"
                dotenv_path.write_text("MTP_TEST_KEY=hello\n", encoding="utf-8")
                loaded = load_dotenv_if_available(str(dotenv_path))
                self.assertTrue(loaded)
                self.assertEqual(os.getenv("MTP_TEST_KEY"), "hello")
        finally:
            if old_value is None:
                os.environ.pop("MTP_TEST_KEY", None)
            else:
                os.environ["MTP_TEST_KEY"] = old_value


if __name__ == "__main__":
    unittest.main()
