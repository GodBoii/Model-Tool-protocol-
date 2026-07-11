from __future__ import annotations

import asyncio
import copy
import inspect
import json
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable, Protocol

from .exceptions import RetryAgentRun, StopAgentRun
from .media import coerce_audios, coerce_files, coerce_images, coerce_videos
from .policy import PolicyDecision, RiskPolicy
from .protocol import ExecutionPlan, ToolCall, ToolOutput, ToolResult, ToolSpec
from .schema import (
    ToolArgumentsValidationError,
    coerce_tool_arguments,
    validate_execution_plan,
    validate_tool_arguments,
)

ToolHandler = Callable[..., Any] | Callable[..., Awaitable[Any]]
ApprovalHandler = Callable[[ToolSpec, ToolCall, dict[str, Any]], bool | Awaitable[bool]]
CancelChecker = Callable[[], bool]


class ExecutionCancelledError(RuntimeError):
    """Raised when an in-flight execution plan is cancelled."""


class ToolExecutionTimeoutError(TimeoutError):
    """Raised when a tool exceeds the registry execution timeout."""


class ToolRetryError(RuntimeError):
    """Raised when a tool requests retrying the run with feedback."""

    def __init__(self, *, call_id: str, tool_name: str, message: str) -> None:
        super().__init__(message)
        self.call_id = call_id
        self.tool_name = tool_name
        self.message = message


class ToolStopError(RuntimeError):
    """Raised when a tool requests stopping/pausing the run."""

    def __init__(self, *, call_id: str, tool_name: str, message: str) -> None:
        super().__init__(message)
        self.call_id = call_id
        self.tool_name = tool_name
        self.message = message


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
    expires_at_monotonic: float

    def valid(self) -> bool:
        # Wall clocks can jump forwards or backwards (NTP, sleep/resume, or a
        # manual clock change). TTLs describe elapsed time, so use a monotonic
        # clock for correctness while retaining ``expires_at`` for API display.
        return time.monotonic() < self.expires_at_monotonic


class ToolRegistry:
    def __init__(
        self,
        policy: RiskPolicy | None = None,
        *,
        max_cache_entries: int = 1024,
        max_concurrency: int = 16,
        tool_timeout_seconds: float | None = None,
        approval_handler: ApprovalHandler | None = None,
    ) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._toolkit_loaders: dict[str, ToolkitLoader] = {}
        self._loaded_toolkits: set[str] = set()
        self._cache: dict[tuple[str, str], _CacheEntry] = {}
        self._cache_hits = 0
        self._cache_misses = 0
        self._cache_evictions = 0
        self._tool_specs_cache: list[ToolSpec] | None = None
        self.policy = policy or RiskPolicy()
        self.max_cache_entries = max_cache_entries
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        self.max_concurrency = max_concurrency
        if tool_timeout_seconds is not None and tool_timeout_seconds <= 0:
            raise ValueError("tool_timeout_seconds must be greater than 0 or None")
        self.tool_timeout_seconds = tool_timeout_seconds
        self.approval_handler = approval_handler

    def register_tool(self, spec: ToolSpec, handler: ToolHandler) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = RegisteredTool(spec=spec, handler=handler)
        self._tool_specs_cache = None

    def add_tool(self, tool: RegisteredTool) -> None:
        self.register_tool(tool.spec, tool.handler)

    def unregister_tool(self, name: str) -> bool:
        removed = self._tools.pop(name, None)
        if removed is None:
            return False
        self._tool_specs_cache = None
        return True

    def set_tools(self, tools: list[RegisteredTool]) -> None:
        self._tools = {tool.spec.name: tool for tool in tools}
        # Replace semantics: clear any previously attached toolkit loaders/previews.
        self._toolkit_loaders = {}
        self._loaded_toolkits = set()
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
        self._cache_evictions += len(expired)

    def _enforce_cache_limit(self) -> None:
        if self.max_cache_entries <= 0:
            self._cache_evictions += len(self._cache)
            self._cache.clear()
            return
        if len(self._cache) <= self.max_cache_entries:
            return
        ordered = sorted(self._cache.items(), key=lambda item: item[1].expires_at_monotonic)
        overflow = len(self._cache) - self.max_cache_entries
        for key, _ in ordered[:overflow]:
            self._cache.pop(key, None)
        self._cache_evictions += overflow

    def clear_cache(self, tool_name: str | None = None) -> int:
        """Remove cached results and return the number of entries removed.

        Passing a tool name only clears entries belonging to that tool. Cache
        lifetime counters are intentionally retained; call :meth:`cache_stats`
        with ``reset_stats=True`` when a fresh measurement window is needed.
        """
        if tool_name is None:
            removed = len(self._cache)
            self._cache.clear()
            return removed
        keys = [key for key in self._cache if key[0] == tool_name]
        for key in keys:
            self._cache.pop(key, None)
        return len(keys)

    def cache_stats(self, *, reset_stats: bool = False) -> dict[str, int]:
        """Return a snapshot of cache occupancy and lifetime activity."""
        self._evict_expired_cache_entries()
        stats = {
            "entries": len(self._cache),
            "max_entries": self.max_cache_entries,
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "evictions": self._cache_evictions,
        }
        if reset_stats:
            self._cache_hits = 0
            self._cache_misses = 0
            self._cache_evictions = 0
        return stats

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

    def _inject_media_args(
        self,
        handler: ToolHandler,
        args: dict[str, Any],
        media_context: dict[str, Any] | None,
        *,
        cancel_checker: CancelChecker | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        call_args = dict(args)
        try:
            signature = inspect.signature(handler)
        except Exception:
            return call_args

        if media_context is not None:
            if "images" in signature.parameters and "images" not in call_args:
                call_args["images"] = media_context.get("images")
            if "videos" in signature.parameters and "videos" not in call_args:
                call_args["videos"] = media_context.get("videos")
            if "audios" in signature.parameters and "audios" not in call_args:
                call_args["audios"] = media_context.get("audios")
            if "files" in signature.parameters and "files" not in call_args:
                call_args["files"] = media_context.get("files")
        if cancel_checker is not None and "cancel_checker" in signature.parameters and "cancel_checker" not in call_args:
            call_args["cancel_checker"] = cancel_checker
        if cancel_event is not None and "cancel_event" in signature.parameters and "cancel_event" not in call_args:
            call_args["cancel_event"] = cancel_event
        return call_args

    async def _await_with_cancellation(
        self,
        task: asyncio.Task[Any],
        *,
        cancel_checker: CancelChecker | None = None,
        cancel_event: threading.Event | None = None,
        timeout_seconds: float | None = None,
    ) -> Any:
        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
        while True:
            wait_seconds = 0.05
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if cancel_event is not None:
                        cancel_event.set()
                    task.cancel()
                    raise ToolExecutionTimeoutError(
                        f"Tool execution exceeded {timeout_seconds:g} seconds."
                    )
                wait_seconds = min(wait_seconds, remaining)
            done, _pending = await asyncio.wait({task}, timeout=wait_seconds)
            if task in done:
                return await task
            if cancel_checker is not None and cancel_checker():
                if cancel_event is not None:
                    cancel_event.set()
                task.cancel()
                raise ExecutionCancelledError("Execution cancelled during in-flight tool execution.")

    async def _invoke(
        self,
        handler: ToolHandler,
        args: dict[str, Any],
        media_context: dict[str, Any] | None = None,
        *,
        cancel_checker: CancelChecker | None = None,
    ) -> Any:
        if cancel_checker is not None and cancel_checker():
            raise ExecutionCancelledError("Execution cancelled before tool invocation.")

        cancel_event = threading.Event()
        call_args = self._inject_media_args(
            handler,
            args,
            media_context,
            cancel_checker=cancel_checker,
            cancel_event=cancel_event,
        )

        # Async handlers can be cancelled directly via task cancellation.
        if inspect.iscoroutinefunction(handler):
            task = asyncio.create_task(handler(**call_args))
            return await self._await_with_cancellation(
                task,
                cancel_checker=cancel_checker,
                cancel_event=cancel_event,
                timeout_seconds=self.tool_timeout_seconds,
            )

        # Sync handlers run in a worker thread. In-flight cancellation is cooperative
        # via optional `cancel_event` / `cancel_checker` parameters.
        task = asyncio.create_task(asyncio.to_thread(handler, **call_args))
        return await self._await_with_cancellation(
            task,
            cancel_checker=cancel_checker,
                cancel_event=cancel_event,
                timeout_seconds=self.tool_timeout_seconds,
        )

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

    async def execute_call(
        self,
        call: ToolCall,
        prior_results: dict[str, ToolResult],
        *,
        media_context: dict[str, Any] | None = None,
        cancel_checker: CancelChecker | None = None,
    ) -> ToolResult:
        if cancel_checker is not None and cancel_checker():
            raise ExecutionCancelledError("Execution cancelled before tool call execution.")

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

        resolved_args = call.arguments
        if isinstance(resolved_args, dict):
            resolved_args = coerce_tool_arguments(resolved_args, tool.spec.input_schema)
        try:
            resolved_args = self._resolve_refs(resolved_args, prior_results)
        except KeyError as exc:
            return ToolResult(
                call_id=call.id,
                tool_name=call.name,
                output=None,
                success=False,
                error=str(exc),
            )
        if isinstance(resolved_args, dict):
            resolved_args = coerce_tool_arguments(resolved_args, tool.spec.input_schema)
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
                self._cache_hits += 1
                # A caller must not be able to mutate the value retained for
                # future callers. This also isolates duplicate calls in a plan.
                cached_output = copy.deepcopy(cached.value)
                cached_content = cached_output
                cached_images = None
                cached_videos = None
                cached_audios = None
                cached_files = None
                if isinstance(cached_output, dict) and cached_output.get("_mtp_tool_output") is True:
                    cached_content = cached_output.get("content")
                    cached_images = coerce_images(cached_output.get("images"))
                    cached_videos = coerce_videos(cached_output.get("videos"))
                    cached_audios = coerce_audios(cached_output.get("audios"))
                    cached_files = coerce_files(cached_output.get("files"))
                return ToolResult(
                    call_id=call.id,
                    tool_name=call.name,
                    output=cached_content,
                    cached=True,
                    approval=decision.value,
                    expires_at=cached.expires_at,
                    images=cached_images,
                    videos=cached_videos,
                    audios=cached_audios,
                    files=cached_files,
                )
            self._cache_misses += 1

        try:
            output = await self._invoke(
                tool.handler,
                resolved_args,
                media_context=media_context,
                cancel_checker=cancel_checker,
            )
            content = output
            images = None
            videos = None
            audios = None
            files = None
            if isinstance(output, ToolOutput):
                content = output.content
                images = output.images
                videos = output.videos
                audios = output.audios
                files = output.files
            elif isinstance(output, dict):
                has_media_keys = any(k in output for k in ("images", "videos", "audios", "files"))
                if has_media_keys:
                    content = output.get("content", output)
                    images = coerce_images(output.get("images"))
                    videos = coerce_videos(output.get("videos"))
                    audios = coerce_audios(output.get("audios"))
                    files = coerce_files(output.get("files"))
            expires_at = None
            if ttl > 0:
                expires_at = datetime.now(UTC) + timedelta(seconds=ttl)
                cache_value: Any = content
                if images or videos or audios or files:
                    cache_value = {
                        "_mtp_tool_output": True,
                        "content": content,
                        "images": [img.to_dict() for img in images or []],
                        "videos": [vid.to_dict() for vid in videos or []],
                        "audios": [aud.to_dict() for aud in audios or []],
                        "files": [file.to_dict() for file in files or []],
                    }
                try:
                    isolated_cache_value = copy.deepcopy(cache_value)
                except Exception:  # noqa: BLE001 - arbitrary tool return types
                    # Some third-party return objects cannot be copied safely.
                    # Returning the result is still useful; silently avoid an
                    # unsafe cache entry rather than sharing mutable state.
                    expires_at = None
                else:
                    self._cache[cache_key] = _CacheEntry(
                        value=isolated_cache_value,
                        expires_at=expires_at,
                        expires_at_monotonic=time.monotonic() + ttl,
                    )
                    self._enforce_cache_limit()
            return ToolResult(
                call_id=call.id,
                tool_name=call.name,
                output=content,
                success=True,
                approval=decision.value,
                expires_at=expires_at,
                images=images,
                videos=videos,
                audios=audios,
                files=files,
            )
        except asyncio.CancelledError:
            raise
        except ExecutionCancelledError:
            raise
        except ToolExecutionTimeoutError as exc:
            return ToolResult(
                call_id=call.id,
                tool_name=call.name,
                output=None,
                success=False,
                error=str(exc),
                approval=decision.value,
            )
        except RetryAgentRun as exc:
            message = str(exc).strip() or "Tool requested a retry."
            raise ToolRetryError(call_id=call.id, tool_name=call.name, message=message) from exc
        except StopAgentRun as exc:
            message = str(exc).strip() or "Tool requested the run to stop."
            raise ToolStopError(call_id=call.id, tool_name=call.name, message=message) from exc
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                call_id=call.id,
                tool_name=call.name,
                output=None,
                success=False,
                error=str(exc),
                approval=decision.value,
            )

    async def execute_plan(
        self,
        plan: ExecutionPlan,
        *,
        cancel_checker: CancelChecker | None = None,
        media_context: dict[str, Any] | None = None,
    ) -> list[ToolResult]:
        validate_execution_plan(plan)
        results: dict[str, ToolResult] = {}
        ordered: list[ToolResult] = []

        def failed_dependencies(call: ToolCall) -> list[str]:
            return [
                dependency
                for dependency in call.depends_on
                if dependency in results and not results[dependency].success
            ]

        def dependency_failure(call: ToolCall, dependencies: list[str]) -> ToolResult:
            return ToolResult(
                call_id=call.id,
                tool_name=call.name,
                output=None,
                success=False,
                error=f"Skipped because dependencies failed: {dependencies}",
                skipped=True,
            )

        for batch in plan.batches:
            if cancel_checker is not None and cancel_checker():
                raise ExecutionCancelledError("Execution plan cancelled before batch execution.")
            if batch.mode == "sequential":
                for call in batch.calls:
                    if cancel_checker is not None and cancel_checker():
                        raise ExecutionCancelledError("Execution plan cancelled before tool call execution.")
                    if call.depends_on:
                        unresolved = [dep for dep in call.depends_on if dep not in results]
                        if unresolved:
                            raise ValueError(
                                f"Call {call.id} depends on unresolved calls: {unresolved}"
                            )
                        failed = failed_dependencies(call)
                        if failed:
                            result = dependency_failure(call, failed)
                            results[call.id] = result
                            ordered.append(result)
                            continue
                    result = await self.execute_call(
                        call,
                        results,
                        media_context=media_context,
                        cancel_checker=cancel_checker,
                    )
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

            if cancel_checker is not None and cancel_checker():
                raise ExecutionCancelledError("Execution plan cancelled before parallel tool execution.")
            runnable_calls: list[ToolCall] = []
            for call in batch.calls:
                failed = failed_dependencies(call)
                if failed:
                    result = dependency_failure(call, failed)
                    results[call.id] = result
                else:
                    runnable_calls.append(call)

            semaphore = asyncio.Semaphore(self.max_concurrency)

            async def execute_limited(call: ToolCall) -> ToolResult:
                async with semaphore:
                    return await self.execute_call(
                        call,
                        results,
                        media_context=media_context,
                        cancel_checker=cancel_checker,
                    )

            unique_calls: dict[tuple[str, str], ToolCall] = {}
            call_keys: dict[str, tuple[str, str]] = {}
            for call in runnable_calls:
                key = self._cache_key(call.name, self._resolve_refs(call.arguments, results))
                call_keys[call.id] = key
                unique_calls.setdefault(key, call)

            unique_results = await asyncio.gather(
                *[execute_limited(call) for call in unique_calls.values()]
            )
            result_by_key = dict(zip(unique_calls.keys(), unique_results, strict=True))
            for call in batch.calls:
                if call.id not in results:
                    result = result_by_key[call_keys[call.id]]
                    if result.call_id != call.id:
                        result = ToolResult(
                            call_id=call.id,
                            tool_name=result.tool_name,
                            output=result.output,
                            success=result.success,
                            error=result.error,
                            cached=True,
                            approval=result.approval,
                            skipped=result.skipped,
                            expires_at=result.expires_at,
                            images=result.images,
                            videos=result.videos,
                            audios=result.audios,
                            files=result.files,
                        )
                    results[call.id] = result
                ordered.append(results[call.id])

        return ordered
