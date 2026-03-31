from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable, Protocol

from .policy import PolicyDecision, RiskPolicy
from .protocol import ExecutionPlan, ToolCall, ToolResult, ToolSpec
from .schema import ToolArgumentsValidationError, validate_execution_plan, validate_tool_arguments

ToolHandler = Callable[..., Any] | Callable[..., Awaitable[Any]]
ApprovalHandler = Callable[[ToolSpec, ToolCall, dict[str, Any]], bool | Awaitable[bool]]


class ToolkitLoader(Protocol):
    def load_tools(self) -> list["RegisteredTool"]:
        ...

    def list_tool_specs(self) -> list[ToolSpec]:
        ...


@dataclass(slots=True)
class RegisteredTool:
    spec: ToolSpec
    handler: ToolHandler


@dataclass(slots=True)
class _CacheEntry:
    value: Any
    expires_at: datetime

    def valid(self) -> bool:
        return datetime.now(UTC) < self.expires_at


class ToolRegistry:
    def __init__(
        self,
        policy: RiskPolicy | None = None,
        *,
        max_cache_entries: int = 1024,
        approval_handler: ApprovalHandler | None = None,
    ) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._toolkit_loaders: dict[str, ToolkitLoader] = {}
        self._loaded_toolkits: set[str] = set()
        self._cache: dict[tuple[str, str], _CacheEntry] = {}
        self._tool_specs_cache: list[ToolSpec] | None = None
        self.policy = policy or RiskPolicy()
        self.max_cache_entries = max_cache_entries
        self.approval_handler = approval_handler

    def register_tool(self, spec: ToolSpec, handler: ToolHandler) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = RegisteredTool(spec=spec, handler=handler)
        self._tool_specs_cache = None

    def register_toolkit_loader(self, toolkit_name: str, loader: ToolkitLoader) -> None:
        self._toolkit_loaders[toolkit_name] = loader
        self._tool_specs_cache = None

    def list_tools(self) -> list[ToolSpec]:
        if self._tool_specs_cache is not None:
            return list(self._tool_specs_cache)
        specs: dict[str, ToolSpec] = {name: entry.spec for name, entry in self._tools.items()}
        for loader in self._toolkit_loaders.values():
            list_fn = getattr(loader, "list_tool_specs", None)
            if callable(list_fn):
                preview_specs = list_fn()
                if not preview_specs:
                    continue
                for spec in preview_specs:
                    specs.setdefault(spec.name, spec)
        self._tool_specs_cache = list(specs.values())
        return list(self._tool_specs_cache)

    def _cache_key(self, tool_name: str, arguments: dict[str, Any]) -> tuple[str, str]:
        canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
        return tool_name, canonical

    def _evict_expired_cache_entries(self) -> None:
        expired = [key for key, entry in self._cache.items() if not entry.valid()]
        for key in expired:
            self._cache.pop(key, None)

    def _enforce_cache_limit(self) -> None:
        if self.max_cache_entries <= 0:
            self._cache.clear()
            return
        if len(self._cache) <= self.max_cache_entries:
            return
        ordered = sorted(self._cache.items(), key=lambda item: item[1].expires_at)
        overflow = len(self._cache) - self.max_cache_entries
        for key, _ in ordered[:overflow]:
            self._cache.pop(key, None)

    def _load_toolkit(self, toolkit_name: str) -> None:
        if toolkit_name in self._loaded_toolkits:
            return
        loader = self._toolkit_loaders.get(toolkit_name)
        if loader is None:
            return
        for tool in loader.load_tools():
            if tool.spec.name not in self._tools:
                self._tools[tool.spec.name] = tool
                self._tool_specs_cache = None
        self._loaded_toolkits.add(toolkit_name)

    def ensure_tools_available(self, tool_names: list[str]) -> None:
        missing = [name for name in tool_names if name not in self._tools]
        if not missing:
            return
        prefixes = {name.split(".", 1)[0] for name in missing if "." in name}
        for prefix in prefixes:
            self._load_toolkit(prefix)

    async def _invoke(self, handler: ToolHandler, args: dict[str, Any]) -> Any:
        result = handler(**args)
        if inspect.isawaitable(result):
            return await result
        return result

    async def _should_allow_ask(self, spec: ToolSpec, call: ToolCall, args: dict[str, Any]) -> bool:
        if self.approval_handler is None:
            return False
        decision = self.approval_handler(spec, call, args)
        if inspect.isawaitable(decision):
            return bool(await decision)
        return bool(decision)

    def _resolve_refs(self, value: Any, results: dict[str, ToolResult]) -> Any:
        if isinstance(value, dict):
            if "$ref" in value and len(value) == 1:
                ref_id = value["$ref"]
                if ref_id not in results:
                    raise KeyError(f"Missing tool result reference: {ref_id}")
                return results[ref_id].output
            return {k: self._resolve_refs(v, results) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve_refs(v, results) for v in value]
        return value

    async def execute_call(self, call: ToolCall, prior_results: dict[str, ToolResult]) -> ToolResult:
        self.ensure_tools_available([call.name])
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(
                call_id=call.id,
                tool_name=call.name,
                output=None,
                success=False,
                error=f"Unknown tool: {call.name}",
            )

        resolved_args = self._resolve_refs(call.arguments, prior_results)
        try:
            validate_tool_arguments(resolved_args, tool.spec.input_schema)
        except ToolArgumentsValidationError as exc:
            return ToolResult(
                call_id=call.id,
                tool_name=call.name,
                output=None,
                success=False,
                error=f"Invalid tool arguments: {exc}",
            )
        decision = self.policy.decide(tool.spec, call, resolved_args)
        if decision == PolicyDecision.ASK:
            approved = await self._should_allow_ask(tool.spec, call, resolved_args)
            if approved:
                decision = PolicyDecision.ALLOW
        if decision != PolicyDecision.ALLOW:
            suffix = "requires explicit human approval" if decision == PolicyDecision.ASK else "denied by policy"
            return ToolResult(
                call_id=call.id,
                tool_name=call.name,
                output=None,
                success=False,
                error=f"Tool call {call.name} {suffix}.",
                approval=decision.value,
                skipped=True,
            )

        cache_key = self._cache_key(call.name, resolved_args)
        ttl = tool.spec.cache_ttl_seconds
        if ttl > 0:
            self._evict_expired_cache_entries()
            cached = self._cache.get(cache_key)
            if cached and cached.valid():
                return ToolResult(
                    call_id=call.id,
                    tool_name=call.name,
                    output=cached.value,
                    cached=True,
                    approval=decision.value,
                    expires_at=cached.expires_at,
                )

        try:
            output = await self._invoke(tool.handler, resolved_args)
            expires_at = None
            if ttl > 0:
                expires_at = datetime.now(UTC) + timedelta(seconds=ttl)
                self._cache[cache_key] = _CacheEntry(value=output, expires_at=expires_at)
                self._enforce_cache_limit()
            return ToolResult(
                call_id=call.id,
                tool_name=call.name,
                output=output,
                success=True,
                approval=decision.value,
                expires_at=expires_at,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                call_id=call.id,
                tool_name=call.name,
                output=None,
                success=False,
                error=str(exc),
                approval=decision.value,
            )

    async def execute_plan(self, plan: ExecutionPlan) -> list[ToolResult]:
        validate_execution_plan(plan)
        results: dict[str, ToolResult] = {}
        ordered: list[ToolResult] = []

        for batch in plan.batches:
            if batch.mode == "sequential":
                for call in batch.calls:
                    if call.depends_on:
                        unresolved = [dep for dep in call.depends_on if dep not in results]
                        if unresolved:
                            raise ValueError(
                                f"Call {call.id} depends on unresolved calls: {unresolved}"
                            )
                    result = await self.execute_call(call, results)
                    results[call.id] = result
                    ordered.append(result)
                continue

            for call in batch.calls:
                if call.depends_on:
                    unresolved = [dep for dep in call.depends_on if dep not in results]
                    if unresolved:
                        raise ValueError(
                            f"Call {call.id} depends on unresolved calls: {unresolved}"
                        )

            task_calls = [self.execute_call(call, results) for call in batch.calls]
            batch_results = await asyncio.gather(*task_calls)
            for call, result in zip(batch.calls, batch_results, strict=True):
                results[call.id] = result
                ordered.append(result)

        return ordered
