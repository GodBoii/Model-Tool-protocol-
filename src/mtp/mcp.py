from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Protocol

from .protocol import ToolCall, ToolRiskLevel, ToolSpec
from .runtime import ToolRegistry

JsonDict = dict[str, Any]
AuthValidator = Callable[[str | None, JsonDict], bool]
ResourceReader = Callable[[str], Any]
PromptRenderer = Callable[[str, dict[str, Any]], Any]
ProgressHandler = Callable[[JsonDict], None]
ProgressListener = Callable[[JsonDict], None]


@dataclass(slots=True)
class MCPAuthContext:
    method: str
    request_id: Any
    params: JsonDict
    metadata: JsonDict


@dataclass(slots=True)
class MCPAuthDecision:
    allowed: bool
    error_code: int = -32001
    message: str = "Unauthorized"
    www_authenticate: str | None = None
    details: JsonDict | None = None


class MCPAuthProvider(Protocol):
    def authorize(
        self,
        token: str | None,
        request: JsonDict,
        context: MCPAuthContext,
    ) -> MCPAuthDecision | bool | Awaitable[MCPAuthDecision | bool]:
        ...


@dataclass(slots=True)
class MCPResource:
    uri: str
    name: str | None = None
    description: str = ""
    mime_type: str = "text/plain"


@dataclass(slots=True)
class MCPPromptArgument:
    name: str
    description: str = ""
    required: bool = False


@dataclass(slots=True)
class MCPPrompt:
    name: str
    description: str = ""
    arguments: list[MCPPromptArgument] | None = None
    template: str | None = None


@dataclass(slots=True)
class MCPServerInfo:
    name: str = "mtp-mcp-adapter"
    version: str = "0.1.0"


class MCPJsonRpcServer:
    """
    Thin MCP-compatible JSON-RPC adapter around MTP ToolRegistry.

    Scope:
    - lifecycle: initialize + notifications/initialized
    - capability negotiation: tools/resources/prompts + progress/cancellation hints
    - methods: ping, tools/list, tools/call, resources/list, resources/read, prompts/list, prompts/get
    - optional request-level auth token validation
    - optional cancellation (`$/cancelRequest` or `notifications/cancelled`)
    - optional progress hook for transports that emit notifications
    """

    def __init__(
        self,
        *,
        tools: ToolRegistry,
        server_info: MCPServerInfo | None = None,
        instructions: str | None = None,
        require_auth: bool = False,
        auth_token: str | None = None,
        auth_validator: AuthValidator | None = None,
        auth_provider: MCPAuthProvider | None = None,
        protocol_version: str = "2026-03-26",
        resources: list[MCPResource] | None = None,
        resource_reader: ResourceReader | None = None,
        prompts: list[MCPPrompt] | None = None,
        prompt_renderer: PromptRenderer | None = None,
        progress_handler: ProgressHandler | None = None,
        support_progress: bool = True,
        support_cancellation: bool = True,
    ) -> None:
        self.tools = tools
        self.server_info = server_info or MCPServerInfo()
        self.instructions = instructions or (
            "This MCP compatibility server exposes MTP tools through JSON-RPC methods."
        )
        self.require_auth = require_auth
        self.auth_token = auth_token
        self.auth_validator = auth_validator
        self.auth_provider = auth_provider
        self.protocol_version = protocol_version
        self.resources = list(resources or [])
        self.resource_reader = resource_reader
        self.prompts = list(prompts or [])
        self.prompt_renderer = prompt_renderer
        self.progress_handler = progress_handler
        self.support_progress = support_progress
        self.support_cancellation = support_cancellation

        self._initialized = False
        self._client_initialized = False
        self._client_info: JsonDict = {}
        self._client_capabilities: JsonDict = {}
        self._initialized_at: datetime | None = None
        self._cancelled_request_ids: set[str] = set()
        self._progress_events: list[JsonDict] = []
        self._progress_listeners: list[ProgressListener] = []
        self._active_call_tasks: dict[str, asyncio.Task[Any]] = {}

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def client_initialized(self) -> bool:
        return self._client_initialized

    @property
    def client_info(self) -> JsonDict:
        return dict(self._client_info)

    @property
    def progress_events(self) -> list[JsonDict]:
        return list(self._progress_events)

    def add_progress_listener(self, listener: ProgressListener) -> None:
        self._progress_listeners.append(listener)

    def handle_json(self, raw: str) -> str | None:
        """
        Handle one JSON-RPC request payload (single request object only).
        Returns serialized response JSON, or None for notifications.
        """
        try:
            data = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            return json.dumps(self._error_response(None, -32700, f"Parse error: {exc}"))

        if not isinstance(data, dict):
            return json.dumps(self._error_response(None, -32600, "Invalid Request: expected object"))

        response = self.handle_request(data)
        if response is None:
            return None
        return json.dumps(response, default=str)

    def handle_request(self, request: JsonDict) -> JsonDict | None:
        request_id = request.get("id")
        is_notification = "id" not in request

        validation_error = self._validate_request(request)
        if validation_error is not None:
            if is_notification:
                return None
            return self._error_response(request_id, -32600, validation_error)

        method = str(request["method"])
        params = request.get("params") if isinstance(request.get("params"), dict) else {}

        if self.support_cancellation and method in {"$/cancelRequest", "notifications/cancelled"}:
            self._record_cancel_request(params)
            return None if is_notification else {"jsonrpc": "2.0", "id": request_id, "result": {}}

        if self._requires_auth(method):
            auth_decision = self._authorized_sync(request, method=method, request_id=request_id, params=params)
            if not auth_decision.allowed:
                if is_notification:
                    return None
                return self._unauthorized_response(request_id, auth_decision)

        if method not in {"initialize", "ping", "notifications/initialized"} and not self._initialized:
            if is_notification:
                return None
            return self._error_response(request_id, -32002, "Server not initialized")

        if self.support_cancellation and request_id is not None and self._is_request_cancelled(request_id):
            if is_notification:
                return None
            return self._error_response(request_id, -32800, "Request cancelled")

        try:
            result = self._dispatch(method, params, request_id=request_id)
        except ValueError as exc:
            if is_notification:
                return None
            message = str(exc)
            if "cancelled" in message.lower():
                return self._error_response(request_id, -32800, message)
            return self._error_response(request_id, -32602, message)
        except Exception as exc:  # noqa: BLE001
            if is_notification:
                return None
            return self._error_response(request_id, -32000, str(exc))

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    async def ahandle_json(self, raw: str) -> str | None:
        try:
            data = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            return json.dumps(self._error_response(None, -32700, f"Parse error: {exc}"))
        if not isinstance(data, dict):
            return json.dumps(self._error_response(None, -32600, "Invalid Request: expected object"))

        response = await self.ahandle_request(data)
        if response is None:
            return None
        return json.dumps(response, default=str)

    async def ahandle_request(self, request: JsonDict) -> JsonDict | None:
        request_id = request.get("id")
        is_notification = "id" not in request

        validation_error = self._validate_request(request)
        if validation_error is not None:
            if is_notification:
                return None
            return self._error_response(request_id, -32600, validation_error)

        method = str(request["method"])
        params = request.get("params") if isinstance(request.get("params"), dict) else {}

        if self.support_cancellation and method in {"$/cancelRequest", "notifications/cancelled"}:
            self._record_cancel_request(params)
            return None if is_notification else {"jsonrpc": "2.0", "id": request_id, "result": {}}

        if self._requires_auth(method):
            auth_decision = await self._authorized_async(request, method=method, request_id=request_id, params=params)
            if not auth_decision.allowed:
                if is_notification:
                    return None
                return self._unauthorized_response(request_id, auth_decision)

        if method not in {"initialize", "ping", "notifications/initialized"} and not self._initialized:
            if is_notification:
                return None
            return self._error_response(request_id, -32002, "Server not initialized")

        if self.support_cancellation and request_id is not None and self._is_request_cancelled(request_id):
            if is_notification:
                return None
            return self._error_response(request_id, -32800, "Request cancelled")

        try:
            result = await self._adispatch(method, params, request_id=request_id)
        except ValueError as exc:
            if is_notification:
                return None
            message = str(exc)
            if "cancelled" in message.lower():
                return self._error_response(request_id, -32800, message)
            return self._error_response(request_id, -32602, message)
        except Exception as exc:  # noqa: BLE001
            if is_notification:
                return None
            return self._error_response(request_id, -32000, str(exc))

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _validate_request(self, request: JsonDict) -> str | None:
        if request.get("jsonrpc") != "2.0":
            return "Invalid Request: jsonrpc must be '2.0'"
        method = request.get("method")
        if not isinstance(method, str) or not method:
            return "Invalid Request: missing method"
        params = request.get("params")
        if params is not None and not isinstance(params, dict):
            return "Invalid params: expected object"
        return None

    def _requires_auth(self, method: str) -> bool:
        return (
            self.require_auth
            or self.auth_provider is not None
            or self.auth_validator is not None
            or self.auth_token is not None
        )

    def _authorized(self, request: JsonDict) -> bool:
        token: str | None = None
        meta = request.get("meta")
        if isinstance(meta, dict):
            candidate = meta.get("authToken")
            if isinstance(candidate, str):
                token = candidate
        if token is None:
            params = request.get("params")
            if isinstance(params, dict):
                candidate = params.get("auth_token")
                if isinstance(candidate, str):
                    token = candidate

        if self.auth_validator is not None:
            return bool(self.auth_validator(token, request))
        if self.auth_token is not None:
            return token == self.auth_token
        return token is not None

    def _extract_auth_token(self, request: JsonDict) -> str | None:
        token: str | None = None
        meta = request.get("meta")
        if isinstance(meta, dict):
            candidate = meta.get("authToken")
            if isinstance(candidate, str):
                token = candidate
        if token is None:
            params = request.get("params")
            if isinstance(params, dict):
                candidate = params.get("auth_token")
                if isinstance(candidate, str):
                    token = candidate
        return token

    def _auth_context(self, *, method: str, request_id: Any, params: JsonDict, request: JsonDict) -> MCPAuthContext:
        metadata = request.get("meta")
        if not isinstance(metadata, dict):
            metadata = {}
        return MCPAuthContext(
            method=method,
            request_id=request_id,
            params=dict(params),
            metadata=dict(metadata),
        )

    def _coerce_auth_decision(self, decision: MCPAuthDecision | bool) -> MCPAuthDecision:
        if isinstance(decision, MCPAuthDecision):
            return decision
        return MCPAuthDecision(allowed=bool(decision))

    def _authorized_sync(
        self,
        request: JsonDict,
        *,
        method: str,
        request_id: Any,
        params: JsonDict,
    ) -> MCPAuthDecision:
        token = self._extract_auth_token(request)
        context = self._auth_context(method=method, request_id=request_id, params=params, request=request)

        if self.auth_provider is not None:
            result = self.auth_provider.authorize(token, request, context)
            if asyncio.iscoroutine(result):
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    resolved = asyncio.run(result)
                    return self._coerce_auth_decision(resolved)
                close_fn = getattr(result, "close", None)
                if callable(close_fn):
                    close_fn()
                return MCPAuthDecision(
                    allowed=False,
                    message="Async auth provider requires async MCP handler (`ahandle_request`).",
                )
            return self._coerce_auth_decision(result)

        if self.auth_validator is not None:
            return MCPAuthDecision(allowed=bool(self.auth_validator(token, request)))
        if self.auth_token is not None:
            return MCPAuthDecision(allowed=token == self.auth_token)
        return MCPAuthDecision(allowed=token is not None)

    async def _authorized_async(
        self,
        request: JsonDict,
        *,
        method: str,
        request_id: Any,
        params: JsonDict,
    ) -> MCPAuthDecision:
        token = self._extract_auth_token(request)
        context = self._auth_context(method=method, request_id=request_id, params=params, request=request)

        if self.auth_provider is not None:
            result = self.auth_provider.authorize(token, request, context)
            if asyncio.iscoroutine(result):
                result = await result
            return self._coerce_auth_decision(result)
        if self.auth_validator is not None:
            return MCPAuthDecision(allowed=bool(self.auth_validator(token, request)))
        if self.auth_token is not None:
            return MCPAuthDecision(allowed=token == self.auth_token)
        return MCPAuthDecision(allowed=token is not None)

    def _dispatch(self, method: str, params: JsonDict, *, request_id: Any = None) -> JsonDict:
        if method == "ping":
            return {"ok": True, "timestamp": datetime.now(UTC).isoformat()}
        if method == "initialize":
            return self._initialize(params)
        if method == "notifications/initialized":
            self._client_initialized = True
            return {}
        if method == "notifications/progress":
            if self.support_progress:
                self._record_inbound_progress(params)
            return {}
        if method == "tools/list":
            return {"tools": [self._tool_spec_to_mcp(spec) for spec in self.tools.list_tools()]}
        if method == "tools/call":
            return self._tools_call(params, request_id=request_id)
        if method == "resources/list":
            return {"resources": [self._resource_to_mcp(resource) for resource in self.resources]}
        if method == "resources/read":
            return self._resources_read(params)
        if method == "prompts/list":
            return {"prompts": [self._prompt_to_mcp(prompt) for prompt in self.prompts]}
        if method == "prompts/get":
            return self._prompts_get(params)
        raise ValueError(f"Method not found: {method}")

    async def _adispatch(self, method: str, params: JsonDict, *, request_id: Any = None) -> JsonDict:
        if method in {"ping", "initialize", "notifications/initialized", "notifications/progress"}:
            return self._dispatch(method, params, request_id=request_id)
        if method == "tools/list":
            return {"tools": [self._tool_spec_to_mcp(spec) for spec in self.tools.list_tools()]}
        if method == "tools/call":
            return await self._atools_call(params, request_id=request_id)
        if method == "resources/list":
            return {"resources": [self._resource_to_mcp(resource) for resource in self.resources]}
        if method == "resources/read":
            return self._resources_read(params)
        if method == "prompts/list":
            return {"prompts": [self._prompt_to_mcp(prompt) for prompt in self.prompts]}
        if method == "prompts/get":
            return self._prompts_get(params)
        raise ValueError(f"Method not found: {method}")

    def _initialize(self, params: JsonDict) -> JsonDict:
        requested_version = params.get("protocolVersion")
        if isinstance(requested_version, str) and requested_version.strip():
            negotiated_version = requested_version
        else:
            negotiated_version = self.protocol_version

        client_info = params.get("clientInfo")
        if isinstance(client_info, dict):
            self._client_info = dict(client_info)
        else:
            self._client_info = {}

        capabilities = params.get("capabilities")
        if isinstance(capabilities, dict):
            self._client_capabilities = dict(capabilities)
        else:
            self._client_capabilities = {}

        self._initialized = True
        self._initialized_at = datetime.now(UTC)
        server_capabilities: JsonDict = {
            "tools": {"listChanged": False},
            "resources": {"listChanged": False},
            "prompts": {"listChanged": False},
            "experimental": {
                "progressNotifications": self.support_progress,
                "requestCancellation": self.support_cancellation,
            },
        }
        return {
            "protocolVersion": negotiated_version,
            "serverInfo": {
                "name": self.server_info.name,
                "version": self.server_info.version,
            },
            "capabilities": server_capabilities,
            "instructions": self.instructions,
        }

    def _tool_spec_to_mcp(self, spec: ToolSpec) -> JsonDict:
        annotations = {
            "title": spec.name,
            "riskLevel": spec.risk_level.value if isinstance(spec.risk_level, ToolRiskLevel) else str(spec.risk_level),
            "costHint": spec.cost_hint,
            "sideEffects": spec.side_effects,
        }
        return {
            "name": spec.name,
            "description": spec.description,
            "inputSchema": spec.input_schema or {"type": "object", "additionalProperties": True},
            "annotations": annotations,
        }

    def _tools_call(self, params: JsonDict, *, request_id: Any = None) -> JsonDict:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("tools/call requires string param `name`")
        arguments = params.get("arguments", {})
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise ValueError("tools/call param `arguments` must be an object")
        call_id = params.get("callId")
        if not isinstance(call_id, str) or not call_id:
            call_id = f"mcp-{datetime.now(UTC).timestamp()}"
        if self.support_cancellation and (
            (request_id is not None and self._is_request_cancelled(request_id)) or self._is_request_cancelled(call_id)
        ):
            raise ValueError("Request cancelled")

        progress_token = params.get("progressToken")
        session_id = params.get("sessionId")
        auth_fingerprint = self._token_fingerprint(params.get("auth_token"))
        if self.support_progress:
            self._emit_progress(
                progress_token=progress_token,
                progress=0,
                total=1,
                message=f"Starting tool call: {name}",
                call_id=call_id,
                session_id=session_id,
                auth_fingerprint=auth_fingerprint,
            )

        call = ToolCall(id=call_id, name=name, arguments=arguments)
        result = self._run_coro_sync(self.tools.execute_call(call, prior_results={}))
        if self.support_progress:
            self._emit_progress(
                progress_token=progress_token,
                progress=1,
                total=1,
                message=f"Finished tool call: {name}",
                call_id=call_id,
                session_id=session_id,
                auth_fingerprint=auth_fingerprint,
            )

        is_error = (not result.success) or bool(result.error)
        rendered_text = self._render_tool_output_text(result.output if result.success else result.error)
        response: JsonDict = {
            "isError": is_error,
            "content": [{"type": "text", "text": rendered_text}],
            "result": {
                "callId": result.call_id,
                "toolName": result.tool_name,
                "success": result.success,
                "error": result.error,
                "cached": result.cached,
                "approval": result.approval,
                "skipped": result.skipped,
                "output": result.output,
            },
        }
        return response

    async def _atools_call(self, params: JsonDict, *, request_id: Any = None) -> JsonDict:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("tools/call requires string param `name`")
        arguments = params.get("arguments", {})
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise ValueError("tools/call param `arguments` must be an object")
        call_id = params.get("callId")
        if not isinstance(call_id, str) or not call_id:
            call_id = f"mcp-{datetime.now(UTC).timestamp()}"

        if self.support_cancellation and (
            (request_id is not None and self._is_request_cancelled(request_id)) or self._is_request_cancelled(call_id)
        ):
            raise ValueError("Request cancelled")

        progress_token = params.get("progressToken")
        session_id = params.get("sessionId")
        auth_fingerprint = self._token_fingerprint(params.get("auth_token"))
        if self.support_progress:
            self._emit_progress(
                progress_token=progress_token,
                progress=0,
                total=1,
                message=f"Starting tool call: {name}",
                call_id=call_id,
                session_id=session_id,
                auth_fingerprint=auth_fingerprint,
            )

        req_key = str(request_id) if request_id is not None else None
        call_key = str(call_id)
        call = ToolCall(id=call_id, name=name, arguments=arguments)

        def _cancel_checker() -> bool:
            if req_key is not None and self._is_request_cancelled(req_key):
                return True
            return self._is_request_cancelled(call_key)

        task = asyncio.create_task(
            self.tools.execute_call(
                call,
                prior_results={},
                cancel_checker=_cancel_checker,
            )
        )
        self._active_call_tasks[call_key] = task
        if req_key is not None:
            self._active_call_tasks[req_key] = task

        try:
            result = await task
        except asyncio.CancelledError as exc:
            raise ValueError("Request cancelled") from exc
        finally:
            self._active_call_tasks.pop(call_key, None)
            if req_key is not None:
                self._active_call_tasks.pop(req_key, None)

        if self.support_progress:
            self._emit_progress(
                progress_token=progress_token,
                progress=1,
                total=1,
                message=f"Finished tool call: {name}",
                call_id=call_id,
                session_id=session_id,
                auth_fingerprint=auth_fingerprint,
            )

        is_error = (not result.success) or bool(result.error)
        rendered_text = self._render_tool_output_text(result.output if result.success else result.error)
        return {
            "isError": is_error,
            "content": [{"type": "text", "text": rendered_text}],
            "result": {
                "callId": result.call_id,
                "toolName": result.tool_name,
                "success": result.success,
                "error": result.error,
                "cached": result.cached,
                "approval": result.approval,
                "skipped": result.skipped,
                "output": result.output,
            },
        }

    def _resource_to_mcp(self, resource: MCPResource) -> JsonDict:
        return {
            "uri": resource.uri,
            "name": resource.name or resource.uri,
            "description": resource.description,
            "mimeType": resource.mime_type,
        }

    def _resources_read(self, params: JsonDict) -> JsonDict:
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri:
            raise ValueError("resources/read requires string param `uri`")
        resource = self._find_resource(uri)
        if resource is None:
            raise ValueError(f"Unknown resource uri: {uri}")
        if self.resource_reader is None:
            raise ValueError("resources/read is not configured: provide `resource_reader`.")
        payload = self.resource_reader(uri)
        content: JsonDict = {
            "uri": resource.uri,
            "mimeType": resource.mime_type,
        }
        if isinstance(payload, bytes):
            content["blob"] = base64.b64encode(payload).decode("ascii")
        elif isinstance(payload, str):
            content["text"] = payload
        elif payload is None:
            content["text"] = ""
        else:
            content["text"] = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
        return {"contents": [content]}

    def _prompt_to_mcp(self, prompt: MCPPrompt) -> JsonDict:
        args = []
        for arg in prompt.arguments or []:
            args.append(
                {
                    "name": arg.name,
                    "description": arg.description,
                    "required": arg.required,
                }
            )
        return {
            "name": prompt.name,
            "description": prompt.description,
            "arguments": args,
        }

    def _prompts_get(self, params: JsonDict) -> JsonDict:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("prompts/get requires string param `name`")
        arguments = params.get("arguments", {})
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise ValueError("prompts/get param `arguments` must be an object")

        prompt = self._find_prompt(name)
        if prompt is None:
            raise ValueError(f"Unknown prompt: {name}")

        for arg in prompt.arguments or []:
            if arg.required and arg.name not in arguments:
                raise ValueError(f"Missing required prompt argument: {arg.name}")

        if self.prompt_renderer is not None:
            rendered = self.prompt_renderer(name, arguments)
            if isinstance(rendered, dict):
                return rendered
            text = str(rendered)
        elif prompt.template is not None:
            try:
                text = prompt.template.format(**arguments)
            except KeyError as exc:
                raise ValueError(f"Missing prompt template argument: {exc.args[0]}") from exc
        else:
            raise ValueError("prompts/get is not configured: provide `prompt_renderer` or prompt templates.")

        return {
            "description": prompt.description,
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": text,
                    },
                }
            ],
        }

    def _find_resource(self, uri: str) -> MCPResource | None:
        for resource in self.resources:
            if resource.uri == uri:
                return resource
        return None

    def _find_prompt(self, name: str) -> MCPPrompt | None:
        for prompt in self.prompts:
            if prompt.name == name:
                return prompt
        return None

    def _record_cancel_request(self, params: JsonDict) -> None:
        request_id = params.get("id")
        if request_id is None:
            request_id = params.get("requestId")
        if request_id is None:
            request_id = params.get("callId")
        if request_id is None:
            return
        request_id_str = str(request_id)
        self._cancelled_request_ids.add(request_id_str)
        task = self._active_call_tasks.get(request_id_str)
        if task is not None:
            task.cancel()

    def _is_request_cancelled(self, request_id: Any) -> bool:
        return str(request_id) in self._cancelled_request_ids

    def _record_inbound_progress(self, params: JsonDict) -> None:
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "direction": "inbound",
            "progressToken": params.get("progressToken"),
            "progress": params.get("progress"),
            "total": params.get("total"),
            "message": params.get("message"),
            "callId": params.get("callId"),
        }
        self._progress_events.append(event)

    def _emit_progress(
        self,
        *,
        progress_token: Any,
        progress: int,
        total: int,
        message: str,
        call_id: str,
        session_id: Any = None,
        auth_fingerprint: str | None = None,
    ) -> None:
        if progress_token is None:
            return
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "direction": "outbound",
            "progressToken": progress_token,
            "progress": progress,
            "total": total,
            "message": message,
            "callId": call_id,
        }
        if isinstance(session_id, str) and session_id.strip():
            event["sessionId"] = session_id.strip()
        if isinstance(auth_fingerprint, str) and auth_fingerprint:
            event["authFingerprint"] = auth_fingerprint
        self._progress_events.append(event)
        if self.progress_handler is not None:
            self.progress_handler(dict(event))
        for listener in self._progress_listeners:
            listener(dict(event))

    def _token_fingerprint(self, token: Any) -> str | None:
        if not isinstance(token, str):
            return None
        normalized = token.strip()
        if not normalized:
            return None
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return digest[:16]

    def _run_coro_sync(self, coro: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        close_fn = getattr(coro, "close", None)
        if callable(close_fn):
            close_fn()
        raise RuntimeError(
            "MCPJsonRpcServer sync methods cannot run inside an active asyncio event loop. "
            "Call from non-async context or add an async wrapper."
        )

    def _render_tool_output_text(self, payload: Any) -> str:
        if payload is None:
            return ""
        if isinstance(payload, str):
            return payload
        try:
            return json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
        except Exception:
            return str(payload)

    def _error_response(self, request_id: Any, code: int, message: str, *, data: JsonDict | None = None) -> JsonDict:
        error: JsonDict = {"code": code, "message": message}
        if data:
            error["data"] = data
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": error,
        }

    def _unauthorized_response(self, request_id: Any, decision: MCPAuthDecision) -> JsonDict:
        data: JsonDict = {}
        if decision.www_authenticate:
            data["www_authenticate"] = decision.www_authenticate
        if decision.details:
            data["details"] = decision.details
        return self._error_response(
            request_id,
            decision.error_code,
            decision.message,
            data=data or None,
        )


def run_mcp_stdio(server: MCPJsonRpcServer) -> None:
    """
    Line-delimited stdio JSON-RPC loop.

    Reads one JSON request object per line and writes one JSON response object per line
    for request messages. Notifications do not emit responses.
    """
    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        response = server.handle_json(raw)
        if response is None:
            continue
        sys.stdout.write(response + "\n")
        sys.stdout.flush()
