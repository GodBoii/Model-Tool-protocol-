"""Bounded, opt-in modern MCP interactions and request notification context."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import math
import queue
import secrets
import threading
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

_CONTEXT: ContextVar[MCPRequestContext | None] = ContextVar(
    "mtp_mcp_request", default=None
)
_SUB_KEY = "io.modelcontextprotocol/subscriptionId"
_NOTIFICATIONS = {
    "notifications/tools/list_changed": "toolsListChanged",
    "notifications/prompts/list_changed": "promptsListChanged",
    "notifications/resources/list_changed": "resourcesListChanged",
    "notifications/resources/updated": "resourceSubscriptions",
}


def _json_copy(value: Any, limit: int = 262144) -> Any:
    raw = json.dumps(value, allow_nan=False)
    if len(raw.encode()) > limit:
        raise ValueError("MCP feature payload exceeds its size limit")
    return json.loads(raw)


def _identity(token: str | None) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


@dataclass
class MCPRequestContext:
    request_id: str | int
    principal: str
    capabilities: dict[str, Any]
    progress_token: str | int | None = None
    notify: Callable[[dict[str, Any]], None] | None = None
    _execute_tool: Callable[[dict[str, Any] | None], Any] | None = field(
        default=None, repr=False
    )
    _last_progress: float = field(default=-math.inf, init=False)

    async def execute_tool(
        self, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Execute this request's original tool through registry policy and approval."""
        if self._execute_tool is None:
            raise RuntimeError("This request is not a tool call")
        return await self._execute_tool(arguments)

    def progress(
        self, value: float, *, total: float | None = None, message: str | None = None
    ) -> None:
        if self.notify is None or self.progress_token is None:
            return
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or value <= self._last_progress
        ):
            raise ValueError("Progress must be finite and strictly increase")
        if total is not None and (
            type(total) not in (int, float) or not math.isfinite(total) or total < value
        ):
            raise ValueError("Progress total must be finite and at least progress")
        params: dict[str, Any] = {
            "progressToken": self.progress_token,
            "progress": value,
        }
        if total is not None:
            params["total"] = total
        if message is not None:
            if not isinstance(message, str):
                raise ValueError("Progress message must be a string")
            params["message"] = message
        self.notify(
            _json_copy(
                {"jsonrpc": "2.0", "method": "notifications/progress", "params": params}
            )
        )
        self._last_progress = value


def current_mcp_context() -> MCPRequestContext:
    context = _CONTEXT.get()
    if context is None:
        raise RuntimeError("No modern MCP request is active")
    return context


@dataclass
class MCPInputRequired:
    input_requests: dict[str, dict[str, Any]]
    continuation: Callable[[dict[str, Any], MCPRequestContext], Any]


@dataclass
class _Resume:
    principal: str
    digest: str
    request_id: str | int
    expires: float
    interaction: MCPInputRequired
    status: str = "pending"
    result: Any = None
    response_digest: str | None = None


@dataclass
class _Subscription:
    key: str
    principal: str
    request_id: str | int
    filters: dict[str, Any]
    events: queue.Queue[Any]


class MissingClientCapability(ValueError):
    def __init__(self, required: str) -> None:
        super().__init__("Required client capability is missing")
        self.required = required


class ModernMCPFeatures:
    def __init__(
        self,
        *,
        max_states: int = 256,
        state_ttl_seconds: float = 300,
        max_subscriptions: int = 128,
        queue_size: int = 64,
    ) -> None:
        if any(
            type(value) is not int or value <= 0
            for value in (max_states, max_subscriptions, queue_size)
        ):
            raise ValueError("Feature limits must be positive integers")
        if not math.isfinite(state_ttl_seconds) or state_ttl_seconds <= 0:
            raise ValueError("State TTL must be finite and positive")
        self.max_states = max_states
        self.state_ttl_seconds = state_ttl_seconds
        self.max_subscriptions = max_subscriptions
        self.queue_size = queue_size
        self._callbacks: dict[tuple[str, str], Callable[..., Any]] = {}
        self._states: dict[str, _Resume] = {}
        self._subscriptions: dict[str, _Subscription] = {}
        self._lock = threading.Lock()

    def register_interaction(
        self,
        method: str,
        name: str,
        callback: Callable[[dict[str, Any], MCPRequestContext], Any],
    ) -> None:
        if (
            method not in {"tools/call", "resources/read", "prompts/get"}
            or not isinstance(name, str)
            or not name
            or not callable(callback)
        ):
            raise ValueError("MRTR requires a supported method, name, and callback")
        self._callbacks[(method, name)] = callback

    def has_interaction(self, method: str, params: dict[str, Any]) -> bool:
        name = params.get("uri" if method == "resources/read" else "name")
        return isinstance(name, str) and (method, name) in self._callbacks

    def _digest(self, method: str, params: dict[str, Any]) -> str:
        salient = {
            key: value
            for key, value in params.items()
            if key
            not in {"_meta", "auth_token", "requestState", "inputResponses", "callId"}
        }
        return hashlib.sha256(
            json.dumps([method, salient], sort_keys=True, allow_nan=False).encode()
        ).hexdigest()

    def _validate_inputs(
        self, inputs: dict[str, Any], context: MCPRequestContext
    ) -> None:
        if not isinstance(inputs, dict) or not inputs or len(inputs) > 32:
            raise ValueError(
                "Input requests must be a nonempty map of at most 32 entries"
            )
        _json_copy(inputs)
        for key, request in inputs.items():
            if (
                not isinstance(key, str)
                or not key
                or not isinstance(request, dict)
                or not isinstance(request.get("params"), dict)
            ):
                raise ValueError("Invalid input request")
            method = request.get("method")
            capability = {
                "roots/list": "roots",
                "sampling/createMessage": "sampling",
                "elicitation/create": "elicitation",
            }.get(method)
            if capability is None:
                raise ValueError("Unsupported input request method")
            if not isinstance(context.capabilities.get(capability), dict):
                raise MissingClientCapability(capability)
            params = request["params"]
            if method == "elicitation/create":
                mode = params.get("mode", "form")
                if (
                    mode not in {"form", "url"}
                    or mode not in context.capabilities[capability]
                ):
                    raise MissingClientCapability("elicitation." + str(mode))
                if not isinstance(params.get("message"), str):
                    raise ValueError("Elicitation requires a message")
                if mode == "form" and not isinstance(
                    params.get("requestedSchema"), dict
                ):
                    raise ValueError("Form elicitation requires requestedSchema")
            if method == "sampling/createMessage" and (
                not isinstance(params.get("messages"), list)
                or type(params.get("maxTokens")) is not int
                or params["maxTokens"] <= 0
            ):
                raise ValueError("Sampling requires messages and positive maxTokens")

    async def interact(
        self, method: str, params: dict[str, Any], context: MCPRequestContext
    ) -> dict[str, Any]:
        digest = self._digest(method, params)
        token = params.get("requestState")
        if token is not None:
            if not isinstance(token, str):
                raise ValueError("Invalid requestState")
            with self._lock:
                state = self._states.get(token)
                if (
                    state is None
                    or state.expires <= time.monotonic()
                    or state.principal != context.principal
                    or state.digest != digest
                    or state.request_id == context.request_id
                ):
                    raise ValueError("Invalid or expired requestState for this request")
                responses = params.get("inputResponses", {})
                if not isinstance(responses, dict):
                    raise ValueError("inputResponses must be an object")  # noqa: TRY004
                _json_copy(responses)
                responses = {
                    key: _json_copy(value)
                    for key, value in responses.items()
                    if key in state.interaction.input_requests
                }
                missing = set(state.interaction.input_requests) - responses.keys()
                if missing:
                    self._validate_inputs(state.interaction.input_requests, context)
                    return {
                        "resultType": "input_required",
                        "inputRequests": {
                            key: state.interaction.input_requests[key]
                            for key in missing
                        },
                        "requestState": token,
                    }
                response_digest = hashlib.sha256(
                    json.dumps(responses, sort_keys=True).encode()
                ).hexdigest()
                if state.status == "complete":
                    if state.response_digest != response_digest:
                        raise ValueError(
                            "requestState was already consumed with different input"
                        )
                    return copy.deepcopy(state.result)
                if state.status != "pending":
                    raise ValueError("requestState is already in use or failed")
                self._validate_responses(state.interaction.input_requests, responses)
                state.status = "running"
                state.response_digest = response_digest
            try:
                result = state.interaction.continuation(responses, context)
                if inspect.isawaitable(result):
                    result = await result
                result = await self._result(result, method, params, context)
            except BaseException:
                with self._lock:
                    state.status = "failed"
                raise
            with self._lock:
                state.status = "complete"
                state.result = _json_copy(result)
            return result
        if "inputResponses" in params:
            raise ValueError("inputResponses requires requestState")
        callback = self._callbacks[
            (method, params.get("uri" if method == "resources/read" else "name"))
        ]
        result = callback(params, context)
        if inspect.isawaitable(result):
            result = await result
        return await self._result(result, method, params, context)

    def _validate_responses(
        self, inputs: dict[str, Any], responses: dict[str, Any]
    ) -> None:
        from jsonschema import Draft202012Validator
        from referencing import Registry
        from referencing.exceptions import NoSuchResource

        def reject(uri: str) -> Any:
            raise NoSuchResource(ref=uri)

        for key, request in inputs.items():
            result = responses[key]
            if not isinstance(result, dict):
                raise ValueError("Each input response must be an object")  # noqa: TRY004
            method = request["method"]
            if method == "roots/list" and (
                not isinstance(result.get("roots"), list)
                or any(
                    not isinstance(root, dict)
                    or not isinstance(root.get("uri"), str)
                    or not root["uri"].startswith("file://")
                    for root in result["roots"]
                )
            ):
                raise ValueError("Invalid roots response")
            if method == "sampling/createMessage" and (
                result.get("role") not in {"user", "assistant"}
                or not isinstance(result.get("model"), str)
                or not isinstance(result.get("content"), (dict, list))
            ):
                raise ValueError("Invalid sampling response")
            if method == "elicitation/create":
                if result.get("action") not in {"accept", "decline", "cancel"}:
                    raise ValueError("Invalid elicitation action")
                if (
                    result["action"] == "accept"
                    and request["params"].get("mode", "form") == "form"
                ):
                    schema = request["params"]["requestedSchema"]
                    try:
                        Draft202012Validator(
                            schema, registry=Registry(retrieve=reject)
                        ).validate(result.get("content"))
                    except Exception as exc:
                        raise ValueError("Invalid elicitation content") from exc

    async def _result(
        self,
        result: Any,
        method: str,
        params: dict[str, Any],
        context: MCPRequestContext,
    ) -> dict[str, Any]:
        if not isinstance(result, MCPInputRequired):
            if not isinstance(result, dict):
                raise ValueError("Interaction must return an MCP result object")  # noqa: TRY004
            return _json_copy(result)
        self._validate_inputs(result.input_requests, context)
        with self._lock:
            now = time.monotonic()
            self._states = {
                key: state
                for key, state in self._states.items()
                if state.expires > now or state.status == "running"
            }
            if len(self._states) >= self.max_states:
                raise ValueError("MRTR state capacity reached")
            token = secrets.token_urlsafe(32)
            self._states[token] = _Resume(
                context.principal,
                self._digest(method, params),
                context.request_id,
                now + self.state_ttl_seconds,
                result,
            )
        return {
            "resultType": "input_required",
            "inputRequests": _json_copy(result.input_requests),
            "requestState": token,
        }

    async def listen(
        self, params: dict[str, Any], context: MCPRequestContext
    ) -> dict[str, Any]:
        if context.notify is None:
            raise ValueError("subscriptions/listen requires a streaming transport")
        filters = params.get("notifications")
        if not isinstance(filters, dict):
            raise ValueError("notifications filter must be an object")  # noqa: TRY004
        accepted = {}
        for name, value in filters.items():
            if name not in _NOTIFICATIONS.values():
                continue
            if name == "resourceSubscriptions":
                if (
                    not isinstance(value, list)
                    or len(value) > 128
                    or any(not isinstance(uri, str) for uri in value)
                ):
                    raise ValueError(
                        "resourceSubscriptions must contain at most 128 strings"
                    )
                accepted[name] = list(dict.fromkeys(value))
            elif type(value) is not bool:
                raise ValueError("Subscription list filters must be booleans")
            elif value:
                accepted[name] = True
        sub = _Subscription(
            secrets.token_hex(16),
            context.principal,
            context.request_id,
            accepted,
            queue.Queue(self.queue_size),
        )
        with self._lock:
            if len(self._subscriptions) >= self.max_subscriptions:
                raise ValueError("Subscription capacity reached")
            self._subscriptions[sub.key] = sub
        try:
            context.notify(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/subscriptions/acknowledged",
                    "params": {
                        "_meta": {_SUB_KEY: context.request_id},
                        "notifications": accepted,
                    },
                }
            )
            while True:
                try:
                    event = sub.events.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.025)
                    continue
                if event is None:
                    return {"_meta": {_SUB_KEY: context.request_id}}
                context.notify(event)
        finally:
            with self._lock:
                self._subscriptions.pop(sub.key, None)

    def publish(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        auth_token: str | None = None,
    ) -> int:
        if method not in _NOTIFICATIONS:
            raise ValueError("Unsupported subscription notification")
        params = _json_copy(params or {})
        principal = _identity(auth_token)
        sent = 0
        with self._lock:
            for sub in self._subscriptions.values():
                if sub.principal != principal:
                    continue
                field = _NOTIFICATIONS[method]
                if field == "resourceSubscriptions":
                    if params.get("uri") not in sub.filters.get(field, []):
                        continue
                elif not sub.filters.get(field):
                    continue
                event = {
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": {**params, "_meta": {_SUB_KEY: sub.request_id}},
                }
                try:
                    sub.events.put_nowait(event)
                    sent += 1
                except queue.Full:
                    # Tear down rather than lose an invalidation while pretending success.
                    while not sub.events.empty():
                        sub.events.get_nowait()
                    sub.events.put_nowait(None)
        return sent

    def close(self) -> None:
        with self._lock:
            for sub in self._subscriptions.values():
                while not sub.events.empty():
                    sub.events.get_nowait()
                sub.events.put_nowait(None)


def configure_modern_mcp(server: Any, features: ModernMCPFeatures) -> None:
    if not getattr(server, "modern_enabled", False):
        raise ValueError("Modern features require enable_modern=True")
    server._modern_requests.features = features


@dataclass
class MCPOAuthMetadata:
    resource: str
    authorization_servers: list[str]
    scopes_supported: list[str] = field(default_factory=list)
    authorization_server_metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        for value in [self.resource, *self.authorization_servers]:
            if not isinstance(value, str) or any(
                ord(c) < 33 or ord(c) > 126 or c in {'"', "\\"} for c in value
            ):
                raise ValueError(
                    "OAuth metadata URLs must contain safe ASCII URI characters"
                )
            parsed = urlsplit(value)
            if (
                not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.fragment
                or parsed.query
                or (
                    parsed.scheme != "https"
                    and not (
                        parsed.scheme == "http"
                        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                    )
                )
            ):
                raise ValueError(
                    "OAuth metadata URLs require HTTPS or local HTTP and no credentials, query, or fragment"
                )
        if not self.authorization_servers:
            raise ValueError("At least one authorization server is required")
        if any(
            not isinstance(scope, str)
            or not scope
            or '"' in scope
            or "\\" in scope
            or any(ord(c) < 33 or ord(c) > 126 for c in scope)
            for scope in self.scopes_supported
        ):
            raise ValueError("Invalid OAuth scope")
        if self.authorization_server_metadata is not None:
            metadata = _json_copy(self.authorization_server_metadata)
            if metadata.get("issuer") not in self.authorization_servers:
                raise ValueError(
                    "Authorization metadata issuer must match a configured server"
                )
            for key in ("authorization_endpoint", "token_endpoint"):
                if (
                    not isinstance(metadata.get(key), str)
                    or urlsplit(metadata[key]).scheme != "https"
                ):
                    raise ValueError(
                        "Authorization metadata requires HTTPS authorization and token endpoints"
                    )
            self.authorization_server_metadata = metadata

    @property
    def metadata_path(self) -> str:
        return "/.well-known/oauth-protected-resource" + urlsplit(
            self.resource
        ).path.rstrip("/")

    @property
    def metadata_url(self) -> str:
        parsed = urlsplit(self.resource)
        return f"{parsed.scheme}://{parsed.netloc}{self.metadata_path}"

    def protected_resource(self) -> dict[str, Any]:
        return {
            "resource": self.resource,
            "authorization_servers": list(self.authorization_servers),
            "bearer_methods_supported": ["header"],
            "scopes_supported": list(self.scopes_supported),
        }
