from __future__ import annotations

from dataclasses import dataclass, field
import fnmatch
from typing import Any

from mtp.policy import PolicyDecision, RiskPolicy
from mtp.protocol import ToolCall, ToolRiskLevel, ToolSpec


HARNESS_MODES = ("plan", "code", "debug", "review")
SANDBOX_MODES = ("read-only", "workspace-write", "danger-full-access")
PERMISSION_ACTIONS = ("allow", "ask", "deny")


@dataclass(slots=True)
class HarnessPermissions:
    default: str = "ask"
    read: dict[str, str] = field(
        default_factory=lambda: {
            "*": "allow",
            "*.env": "deny",
            "*.env.*": "deny",
            "*.env.example": "allow",
        }
    )
    edit: dict[str, str] = field(default_factory=lambda: {"*": "ask"})
    bash: dict[str, str] = field(
        default_factory=lambda: {
            "git status*": "allow",
            "git diff*": "allow",
            "git show*": "allow",
            "python -m py_compile*": "allow",
            "python -m compileall*": "ask",
            "pytest*": "ask",
            "python -m pytest*": "ask",
            "npm test*": "ask",
            "npm run test*": "ask",
            "rm *": "deny",
            "del *": "deny",
            "rmdir *": "deny",
            "Remove-Item *": "deny",
            "*": "ask",
        }
    )
    tool: dict[str, str] = field(default_factory=dict)

    def merged_for_mode(self, mode: str) -> "HarnessPermissions":
        copied = HarnessPermissions(
            default=self.default,
            read=dict(self.read),
            edit=dict(self.edit),
            bash=dict(self.bash),
            tool=dict(self.tool),
        )
        if mode in {"plan", "review"}:
            copied.edit["*"] = "deny"
            copied.bash["*"] = "deny"
            copied.bash["git status*"] = "allow"
            copied.bash["git diff*"] = "allow"
            copied.tool["edit.*"] = "deny"
            copied.tool["shell.run"] = "deny"
            copied.tool["test.run"] = "deny"
        elif mode == "debug":
            copied.bash["python -m pytest*"] = "ask"
            copied.bash["pytest*"] = "ask"
            copied.bash["python*"] = "ask"
        return copied


def normalize_harness_mode(value: str | None) -> str:
    mode = (value or "code").strip().lower()
    if mode not in HARNESS_MODES:
        raise ValueError(f"Unknown harness mode: {value!r}. Expected one of: {', '.join(HARNESS_MODES)}")
    return mode


def normalize_sandbox_mode(value: str | None) -> str:
    """Validate a TUI execution permission profile.

    These profiles control tool approvals.  They are deliberately not called
    process sandboxes: setting a subprocess's working directory does not
    contain what that process can read or write.
    """
    mode = ("workspace-write" if value is None else value).strip().lower()
    if mode not in SANDBOX_MODES:
        raise ValueError(f"Unknown sandbox mode: {value!r}. Expected one of: {', '.join(SANDBOX_MODES)}")
    return mode


def permissions_for_sandbox(mode: str) -> HarnessPermissions:
    """Return permissions for *mode* without silently approving ``ask`` rules."""
    resolved = normalize_sandbox_mode(mode)
    permissions = HarnessPermissions()
    if resolved == "read-only":
        permissions.edit["*"] = "deny"
        permissions.bash["*"] = "deny"
        permissions.tool["edit.*"] = "deny"
        permissions.tool["shell.*"] = "deny"
        permissions.tool["test.*"] = "deny"
    elif resolved == "danger-full-access":
        # This is an explicit opt-in permission profile, not OS containment.
        permissions.edit["*"] = "allow"
        permissions.bash["*"] = "allow"
        permissions.default = "allow"
    # workspace-write intentionally retains ASK for edits, tests and arbitrary
    # shell commands.  The non-interactive harness denies those unless a caller
    # supplies a real approval flow; safe read-only command patterns remain.
    return permissions


def _normalize_action(value: str) -> str:
    action = value.strip().lower()
    if action not in PERMISSION_ACTIONS:
        raise ValueError(f"Invalid permission action: {value!r}")
    return action


def _match_rules(rules: dict[str, str], subject: str, default: str) -> str:
    selected = default
    selected_specificity = -1
    for pattern, action in rules.items():
        if fnmatch.fnmatchcase(subject, pattern):
            # A catch-all is commonly declared last for readability.  It must
            # not override a more specific allow/deny rule merely due to dict
            # insertion order.
            specificity = len(pattern.replace("*", "").replace("?", ""))
            if specificity >= selected_specificity:
                selected = _normalize_action(action)
                selected_specificity = specificity
    return selected


def _tool_group(tool_name: str) -> str:
    if tool_name.startswith(("fs.", "project.", "agent.explore", "agent.debug_context", "agent.syntax_check")):
        return "read"
    if tool_name.startswith("edit."):
        return "edit"
    if tool_name.startswith(("shell.", "test.")):
        return "bash"
    return "tool"


class HarnessRiskPolicy(RiskPolicy):
    def __init__(self, *, mode: str, permissions: HarnessPermissions | None = None) -> None:
        self.mode = normalize_harness_mode(mode)
        self.permissions = (permissions or HarnessPermissions()).merged_for_mode(self.mode)
        super().__init__(
            by_risk={
                ToolRiskLevel.READ_ONLY: PolicyDecision.ALLOW,
                ToolRiskLevel.WRITE: PolicyDecision.ASK,
                ToolRiskLevel.DESTRUCTIVE: PolicyDecision.ASK,
            }
        )

    def decide(self, tool: ToolSpec, call: ToolCall, args: dict[str, Any]) -> PolicyDecision:
        tool_name = tool.name
        tool_action = _match_rules(self.permissions.tool, tool_name, "")
        if tool_action:
            return PolicyDecision(tool_action)

        group = _tool_group(tool_name)
        subject = _permission_subject(tool_name, args)
        if group == "read":
            action = _match_rules(self.permissions.read, subject, "allow")
        elif group == "edit":
            action = _match_rules(self.permissions.edit, subject, "ask")
        elif group == "bash":
            action = _match_rules(self.permissions.bash, subject, "ask")
        else:
            action = self.permissions.default
        return PolicyDecision(action)


def _permission_subject(tool_name: str, args: dict[str, Any]) -> str:
    if tool_name.startswith(("fs.", "edit.")):
        for key in ("path", "file", "target"):
            value = args.get(key)
            if isinstance(value, str) and value:
                return value.replace("\\", "/")
    if tool_name.startswith(("shell.", "test.")):
        value = args.get("command")
        if isinstance(value, str):
            return " ".join(value.split())
    return tool_name


def make_approval_handler(*, interactive: bool = True):
    async def _approval(tool: ToolSpec, call: ToolCall, args: dict[str, Any]) -> bool:
        if not interactive:
            return False
        subject = _permission_subject(tool.name, args)
        print()
        print(f"  Permission needed: {tool.name}")
        print(f"  Target: {subject}")
        answer = input("  Allow once? [y/N] ").strip().lower()
        return answer in {"y", "yes"}

    return _approval

