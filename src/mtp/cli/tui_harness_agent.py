from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mtp import Agent

from .tui_harness_policy import HarnessPermissions, HarnessRiskPolicy, make_approval_handler, normalize_harness_mode
from .tui_harness_tools import register_harness_toolkits


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    cwd: Path
    mode: str = "code"
    autoresearch: bool = False
    research_instructions: str | None = None
    debug_mode: bool = False


def build_harness_agent(
    *,
    provider: Any,
    cwd: Path,
    mode: str,
    autoresearch: bool,
    research_instructions: str | None,
    debug_mode: bool = False,
    sandbox_mode: str = "workspace-write",
) -> Agent.MTPAgent:
    resolved_mode = normalize_harness_mode(mode)
    perms = HarnessPermissions()
    if sandbox_mode in {"workspace-write", "danger-full-access"}:
        for k in list(perms.edit.keys()):
            if perms.edit[k] == "ask":
                perms.edit[k] = "allow"
        for k in list(perms.bash.keys()):
            if perms.bash[k] == "ask":
                perms.bash[k] = "allow"
        if perms.default == "ask":
            perms.default = "allow"

    tools = Agent.ToolRegistry(
        policy=HarnessRiskPolicy(mode=resolved_mode, permissions=perms),
        approval_handler=make_approval_handler(interactive=False),
    )
    register_harness_toolkits(tools, root=cwd)
    return Agent.MTPAgent(
        provider=provider,
        tools=tools,
        instructions=build_orchestrator_instructions(cwd=cwd, mode=resolved_mode),
        debug_mode=debug_mode,
        strict_dependency_mode=True,
        autoresearch=autoresearch,
        research_instructions=research_instructions,
        stream_tool_events=True,
        stream_tool_results=False,
    )


def build_orchestrator_instructions(*, cwd: Path, mode: str) -> str:
    mode = normalize_harness_mode(mode)
    mode_policy = {
        "plan": "Plan mode: inspect and reason only. Do not edit files or run tests/commands that change state.",
        "code": "Code mode: make focused edits with edit.apply_patch or edit.create_file, then verify when practical.",
        "debug": "Debug mode: reproduce or localize the issue first, gather evidence, edit only after root cause is clear, then verify.",
        "review": "Review mode: inspect code and diffs, report bugs and risks. Do not edit files.",
    }[mode]
    return (
        "You are the MTP TUI main orchestrator agent. The user expects real local work, not generic advice.\n\n"
        f"Workspace: {cwd}\n"
        f"{mode_policy}\n\n"
        "Operating rules:\n"
        "- Start by gathering context with project.inspect, fs.search, fs.read_text, or the read-only agent.* subagent tools.\n"
        "- Tool guide: project.inspect gives root structure, file-format counts, git status, and codebase memory status; fs.search finds relevant relative file paths and uses indexed workspace memory when enabled; fs.read_text reads a specific line range.\n"
        "- Delegation: agent.explore_codebase: read-only explorer; agent.debug_context: read-only debug gatherer; agent.syntax_check: read-only Python syntax checker.\n"
        "- Treat agent.explore_codebase, agent.debug_context, and agent.syntax_check as specialist subagents for broad search, debugging context, and checks.\n"
        "- The main orchestrator owns all important decisions and all code edits. Do not ask subagent tools to edit.\n"
        "- Prefer exact, small edits through edit.apply_patch. Re-read files before editing if context may be stale.\n"
        "- Never overwrite unrelated user changes. Check git.status/git.diff when edits might interact with existing work.\n"
        "- Use shell.run and test.run only for useful inspection or verification. Keep commands targeted.\n"
        "- If a tool is denied, continue with the best safe alternative and explain the limitation briefly.\n"
        "- Finish with a concise summary of what changed, what was checked, and any remaining risk.\n\n"
        "CRITICAL - Tool Calling:\n"
        "- When you need to use tools, call them directly using the structured tool calling API.\n"
        "- NEVER output tool calls as JSON text, code blocks, or descriptions.\n"
        "- NEVER describe what tools you will call - just call them immediately.\n"
        "- The system will automatically execute your tool calls and return results.\n"
        "- After receiving tool results, synthesize them into a clear response for the user.\n\n"
        "Quality bar:\n"
        "- For coding tasks, understand the existing pattern before changing code.\n"
        "- For bug fixes, identify the likely root cause before editing.\n"
        "- Search first, read narrowly. If codebase memory is enabled, prefer fs.search before broad filesystem scans. For broad investigations, delegate context gathering to the read-only subagent tools, then synthesize yourself.\n"
        "- For non-coding computer tasks, inspect first and avoid destructive actions unless the user clearly requested them."
    )

