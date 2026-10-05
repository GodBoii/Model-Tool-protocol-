"""Stateless MCP requests, separate from the legacy initialization lifecycle."""

from __future__ import annotations

import copy
import json
import logging
import uuid
from typing import Any

MODERN_PROTOCOL_VERSION = "2026-07-28"
PROTOCOL_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_CAPABILITIES_KEY = "io.modelcontextprotocol/clientCapabilities"
CLIENT_INFO_KEY = "io.modelcontextprotocol/clientInfo"
SERVER_INFO_KEY = "io.modelcontextprotocol/serverInfo"
_METHODS = {
    "server/discover",
    "ping",
    "tools/list",
    "tools/call",
    "resources/list",
    "resources/read",
    "prompts/list",
    "prompts/get",
}
_DIALECTS = {
    "https://json-schema.org/draft/2020-12/schema",
    "https://json-schema.org/draft/2020-12/schema#",
}
_LOG = logging.getLogger(__name__)


def is_modern_request(request: dict[str, Any]) -> bool:
    params = request.get("params")
    meta = params.get("_meta") if isinstance(params, dict) else None
    return request.get("method") == "server/discover" or (
        isinstance(meta, dict) and PROTOCOL_VERSION_KEY in meta
    )


class ModernMCPRequests:
    def __init__(self, server: Any) -> None:
        try:
            from jsonschema import Draft202012Validator
            from referencing import Registry
        except ImportError as exc:
            raise ImportError(
                "Modern MCP requires the mtpx[mcp-modern] extra."
            ) from exc
        self.server = server
        self.validator = Draft202012Validator

        def reject_external(uri: str) -> Any:
            from referencing.exceptions import NoSuchResource

            raise NoSuchResource(ref=uri)

        self.registry = Registry(retrieve=reject_external)
        self.features = None

    def validate(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        error = self.server._error_response
        if "id" not in request or type(request_id) not in (str, int):
            return error(
                None, -32600, "Modern requests require a non-null string or integer id."
            )
        issue = self.server._validate_request(request)
        if issue:
            return error(request_id, -32600, issue)
        params = request.get("params", {})
        if not isinstance(params, dict):
            return error(request_id, -32602, "Modern request params must be an object.")
        try:
            json.dumps(params, allow_nan=False)
        except (TypeError, ValueError):
            return error(
                request_id,
                -32602,
                "Modern request params must contain valid JSON values.",
            )
        meta = params.get("_meta")
        if not isinstance(meta, dict):
            return error(request_id, -32602, "Required per-request _meta is missing.")
        revision = meta.get(PROTOCOL_VERSION_KEY)
        if not isinstance(revision, str):
            return error(
                request_id,
                -32602,
                "Required protocolVersion metadata must be a string.",
            )
        if revision != MODERN_PROTOCOL_VERSION:
            return error(
                request_id,
                -32022,
                "Unsupported protocol version",
                data={
                    "supported": [MODERN_PROTOCOL_VERSION],
                    "requested": revision,
                },
            )
        if not isinstance(meta.get(CLIENT_CAPABILITIES_KEY), dict):
            return error(
                request_id,
                -32602,
                "Required clientCapabilities metadata must be an object.",
            )
        if CLIENT_INFO_KEY in meta:
            info = meta[CLIENT_INFO_KEY]
            if not isinstance(info, dict) or any(
                not isinstance(info.get(key), str) for key in ("name", "version")
            ):
                return error(
                    request_id,
                    -32602,
                    "clientInfo must include string name and version.",
                )
        if "progressToken" in meta and type(meta["progressToken"]) not in (str, int):
            return error(
                request_id, -32602, "progressToken must be a string or integer."
            )
        if request["method"] not in _METHODS and not (
            self.features is not None and request["method"] == "subscriptions/listen"
        ):
            return error(request_id, -32601, f"Method not found: {request['method']}")
        return None

    def _info(self) -> dict[str, Any]:
        return {
            "name": self.server.server_info.name,
            "version": self.server.server_info.version,
        }

    def _schema(self, schema: dict[str, Any]) -> Any:
        from jsonschema.exceptions import SchemaError

        if (
            schema.get("$schema", "https://json-schema.org/draft/2020-12/schema")
            not in _DIALECTS
        ):
            raise ValueError("Modern MCP supports JSON Schema 2020-12 only.")
        # Bound traversal before calling the validator on application-supplied schemas.
        stack = [(schema, 0)]
        visited = 0
        while stack:
            value, depth = stack.pop()
            visited += 1
            if depth > 64 or visited > 10000:
                raise ValueError("Tool schema exceeds modern MCP validation limits.")
            if isinstance(value, dict):
                stack.extend((child, depth + 1) for child in value.values())
            elif isinstance(value, list):
                stack.extend((child, depth + 1) for child in value)
        try:
            self.validator.check_schema(schema)
        except SchemaError as exc:
            raise ValueError("Invalid JSON Schema 2020-12 tool definition.") from exc
        return self.validator(schema, registry=self.registry)

    def _tools(self) -> list[dict[str, Any]]:
        result = []
        for spec in self.server.tools.list_tools():
            tool = self.server._tool_spec_to_mcp(spec)
            self._schema(tool["inputSchema"])
            result.append(tool)
        return result

    def _params(self, request: dict[str, Any]) -> dict[str, Any]:
        params = copy.deepcopy(request["params"])
        # Request IDs and application call IDs cannot share legacy cancellation state.
        params.pop("progressToken", None)
        params.pop("sessionId", None)
        params["callId"] = "modern-" + uuid.uuid4().hex
        params["_meta"].pop("progressToken", None)
        return params

    def _validate_arguments(self, params: dict[str, Any]) -> None:
        from jsonschema.exceptions import ValidationError
        from referencing.exceptions import Unresolvable

        name = params.get("name")
        tool = next((tool for tool in self._tools() if tool["name"] == name), None)
        if tool is None:
            raise ValueError("Unknown tool name.")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object.")  # noqa: TRY004
        try:
            self._schema(tool["inputSchema"]).validate(arguments)
        except ValidationError as exc:
            raise ValueError(
                "Tool arguments do not satisfy their JSON Schema 2020-12 definition."
            ) from exc
        except Unresolvable as exc:
            raise ValueError("External schema references are disabled.") from exc

    def _validate_raw_arguments(
        self, arguments: Any, schema: dict[str, Any] | None
    ) -> None:
        from jsonschema.exceptions import ValidationError
        from referencing.exceptions import Unresolvable

        from .schema import ToolArgumentsValidationError

        try:
            self._schema(schema or {"type": "object"}).validate(arguments)
        except (ValidationError, Unresolvable, ValueError) as exc:
            raise ToolArgumentsValidationError(
                "Invalid JSON Schema 2020-12 tool arguments."
            ) from exc

    def _auth_failure(self, request_id: Any, decision: Any) -> dict[str, Any]:
        response = self.server._unauthorized_response(request_id, decision)
        code = response["error"]["code"]
        if -32099 <= code <= -32000:
            response["error"]["code"] = 1001
        data = response["error"].setdefault("data", {})
        data["http_status"] = (
            403
            if isinstance(decision.details, dict)
            and decision.details.get("http_status") == 403
            else 401
        )
        return response

    def _complete(self, result: dict[str, Any]) -> dict[str, Any]:
        result = json.loads(json.dumps(result, default=str, allow_nan=False))
        result.setdefault("resultType", "complete")
        meta = result.setdefault("_meta", {})
        meta[SERVER_INFO_KEY] = self._info()
        return result

    def _discover(self) -> dict[str, Any]:
        from .mcp import SUPPORTED_PROTOCOL_VERSIONS

        capabilities = {"tools": {}}
        if self.server.resources and self.server.resource_reader is not None:
            capabilities["resources"] = {}
        if self.server.prompts:
            capabilities["prompts"] = {}
        if self.features is not None:
            capabilities["tools"]["listChanged"] = True
            if "resources" in capabilities:
                capabilities["resources"].update(
                    {"listChanged": True, "subscribe": True}
                )
            if "prompts" in capabilities:
                capabilities["prompts"]["listChanged"] = True
        return {
            "supportedVersions": [
                MODERN_PROTOCOL_VERSION,
                *SUPPORTED_PROTOCOL_VERSIONS,
            ],
            "capabilities": capabilities,
            "instructions": self.server.instructions,
        }

    async def handle(
        self, request: dict[str, Any], *, notify: Any = None, _authorized: bool = False
    ) -> dict[str, Any] | None:
        from .mcp_features import (
            _CONTEXT,
            MCPRequestContext,
            MissingClientCapability,
            _identity,
        )

        if "id" not in request:
            # Neither success nor failure of a notification gets an RPC response.
            return None
        issue = self.validate(request)
        if issue is not None:
            return issue
        method = request["method"]
        request_id = request["id"]
        if not _authorized and self.server._requires_auth(method):
            decision = await self.server._authorized_async(
                request,
                method=method,
                request_id=request_id,
                params=request["params"],
            )
            if not decision.allowed:
                return self._auth_failure(request_id, decision)
        params = self._params(request)
        metadata = request["params"]["_meta"]
        context = MCPRequestContext(
            request_id,
            _identity(self.server._extract_auth_token(request)),
            metadata[CLIENT_CAPABILITIES_KEY],
            metadata.get("progressToken"),
            notify,
        )
        context_token = _CONTEXT.set(context)
        if method == "tools/call":

            async def execute_tool(
                arguments: dict[str, Any] | None = None,
            ) -> dict[str, Any]:
                resolved = copy.deepcopy(params)
                if arguments is not None:
                    resolved["arguments"] = arguments
                self._validate_arguments(resolved)
                return await self.server._atools_call(
                    resolved,
                    request_id=None,
                    argument_validator=self._validate_raw_arguments,
                )

            context._execute_tool = execute_tool
        try:
            if method == "server/discover":
                result = self._discover()
            elif method == "tools/list":
                result = {"tools": self._tools()}
            elif method == "tools/call":
                self._validate_arguments(params)
                if self.features is not None and self.features.has_interaction(
                    method, params
                ):
                    result = await self.features.interact(method, params, context)
                else:
                    if "requestState" in params or "inputResponses" in params:
                        raise ValueError(
                            "This tool has no configured input interaction"
                        )
                    result = await self.server._atools_call(
                        params,
                        request_id=None,
                        argument_validator=self._validate_raw_arguments,
                    )
            elif method == "subscriptions/listen" and self.features is not None:
                result = await self.features.listen(params, context)
            elif self.features is not None and self.features.has_interaction(
                method, params
            ):
                result = await self.features.interact(method, params, context)
            else:
                if "requestState" in params or "inputResponses" in params:
                    raise ValueError("This request has no configured input interaction")
                result = await self.server._adispatch(method, params, request_id=None)
        except MissingClientCapability as exc:
            capability, _, child = exc.required.partition(".")
            return self.server._error_response(
                request_id,
                -32021,
                "Required client capability is missing.",
                data={
                    "requiredCapabilities": {capability: {child: {}} if child else {}}
                },
            )
        except ValueError as exc:
            return self.server._error_response(request_id, -32602, str(exc))
        except Exception:  # noqa: BLE001
            _LOG.error("Modern MCP request failed for method %s", method)
            return self.server._error_response(
                request_id, -32603, "Internal MCP request failure."
            )
        finally:
            _CONTEXT.reset(context_token)
        try:
            completed = self._complete(result)
            if (
                method
                in {
                    "server/discover",
                    "tools/list",
                    "resources/list",
                    "prompts/list",
                    "resources/read",
                }
                and completed["resultType"] == "complete"
            ):
                completed.setdefault("cacheScope", "private")
                completed.setdefault("ttlMs", 0)
                if (
                    "requestState" in request["params"]
                    or "inputResponses" in request["params"]
                ):
                    completed["ttlMs"] = 0
        except (TypeError, ValueError):
            return self.server._error_response(
                request_id, -32603, "Result cannot be serialized as valid JSON."
            )
        return {"jsonrpc": "2.0", "id": request_id, "result": completed}

    def handle_sync(self, request: dict[str, Any]) -> dict[str, Any] | None:
        return self.server._run_coro_sync(self.handle(request))
