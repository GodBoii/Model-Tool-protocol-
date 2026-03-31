from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .protocol import ToolCall, ToolRiskLevel, ToolSpec


class PolicyDecision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(slots=True)
class RiskPolicy:
    by_risk: dict[ToolRiskLevel, PolicyDecision] = field(
        default_factory=lambda: {
            ToolRiskLevel.READ_ONLY: PolicyDecision.ALLOW,
            ToolRiskLevel.WRITE: PolicyDecision.ALLOW,
            ToolRiskLevel.DESTRUCTIVE: PolicyDecision.ASK,
        }
    )
    by_tool_name: dict[str, PolicyDecision] = field(default_factory=dict)

    def decide(self, tool: ToolSpec, call: ToolCall, args: dict[str, Any]) -> PolicyDecision:
        # Tool-specific rules override global risk rules.
        if tool.name in self.by_tool_name:
            return self.by_tool_name[tool.name]
        return self.by_risk.get(tool.risk_level, PolicyDecision.ASK)

