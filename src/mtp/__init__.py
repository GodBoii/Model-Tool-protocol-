"""MTP: Model Tool Protocol SDK.

Public names are imported on first use (PEP 562). ``import mtp`` used to load
every provider adapter, toolkit and transport; now it only loads what the
caller touches, so ``from mtp import JsonSessionStore`` does not pay for the
agent loop or MCP. ``from mtp import X`` and ``mtp.X`` work as before.
"""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

__version__ = "0.1.36"

# Public name -> submodule that defines it.
_EXPORTS: dict[str, str] = {
    "EventStreamContext": ".events",
    "load_dotenv_if_available": ".config",
    "PolicyDecision": ".policy",
    "RiskPolicy": ".policy",
    "Agent": ".agent",
    "AgentAction": ".agent",
    "ProviderAdapter": ".agent",
    "RunOutput": ".agent",
    "JsonSessionStore": ".session_store",
    "MySQLSessionStore": ".session_store",
    "PostgresSessionStore": ".session_store",
    "SessionRecord": ".session_store",
    "SessionRun": ".session_store",
    "SessionStore": ".session_store",
    "RetryAgentRun": ".exceptions",
    "StopAgentRun": ".exceptions",
    "ExecutionPlan": ".protocol",
    "ToolOutput": ".protocol",
    "ToolBatch": ".protocol",
    "ToolCall": ".protocol",
    "ToolResult": ".protocol",
    "ToolRiskLevel": ".protocol",
    "ToolSpec": ".protocol",
    "Audio": ".media",
    "File": ".media",
    "Image": ".media",
    "Video": ".media",
    "ExecutionCancelledError": ".runtime",
    "ToolRegistry": ".runtime",
    "ToolkitLoader": ".runtime",
    "ToolRetryError": ".runtime",
    "ToolStopError": ".runtime",
    "CURRENT_MTP_VERSION": ".schema",
    "MessageEnvelope": ".schema",
    "ToolArgumentsValidationError": ".schema",
    "validate_execution_plan": ".schema",
    "validate_tool_arguments": ".schema",
    "MTPAgent": ".simple_agent",
    "StrictViolation": ".strict",
    "validate_strict_dependencies": ".strict",
    "FunctionToolkit": ".tools",
    "mtp_tool": ".tools",
    "tool_spec_from_callable": ".tools",
    "toolkit_from_functions": ".tools",
    "CalculatorToolkit": ".toolkits",
    "Crawl4aiToolkit": ".toolkits",
    "FileToolkit": ".toolkits",
    "Newspaper4kToolkit": ".toolkits",
    "NewspaperToolkit": ".toolkits",
    "PythonToolkit": ".toolkits",
    "ShellToolkit": ".toolkits",
    "WebsiteToolkit": ".toolkits",
    "WikipediaToolkit": ".toolkits",
    "register_local_toolkits": ".toolkits",
    "HTTPTransportServer": ".transport",
    "run_stdio_transport": ".transport",
    "WebSocketTransportServer": ".transport",
    "run_ws_transport": ".transport",
    "CodebaseMemory": ".codebase",
    "MCPAuthContext": ".mcp",
    "MCPAuthDecision": ".mcp",
    "MCPAuthProvider": ".mcp",
    "MCPJsonRpcServer": ".mcp",
    "MCPPrompt": ".mcp",
    "MCPPromptArgument": ".mcp",
    "MCPResource": ".mcp",
    "MCPServerInfo": ".mcp",
    "run_mcp_stdio": ".mcp",
    "MCPHTTPTransportServer": ".mcp_transport",
    "MCPWebSocketTransportServer": ".mcp_transport",
    "run_mcp_http": ".mcp_transport",
    "run_mcp_ws": ".mcp_transport",
}

# Exports that need an optional dependency; they are None when it is missing,
# as they were with the old eager imports.
_OPTIONAL_EXPORTS = frozenset({"WebSocketTransportServer", "run_ws_transport"})

__all__ = [
    "__version__",
    "Agent",
    "AgentAction",
    "RunOutput",
    "SessionStore",
    "JsonSessionStore",
    "PostgresSessionStore",
    "MySQLSessionStore",
    "SessionRecord",
    "SessionRun",
    "ExecutionPlan",
    "CURRENT_MTP_VERSION",
    "MTPAgent",
    "EventStreamContext",
    "mtp_tool",
    "tool_spec_from_callable",
    "FunctionToolkit",
    "toolkit_from_functions",
    "MessageEnvelope",
    "ProviderAdapter",
    "PolicyDecision",
    "RiskPolicy",
    "ToolBatch",
    "ToolCall",
    "ToolOutput",
    "ToolRegistry",
    "ExecutionCancelledError",
    "ToolRetryError",
    "ToolStopError",
    "ToolResult",
    "ToolRiskLevel",
    "ToolSpec",
    "Audio",
    "Image",
    "Video",
    "File",
    "ToolkitLoader",
    "CalculatorToolkit",
    "Crawl4aiToolkit",
    "FileToolkit",
    "NewspaperToolkit",
    "Newspaper4kToolkit",
    "PythonToolkit",
    "ShellToolkit",
    "WebsiteToolkit",
    "WikipediaToolkit",
    "register_local_toolkits",
    "HTTPTransportServer",
    "run_stdio_transport",
    "WebSocketTransportServer",
    "run_ws_transport",
    "MCPJsonRpcServer",
    "MCPAuthProvider",
    "MCPAuthContext",
    "MCPAuthDecision",
    "MCPResource",
    "MCPPromptArgument",
    "MCPPrompt",
    "MCPServerInfo",
    "run_mcp_stdio",
    "MCPHTTPTransportServer",
    "MCPWebSocketTransportServer",
    "run_mcp_http",
    "run_mcp_ws",
    "StrictViolation",
    "validate_strict_dependencies",
    "load_dotenv_if_available",
    "CodebaseMemory",
    "validate_execution_plan",
    "validate_tool_arguments",
    "ToolArgumentsValidationError",
    "RetryAgentRun",
    "StopAgentRun",
]


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        # Keep ``import mtp; mtp.providers...`` working the way the old eager
        # imports made it work, by resolving submodules on demand.
        try:
            return importlib.import_module(f".{name}", __name__)
        except ModuleNotFoundError as exc:
            if exc.name != f"{__name__}.{name}":
                raise
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:
        value = getattr(importlib.import_module(module_name, __name__), name)
    except Exception:
        if name not in _OPTIONAL_EXPORTS:
            raise
        value = None
    globals()[name] = value  # cache: later lookups skip __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


if TYPE_CHECKING:  # pragma: no cover - static analysis only
    from .agent import Agent, AgentAction, ProviderAdapter, RunOutput
    from .codebase import CodebaseMemory
    from .config import load_dotenv_if_available
    from .events import EventStreamContext
    from .exceptions import RetryAgentRun, StopAgentRun
    from .mcp import (
        MCPAuthContext,
        MCPAuthDecision,
        MCPAuthProvider,
        MCPJsonRpcServer,
        MCPPrompt,
        MCPPromptArgument,
        MCPResource,
        MCPServerInfo,
        run_mcp_stdio,
    )
    from .mcp_transport import MCPHTTPTransportServer, MCPWebSocketTransportServer, run_mcp_http, run_mcp_ws
    from .media import Audio, File, Image, Video
    from .policy import PolicyDecision, RiskPolicy
    from .protocol import ExecutionPlan, ToolBatch, ToolCall, ToolOutput, ToolResult, ToolRiskLevel, ToolSpec
    from .runtime import ExecutionCancelledError, ToolkitLoader, ToolRegistry, ToolRetryError, ToolStopError
    from .schema import (
        CURRENT_MTP_VERSION,
        MessageEnvelope,
        ToolArgumentsValidationError,
        validate_execution_plan,
        validate_tool_arguments,
    )
    from .session_store import (
        JsonSessionStore,
        MySQLSessionStore,
        PostgresSessionStore,
        SessionRecord,
        SessionRun,
        SessionStore,
    )
    from .simple_agent import MTPAgent
    from .strict import StrictViolation, validate_strict_dependencies
    from .tools import FunctionToolkit, mtp_tool, tool_spec_from_callable, toolkit_from_functions
    from .toolkits import (
        CalculatorToolkit,
        Crawl4aiToolkit,
        FileToolkit,
        Newspaper4kToolkit,
        NewspaperToolkit,
        PythonToolkit,
        ShellToolkit,
        WebsiteToolkit,
        WikipediaToolkit,
        register_local_toolkits,
    )
    from .transport import HTTPTransportServer, run_stdio_transport, run_ws_transport, WebSocketTransportServer
