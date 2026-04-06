from .events import EventStreamContext
from .config import load_dotenv_if_available
from .policy import PolicyDecision, RiskPolicy
from .agent import Agent, AgentAction, ProviderAdapter, RunOutput
from .session_store import (
    JsonSessionStore,
    MySQLSessionStore,
    PostgresSessionStore,
    SessionRecord,
    SessionRun,
    SessionStore,
)
from .exceptions import RetryAgentRun, StopAgentRun
from .protocol import (
    ExecutionPlan,
    ToolOutput,
    ToolBatch,
    ToolCall,
    ToolResult,
    ToolRiskLevel,
    ToolSpec,
)
from .media import Audio, File, Image, Video
from .runtime import ExecutionCancelledError, ToolRegistry, ToolkitLoader, ToolRetryError, ToolStopError
from .schema import (
    CURRENT_MTP_VERSION,
    MessageEnvelope,
    ToolArgumentsValidationError,
    validate_execution_plan,
    validate_tool_arguments,
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
from .transport import HTTPTransportServer, run_stdio_transport
from .mcp import (
    MCPJsonRpcServer,
    MCPPrompt,
    MCPPromptArgument,
    MCPResource,
    MCPServerInfo,
    run_mcp_stdio,
)

__version__ = "0.1.3"

# Convenience aliases for minimal import style:
# from mtp import Agent
# tools = Agent.ToolRegistry()
# helper = Agent.MTPAgent(...)
Agent.MTPAgent = MTPAgent
Agent.ToolRegistry = ToolRegistry
Agent.ToolkitLoader = ToolkitLoader
Agent.ToolSpec = ToolSpec
Agent.ToolRiskLevel = ToolRiskLevel
Agent.Audio = Audio
Agent.Image = Image
Agent.Video = Video
Agent.File = File
Agent.mtp_tool = staticmethod(mtp_tool)
Agent.tool_spec_from_callable = staticmethod(tool_spec_from_callable)
Agent.FunctionToolkit = FunctionToolkit
Agent.toolkit_from_functions = staticmethod(toolkit_from_functions)
Agent.RetryAgentRun = RetryAgentRun
Agent.StopAgentRun = StopAgentRun
Agent.register_local_toolkits = staticmethod(register_local_toolkits)
Agent.load_dotenv_if_available = staticmethod(load_dotenv_if_available)

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
    "MCPJsonRpcServer",
    "MCPResource",
    "MCPPromptArgument",
    "MCPPrompt",
    "MCPServerInfo",
    "run_mcp_stdio",
    "StrictViolation",
    "validate_strict_dependencies",
    "load_dotenv_if_available",
    "validate_execution_plan",
    "validate_tool_arguments",
    "ToolArgumentsValidationError",
    "RetryAgentRun",
    "StopAgentRun",
]
