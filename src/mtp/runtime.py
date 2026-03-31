from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable, Protocol

from .policy import PolicyDecision, RiskPolicy
from .protocol import ExecutionPlan, ToolCall, ToolResult, ToolSpec
from .schema import validate_execution_plan

ToolHandler = Callable[..., Any] | Callable[..., Awaitable[Any]]


class ToolkitLoader(Protocol):
    def load_tools(self) -> list["RegisteredTool"]:
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
    def __init__(self, policy: RiskPolicy | None = None) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._toolkit_loaders: dict[str, ToolkitLoader] = {}
        self._loaded_toolkits: set[str] = set()
        self._cache: dict[tuple[str, str], _CacheEntry] = {}
        self.policy = policy or RiskPolicy()

    def register_tool(self, spec: ToolSpec, handler: ToolHandler) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = RegisteredTool(spec=spec, handler=handler)

    def register_toolkit_loader(self, toolkit_name: str, loader: ToolkitLoader) -> None:
        self._toolkit_loaders[toolkit_name] = loader

    def list_tools(self) -> list[ToolSpec]:
        return [entry.spec for entry in self._tools.values()]

    def _cache_key(self, tool_name: str, arguments: dict[str, Any]) -> tuple[str, str]:
        canonical = repr(sorted(arguments.items()))
        return tool_name, canonical

    def _load_toolkit(self, toolkit_name: str) -> None:
        if toolkit_name in self._loaded_toolkits:
            return
        loader = self._toolkit_loaders.get(toolkit_name)
        if loader is None:
            return
        for tool in loader.load_tools():
            if tool.spec.name not in self._tools:
                self._tools[tool.spec.name] = tool
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
        decision = self.policy.decide(tool.spec, call, resolved_args)
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
            return ToolResult(
                call_id=call.id,
                tool_name=call.name,
                output=output,
                success=True,
                approval=decision.value,
                expires_at=expires_at,
            )
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
