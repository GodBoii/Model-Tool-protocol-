from __future__ import annotations

import pytest

from mtp.policy import PolicyDecision, RiskPolicy
from mtp.protocol import ToolCall, ToolRiskLevel, ToolSpec


def _make_tool(name: str = "test.tool", risk: ToolRiskLevel = ToolRiskLevel.READ_ONLY) -> ToolSpec:
    return ToolSpec(name=name, description="d", risk_level=risk)


def _call(name: str = "test.tool") -> ToolCall:
    return ToolCall(id="c1", name=name)


class TestPolicyDecision:
    def test_values(self):
        assert PolicyDecision.ALLOW.value == "allow"
        assert PolicyDecision.ASK.value == "ask"
        assert PolicyDecision.DENY.value == "deny"

    def test_is_str(self):
        assert isinstance(PolicyDecision.ALLOW, str)


class TestRiskPolicy:
    def test_defaults(self):
        policy = RiskPolicy()
        assert policy.decide(_make_tool(risk=ToolRiskLevel.READ_ONLY), _call(), {}) == PolicyDecision.ALLOW
        assert policy.decide(_make_tool(risk=ToolRiskLevel.WRITE), _call(), {}) == PolicyDecision.ALLOW
        assert policy.decide(_make_tool(risk=ToolRiskLevel.DESTRUCTIVE), _call(), {}) == PolicyDecision.ASK

    def test_custom_risk_map(self):
        policy = RiskPolicy(by_risk={ToolRiskLevel.DESTRUCTIVE: PolicyDecision.DENY})
        assert policy.decide(_make_tool(risk=ToolRiskLevel.DESTRUCTIVE), _call(), {}) == PolicyDecision.DENY

    def test_tool_name_override(self):
        policy = RiskPolicy(
            by_tool_name={"shell.run_command": PolicyDecision.DENY}
        )
        assert policy.decide(
            _make_tool("shell.run_command", ToolRiskLevel.WRITE), _call("shell.run_command"), {}
        ) == PolicyDecision.DENY

    def test_tool_name_overrides_risk(self):
        policy = RiskPolicy(
            by_risk={ToolRiskLevel.DESTRUCTIVE: PolicyDecision.DENY},
            by_tool_name={"dangerous.tool": PolicyDecision.ALLOW},
        )
        assert policy.decide(
            _make_tool("dangerous.tool", ToolRiskLevel.DESTRUCTIVE), _call("dangerous.tool"), {}
        ) == PolicyDecision.ALLOW

    def test_unknown_risk_defaults_to_ask(self):
        policy = RiskPolicy(by_risk={})
        assert policy.decide(_make_tool(risk=ToolRiskLevel.READ_ONLY), _call(), {}) == PolicyDecision.ASK
