from __future__ import annotations

import asyncio
import inspect
import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable, Protocol

from .exceptions import RetryAgentRun, StopAgentRun
from .media import coerce_audios, coerce_files, coerce_images, coerce_videos
from .policy import PolicyDecision, RiskPolicy
from .protocol import ExecutionPlan, ToolCall, ToolOutput, ToolResult, ToolSpec
from .schema import ToolArgumentsValidationError, validate_execution_plan, validate_tool_arguments

ToolHandler = Callable[..., Any] | Callable[..., Awaitable[Any]]
ApprovalHandler = Callable[[ToolSpec, ToolCall, dict[str, Any]], bool | Awaitable[bool]]
CancelChecker = Callable[[], bool]


class ExecutionCancelledError(RuntimeError):
    """Raised when an in-flight execution plan is cancelled."""


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

    def add_tool(self, tool: RegisteredTool) -> None:
        self.register_tool(tool.spec, tool.handler)

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
    ) -> Any:
        while True:
            done, _pending = await asyncio.wait({task}, timeout=0.05)
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
            )

        # Sync handlers run in a worker thread. In-flight cancellation is cooperative
        # via optional `cancel_event` / `cancel_checker` parameters.
        task = asyncio.create_task(asyncio.to_thread(handler, **call_args))
        return await self._await_with_cancellation(
            task,
            cancel_checker=cancel_checker,
            cancel_event=cancel_event,
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
                cached_output = cached.value
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
                self._cache[cache_key] = _CacheEntry(value=cache_value, expires_at=expires_at)
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
            task_calls = [
                self.execute_call(
                    call,
                    results,
                    media_context=media_context,
                    cancel_checker=cancel_checker,
                )
                for call in batch.calls
            ]
            batch_results = await asyncio.gather(*task_calls)
            for call, result in zip(batch.calls, batch_results, strict=True):
                results[call.id] = result
                ordered.append(result)

        return ordered
