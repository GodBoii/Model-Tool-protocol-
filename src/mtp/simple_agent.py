from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import datetime
import json
import textwrap
from typing import Any

from .agent import Agent, RunOutput
from .media import Audio, File, Image, Video
from .protocol import ToolResult
from .runtime import ToolRegistry
from .agent import ProviderAdapter
from .session_store import SessionStore


class MTPAgent:
    """
    Provider-agnostic convenience wrapper around Agent.
    Requires explicit provider + tool registry from the user.
    """

    def __init__(
        self,
        *,
        provider: ProviderAdapter,
        tools: ToolRegistry | None = None,
        registry: ToolRegistry | None = None,
        debug_mode: bool = False,
        strict_dependency_mode: bool = False,
        instructions: str | None = None,
        system_instructions: str | None = None,
        stream_chunk_size: int = 40,
        max_history_messages: int = 200,
        enforce_provider_capabilities: bool = True,
        allow_stream_fallback: bool = True,
        autoresearch: bool = False,
        research_instructions: str | None = None,
        stream_tool_events: bool = True,
        stream_tool_results: bool = True,
        mode: str = "standalone",
        members: dict[str, Agent] | None = None,
        session_store: SessionStore | None = None,
    ) -> None:
        if registry is not None and tools is not None and registry is not tools:
            raise ValueError("Pass only one of `tools` or `registry`.")
        resolved_tools = tools or registry
        if resolved_tools is None:
            raise ValueError("Missing tools registry. Pass `tools=` (or legacy `registry=`).")

        self._agent = Agent(
            provider=provider,
            tools=resolved_tools,
            debug_mode=debug_mode,
            strict_dependency_mode=strict_dependency_mode,
            instructions=instructions,
            system_instructions=system_instructions,
            stream_chunk_size=stream_chunk_size,
            max_history_messages=max_history_messages,
            enforce_provider_capabilities=enforce_provider_capabilities,
            allow_stream_fallback=allow_stream_fallback,
            autoresearch=autoresearch,
            research_instructions=research_instructions,
            stream_tool_events=stream_tool_events,
            stream_tool_results=stream_tool_results,
            mode=mode,
            members=members,
            session_store=session_store,
        )

    def run(
        self,
        prompt: str,
        *,
        max_rounds: int = 5,
        images: list[Image] | None = None,
        audios: list[Audio] | None = None,
        videos: list[Video] | None = None,
        files: list[File] | None = None,
        tool_call_limit: int | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return self._agent.run_loop(
            user_input=prompt,
            max_rounds=max_rounds,
            images=images,
            audios=audios,
            videos=videos,
            files=files,
            user_id=user_id,
            session_id=session_id,
            metadata=metadata,
            tool_call_limit=tool_call_limit,
        )

    def run_output(
        self,
        prompt: str,
        *,
        max_rounds: int = 5,
        images: list[Image] | None = None,
        audios: list[Audio] | None = None,
        videos: list[Video] | None = None,
        files: list[File] | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tool_call_limit: int | None = None,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        output_model: ProviderAdapter | None = None,
        output_model_prompt: str | None = None,
        parser_model: ProviderAdapter | None = None,
        parser_model_prompt: str | None = None,
    ) -> RunOutput:
        return self._agent.run_output(
            user_input=prompt,
            max_rounds=max_rounds,
            images=images,
            audios=audios,
            videos=videos,
            files=files,
            run_id=run_id,
            user_id=user_id,
            session_id=session_id,
            metadata=metadata,
            tool_call_limit=tool_call_limit,
            input_schema=input_schema,
            output_schema=output_schema,
            output_model=output_model,
            output_model_prompt=output_model_prompt,
            parser_model=parser_model,
            parser_model_prompt=parser_model_prompt,
        )

    def run_stream(
        self,
        prompt: str,
        *,
        max_rounds: int = 5,
        images: list[Image] | None = None,
        audios: list[Audio] | None = None,
        videos: list[Video] | None = None,
        files: list[File] | None = None,
        tool_call_limit: int | None = None,
        run_id: str | None = None,
        input_schema: dict[str, Any] | None = None,
    ) -> Iterator[str]:
        return self._agent.run_loop_stream(
            user_input=prompt,
            max_rounds=max_rounds,
            images=images,
            audios=audios,
            videos=videos,
            files=files,
            tool_call_limit=tool_call_limit,
            run_id=run_id,
            input_schema=input_schema,
        )

    async def arun(
        self,
        prompt: str,
        *,
        max_rounds: int = 5,
        images: list[Image] | None = None,
        audios: list[Audio] | None = None,
        videos: list[Video] | None = None,
        files: list[File] | None = None,
        tool_call_limit: int | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return await self._agent.arun_loop(
            user_input=prompt,
            max_rounds=max_rounds,
            images=images,
            audios=audios,
            videos=videos,
            files=files,
            user_id=user_id,
            session_id=session_id,
            metadata=metadata,
            tool_call_limit=tool_call_limit,
        )

    async def arun_output(
        self,
        prompt: str,
        *,
        max_rounds: int = 5,
        images: list[Image] | None = None,
        audios: list[Audio] | None = None,
        videos: list[Video] | None = None,
        files: list[File] | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tool_call_limit: int | None = None,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        output_model: ProviderAdapter | None = None,
        output_model_prompt: str | None = None,
        parser_model: ProviderAdapter | None = None,
        parser_model_prompt: str | None = None,
    ) -> RunOutput:
        return await self._agent.arun_output(
            user_input=prompt,
            max_rounds=max_rounds,
            images=images,
            audios=audios,
            videos=videos,
            files=files,
            run_id=run_id,
            user_id=user_id,
            session_id=session_id,
            metadata=metadata,
            tool_call_limit=tool_call_limit,
            input_schema=input_schema,
            output_schema=output_schema,
            output_model=output_model,
            output_model_prompt=output_model_prompt,
            parser_model=parser_model,
            parser_model_prompt=parser_model_prompt,
        )

    def run_events(
        self,
        prompt: str,
        *,
        max_rounds: int = 5,
        images: list[Image] | None = None,
        audios: list[Audio] | None = None,
        videos: list[Video] | None = None,
        files: list[File] | None = None,
        stream_final: bool = True,
        run_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tool_call_limit: int | None = None,
        input_schema: dict[str, Any] | None = None,
        stream_tool_events: bool | None = None,
        stream_tool_results: bool | None = None,
    ) -> Iterator[dict[str, Any]]:
        return self._agent.run_loop_events(
            user_input=prompt,
            max_rounds=max_rounds,
            images=images,
            audios=audios,
            videos=videos,
            files=files,
            stream_final=stream_final,
            run_id=run_id,
            user_id=user_id,
            session_id=session_id,
            metadata=metadata,
            tool_call_limit=tool_call_limit,
            input_schema=input_schema,
            stream_tool_events=stream_tool_events,
            stream_tool_results=stream_tool_results,
        )

    def arun_events(
        self,
        prompt: str,
        *,
        max_rounds: int = 5,
        images: list[Image] | None = None,
        audios: list[Audio] | None = None,
        videos: list[Video] | None = None,
        files: list[File] | None = None,
        stream_final: bool = True,
        run_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tool_call_limit: int | None = None,
        input_schema: dict[str, Any] | None = None,
        stream_tool_events: bool | None = None,
        stream_tool_results: bool | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        return self._agent.arun_loop_events(
            user_input=prompt,
            max_rounds=max_rounds,
            images=images,
            audios=audios,
            videos=videos,
            files=files,
            stream_final=stream_final,
            run_id=run_id,
            user_id=user_id,
            session_id=session_id,
            metadata=metadata,
            tool_call_limit=tool_call_limit,
            input_schema=input_schema,
            stream_tool_events=stream_tool_events,
            stream_tool_results=stream_tool_results,
        )

    def cancel_run(self, run_id: str) -> bool:
        return self._agent.cancel_run(run_id)

    def continue_run(
        self,
        *,
        run_output: RunOutput | None = None,
        run_id: str | None = None,
        max_rounds: int = 5,
        updated_tools: list[ToolResult] | None = None,
        tool_call_limit: int | None = None,
    ) -> RunOutput:
        return self._agent.continue_run(
            run_output=run_output,
            run_id=run_id,
            max_rounds=max_rounds,
            updated_tools=updated_tools,
            tool_call_limit=tool_call_limit,
        )

    async def acontinue_run(
        self,
        *,
        run_output: RunOutput | None = None,
        run_id: str | None = None,
        max_rounds: int = 5,
        updated_tools: list[ToolResult] | None = None,
        tool_call_limit: int | None = None,
    ) -> RunOutput:
        return await self._agent.acontinue_run(
            run_output=run_output,
            run_id=run_id,
            max_rounds=max_rounds,
            updated_tools=updated_tools,
            tool_call_limit=tool_call_limit,
        )

    def print_response(
        self,
        prompt: str,
        *,
        max_rounds: int = 5,
        images: list[Image] | None = None,
        audios: list[Audio] | None = None,
        videos: list[Video] | None = None,
        files: list[File] | None = None,
        stream: bool = False,
        stream_events: bool = False,
        run_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tool_call_limit: int | None = None,
        event_format: str = "pretty",
        stream_tool_events: bool | None = None,
        stream_tool_results: bool | None = None,
    ) -> None:
        if stream_events:
            if event_format not in {"pretty", "json"}:
                raise ValueError("event_format must be 'pretty' or 'json'")
            printed_chunk = False
            pretty_context: dict[str, Any] = {
                "member_map": {},
                "delegated_calls": {},
                "tool_starts": {},
                "run_started_at": None,
                "tools_ok": 0,
                "tools_failed": 0,
                "tools_cached": 0,
                "debug_enabled": bool(self._agent.debug_mode),
                "metrics": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "reasoning_tokens": 0,
                    "cached_input_tokens": 0,
                    "cache_write_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "tool_use_prompt_tokens": 0,
                    "llm_duration_seconds": 0.0,
                    "llm_calls": 0,
                },
            }
            debug_enabled = bool(self._agent.debug_mode)
            for event in self.run_events(
                prompt,
                max_rounds=max_rounds,
                images=images,
                audios=audios,
                videos=videos,
                files=files,
                stream_final=stream,
                run_id=run_id,
                user_id=user_id,
                session_id=session_id,
                metadata=metadata,
                tool_call_limit=tool_call_limit,
                stream_tool_events=stream_tool_events,
                stream_tool_results=stream_tool_results,
            ):
                if event_format == "json":
                    if not self._should_print_event(event.get("type"), debug_enabled=debug_enabled):
                        continue
                    print(json.dumps(event, default=str))
                    continue
                printed_chunk = self._print_pretty_event(event, printed_chunk=printed_chunk, context=pretty_context)
            return
        if not stream:
            print(
                self.run(
                    prompt,
                    max_rounds=max_rounds,
                    images=images,
                    audios=audios,
                    videos=videos,
                    files=files,
                    user_id=user_id,
                    session_id=session_id,
                    metadata=metadata,
                    tool_call_limit=tool_call_limit,
                )
            )
            return
        for chunk in self.run_stream(
            prompt,
            max_rounds=max_rounds,
            images=images,
            audios=audios,
            videos=videos,
            files=files,
            tool_call_limit=tool_call_limit,
            run_id=run_id,
        ):
            print(chunk, end="", flush=True)
        print()

    def _parse_iso_datetime(self, value: Any) -> datetime | None:
        if not isinstance(value, str) or "T" not in value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _format_timestamp(self, stamp: Any) -> str:
        parsed = self._parse_iso_datetime(stamp)
        if parsed is None:
            return str(stamp) if stamp is not None else "-"
        return parsed.strftime("%H:%M:%S.%f")[:-3]

    def _format_meta(self, *, event_type: str, sequence: Any, stamp: Any) -> str:
        return f"[seq={sequence} t={self._format_timestamp(stamp)}] {event_type}"

    def _duration_seconds(self, start: Any, end: Any) -> str | None:
        start_dt = self._parse_iso_datetime(start)
        end_dt = self._parse_iso_datetime(end)
        if start_dt is None or end_dt is None:
            return None
        return f"{(end_dt - start_dt).total_seconds():.3f}s"

    def _short(self, value: Any, *, width: int = 180) -> str:
        if isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
            except Exception:
                text = repr(value)
        if len(text) <= width:
            return text
        return text[:width] + "...<truncated>"

    def _hr(self, char: str = "-", width: int = 88) -> str:
        return char * width

    def _log_line(self, level: str, text: str) -> None:
        print(f"{level} | {text}")

    def _print_wrapped_block(self, title: str, text: Any, *, indent: str = "  ", width: int = 100) -> None:
        print(f"{title}:")
        raw = str(text or "")
        if not raw.strip():
            print(f"{indent}(none)")
            return
        for paragraph in raw.splitlines() or [""]:
            wrapped = textwrap.wrap(paragraph, width=width - len(indent)) or [""]
            for line in wrapped:
                print(f"{indent}{line}")

    def _print_json_block(self, title: str, value: Any, *, indent: str = "  ", max_chars: int = 1200) -> None:
        try:
            rendered = json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, default=str)
        except Exception:
            rendered = str(value)
        if len(rendered) > max_chars:
            rendered = rendered[:max_chars] + "...<truncated>"
        self._print_wrapped_block(title, rendered, indent=indent)

    def _print_bullet(self, text: Any, *, indent: str = "  ", width: int = 100) -> None:
        raw = str(text or "")
        lines = raw.splitlines() or [""]
        first = True
        for line in lines:
            wrapped = textwrap.wrap(line, width=width - len(indent) - 2) or [""]
            for chunk in wrapped:
                prefix = "- " if first else "  "
                print(f"{indent}{prefix}{chunk}")
                first = False

    def _as_int(self, value: Any) -> int:
        try:
            return int(value)
        except Exception:
            return 0

    def _as_float(self, value: Any) -> float:
        try:
            return float(value)
        except Exception:
            return 0.0

    def _merge_usage_metrics(self, context: dict[str, Any], usage: Any, duration_seconds: Any) -> None:
        metrics = context.setdefault(
            "metrics",
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "reasoning_tokens": 0,
                "cached_input_tokens": 0,
                "cache_write_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "tool_use_prompt_tokens": 0,
                "llm_duration_seconds": 0.0,
                "llm_calls": 0,
            },
        )
        usage_dict = usage if isinstance(usage, dict) else {}
        metrics["input_tokens"] = self._as_int(metrics.get("input_tokens")) + self._as_int(usage_dict.get("input_tokens"))
        metrics["output_tokens"] = self._as_int(metrics.get("output_tokens")) + self._as_int(usage_dict.get("output_tokens"))
        metrics["total_tokens"] = self._as_int(metrics.get("total_tokens")) + self._as_int(usage_dict.get("total_tokens"))
        metrics["reasoning_tokens"] = self._as_int(metrics.get("reasoning_tokens")) + self._as_int(
            usage_dict.get("reasoning_tokens")
        )
        metrics["cached_input_tokens"] = self._as_int(metrics.get("cached_input_tokens")) + self._as_int(
            usage_dict.get("cached_input_tokens")
        )
        metrics["cache_write_tokens"] = self._as_int(metrics.get("cache_write_tokens")) + self._as_int(
            usage_dict.get("cache_write_tokens")
        )
        metrics["cache_creation_input_tokens"] = self._as_int(
            metrics.get("cache_creation_input_tokens")
        ) + self._as_int(usage_dict.get("cache_creation_input_tokens"))
        metrics["cache_read_input_tokens"] = self._as_int(metrics.get("cache_read_input_tokens")) + self._as_int(
            usage_dict.get("cache_read_input_tokens")
        )
        metrics["tool_use_prompt_tokens"] = self._as_int(metrics.get("tool_use_prompt_tokens")) + self._as_int(
            usage_dict.get("tool_use_prompt_tokens")
        )
        metrics["llm_duration_seconds"] = self._as_float(metrics.get("llm_duration_seconds")) + self._as_float(duration_seconds)
        metrics["llm_calls"] = self._as_int(metrics.get("llm_calls")) + 1

    def _print_metrics_block(
        self,
        context: dict[str, Any],
        *,
        duration_seconds: float | None = None,
        title: str = "METRICS",
        level: str = "DEBUG",
    ) -> None:
        metrics = context.get("metrics", {})
        input_tokens = self._as_int(metrics.get("input_tokens"))
        output_tokens = self._as_int(metrics.get("output_tokens"))
        total_tokens = self._as_int(metrics.get("total_tokens"))
        reasoning_tokens = self._as_int(metrics.get("reasoning_tokens"))
        cached_input_tokens = self._as_int(metrics.get("cached_input_tokens"))
        cache_write_tokens = self._as_int(metrics.get("cache_write_tokens"))
        cache_creation_input_tokens = self._as_int(metrics.get("cache_creation_input_tokens"))
        cache_read_input_tokens = self._as_int(metrics.get("cache_read_input_tokens"))
        tool_use_prompt_tokens = self._as_int(metrics.get("tool_use_prompt_tokens"))
        llm_calls = self._as_int(metrics.get("llm_calls"))
        measured_duration = self._as_float(metrics.get("llm_duration_seconds"))
        effective_duration = duration_seconds if duration_seconds is not None and duration_seconds > 0 else measured_duration
        tokens_per_second = (total_tokens / effective_duration) if effective_duration > 1e-6 else None

        self._log_line(level, f"{'*' * 24}  {title}  {'*' * 25}")
        self._log_line(
            level,
            f"* Tokens: input={input_tokens}, output={output_tokens}, "
            f"total={total_tokens}, reasoning={reasoning_tokens}"
        )
        if any(
            [
                cached_input_tokens,
                cache_write_tokens,
                cache_creation_input_tokens,
                cache_read_input_tokens,
                tool_use_prompt_tokens,
            ]
        ):
            self._log_line(
                level,
                "* Cache/Tool Tokens: "
                f"cached_input={cached_input_tokens}, cache_write={cache_write_tokens}, "
                f"cache_create_input={cache_creation_input_tokens}, "
                f"cache_read_input={cache_read_input_tokens}, tool_use_prompt={tool_use_prompt_tokens}"
            )
        self._log_line(level, f"* LLM Calls: {llm_calls}")
        self._log_line(level, f"* Duration: {effective_duration:.4f}s")
        if tokens_per_second is None:
            self._log_line(level, "* Tokens per second: n/a")
        else:
            self._log_line(level, f"* Tokens per second: {tokens_per_second:.4f} tokens/s")
        self._log_line(level, f"{'*' * 24}  {title}  {'*' * 25}")

    def _print_xml_section(self, tag: str, items: list[Any], *, width: int = 100) -> None:
        print(f"<{tag}>")
        if not items:
            print("  (none)")
        else:
            for item in items:
                self._print_bullet(item, indent="  ", width=width)
        print(f"</{tag}>")

    def _should_print_event(self, event_type: Any, *, debug_enabled: bool) -> bool:
        event_name = str(event_type or "")
        if debug_enabled:
            return True
        normal_events = {
            "run_started",
            "round_started",
            "text_chunk",
            "run_completed",
            "run_cancelled",
            "run_terminated",
            "run_paused",
            "run_failed",
            "tool_retry_requested",
            "strict_violations",
        }
        return event_name in normal_events

    def _print_pretty_event(
        self,
        event: dict[str, Any],
        *,
        printed_chunk: bool,
        context: dict[str, Any],
    ) -> bool:
        event_type = str(event.get("type", ""))
        stamp = str(event.get("timestamp", ""))
        sequence = event.get("sequence")
        meta = self._format_meta(event_type=event_type, sequence=sequence, stamp=stamp)
        debug_enabled = bool(context.get("debug_enabled", False))

        if event_type == "run_started":
            context["run_started_at"] = stamp
            print(f"\n{self._hr('=')}")
            self._log_line("INFO", f"[MTP RUN START] {meta}")
            print(self._hr("="))
            print(f"Run ID          : {event.get('run_id')}")
            print(f"Max Rounds      : {event.get('max_rounds')}")
            print(f"Tools Available : {event.get('tools_available')}")
            print(f"User ID         : {event.get('user_id') or '-'}")
            print(f"Session ID      : {event.get('session_id') or '-'}")
            metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
            self._print_json_block("Metadata", metadata, indent="  ", max_chars=600)
            if not debug_enabled:
                self._print_wrapped_block("User Message", event.get("user_message"), indent="  ", width=100)
                print("Mode            : normal (set debug_mode=True for detailed tool logs)")
                return False
            direct_tools = [str(tool) for tool in list(event.get("direct_tool_names", []))]
            if not direct_tools:
                all_tools = [str(tool) for tool in list(event.get("tool_names", []))]
                member_delegation_tools = {str(member.get("delegation_tool")) for member in list(event.get("member_agents", []))}
                direct_tools = [tool for tool in all_tools if tool not in member_delegation_tools and not tool.startswith("agent.member.")]
            print("Tools:")
            if direct_tools:
                for tool in direct_tools:
                    print(f"  - {tool}")
            else:
                print("  - (none)")
            member_agents = list(event.get("member_agents", []))
            context["member_map"] = {str(member.get("id")): member for member in member_agents}
            if member_agents:
                print("Sub Agents:")
                for member in member_agents:
                    member_id = member.get("id")
                    role = member.get("role")
                    member_tools = [str(tool) for tool in list(member.get("tools", []))]
                    print(f"  - id={member_id}")
                    print(f"    mode={member.get('mode')}")
                    print(f"    delegation_tool={member.get('delegation_tool')}")
                    print("    tools:")
                    if member_tools:
                        for tool in member_tools:
                            print(f"      - {tool}")
                    else:
                        print("      - (none)")
                    if role:
                        self._print_wrapped_block("    role", role, indent="      ", width=100)
            else:
                print("Sub Agents: (none)")
            system_instructions = list(event.get("system_instructions", []))
            user_instructions = list(event.get("user_instructions", []))
            orchestration_instructions = list(event.get("orchestration_instructions", []))
            self._print_xml_section("tools", direct_tools, width=100)
            member_blocks: list[str] = []
            for member in member_agents:
                member_blocks.append(
                    self._short(
                        {
                            "id": member.get("id"),
                            "mode": member.get("mode"),
                            "delegation_tool": member.get("delegation_tool"),
                            "tools": member.get("tools", []),
                        },
                        width=1000,
                    )
                )
            self._print_xml_section("team_members", member_blocks, width=100)
            print("System Instructions:")
            if system_instructions:
                for item in system_instructions:
                    self._print_bullet(item, indent="  ", width=100)
            else:
                print("  - (none)")
            print("User Instructions:")
            if user_instructions:
                for item in user_instructions:
                    self._print_bullet(item, indent="  ", width=100)
            else:
                print("  - (none)")
            print("Orchestration Instructions:")
            if orchestration_instructions:
                for item in orchestration_instructions:
                    self._print_bullet(item, indent="  ", width=100)
            else:
                print("  - (none)")
            input_validation_error = event.get("input_validation_error")
            if input_validation_error:
                self._print_wrapped_block("Input Validation Error", input_validation_error, indent="  ", width=100)
            self._print_wrapped_block("User Message", event.get("user_message"), indent="  ", width=100)
            self._print_xml_section("system_instructions", system_instructions, width=100)
            self._print_xml_section("user_instructions", user_instructions, width=100)
            self._print_xml_section("orchestration_instructions", orchestration_instructions, width=100)
            return False

        if event_type == "round_started":
            round_idx = event.get("round")
            print(f"\n{self._hr('-')}")
            self._log_line("INFO", f"[MTP ROUND START] round={round_idx}  {meta}")
            print(self._hr("-"))
            return False

        if event_type == "llm_response":
            self._merge_usage_metrics(context, event.get("usage"), event.get("duration_seconds"))
            if not debug_enabled:
                return False
            self._log_line(
                "DEBUG",
                f"*** LLM RESPONSE round={event.get('round')} provider={event.get('provider')} "
                f"model={event.get('model')} stage={event.get('stage') or 'next_action'} "
                f"duration={self._as_float(event.get('duration_seconds')):.4f}s ***",
            )
            usage = event.get("usage")
            print(f"  Provider       : {event.get('provider')}")
            print(f"  Model          : {event.get('model')}")
            print(f"  Stage          : {event.get('stage') or 'next_action'}")
            print(f"  Duration       : {self._as_float(event.get('duration_seconds')):.4f}s")
            print(f"  Has Plan       : {bool(event.get('has_plan'))}")
            print(f"  Has Response   : {bool(event.get('has_response'))}")
            self._print_json_block("  Usage", usage if usage is not None else {}, indent="    ", max_chars=500)
            self._print_metrics_block(context, title="METRICS")
            return False

        if event_type == "plan_received":
            if not debug_enabled:
                return False
            round_idx = event.get("round")
            batches = event.get("batches", [])
            total_calls = sum(len(list(batch.get("calls", []))) for batch in batches)
            print(f"[MTP PLAN] round={round_idx} batches={len(batches)} calls={total_calls}  {meta}")
            for idx, batch in enumerate(batches, start=1):
                print(f"  Batch #{idx} ({batch.get('mode')})")
                calls = list(batch.get("calls", []))
                call_ids = list(batch.get("call_ids", []))
                for call_name, call_id in zip(calls, call_ids, strict=False):
                    print(f"    - tool={call_name}")
                    print(f"      id={call_id}")
            return False

        if event_type == "tool_started":
            call_id = str(event.get("call_id"))
            context.setdefault("tool_starts", {})[call_id] = stamp
            if not debug_enabled:
                return False
            self._log_line(
                "DEBUG",
                f"[MTP TOOL START] round={event.get('round')} tool={event.get('tool_name')} id={event.get('call_id')}  {meta}",
            )
            depends_on = list(event.get("depends_on", []))
            if depends_on:
                print(f"  Depends On     : {depends_on}")
            reasoning = event.get("reasoning")
            if isinstance(reasoning, str) and reasoning.strip():
                self._print_wrapped_block("  Reasoning", reasoning.strip(), indent="    ", width=100)
            self._print_json_block("  Arguments", event.get("arguments"), indent="    ", max_chars=600)
            tool_name = str(event.get("tool_name", ""))
            if tool_name.startswith("agent.member."):
                member_id = tool_name.removeprefix("agent.member.")
                context.setdefault("delegated_calls", {})[call_id] = member_id
                print(f"  Delegation: started member={member_id} parent_call_id={call_id}")
                member = context.get("member_map", {}).get(member_id, {})
                role = member.get("role")
                if role:
                    self._print_wrapped_block("  Member Role", role, indent="    ", width=100)
                member_tools = list(member.get("tools", []))
                print("  Member Tools:")
                if member_tools:
                    for tool in member_tools:
                        print(f"    - {tool}")
                else:
                    print("    - (none)")
                print("  <member_context>")
                self._print_json_block(
                    "    Summary",
                    {
                        "member_id": member_id,
                        "member_tools": member.get("tools"),
                        "task_input": event.get("arguments"),
                    },
                    indent="      ",
                    max_chars=900,
                )
                print("  </member_context>")
            return False

        if event_type == "tool_finished":
            success = bool(event.get("success"))
            success_text = "SUCCESS" if success else "FAILED"
            call_id = str(event.get("call_id"))
            if success:
                context["tools_ok"] = int(context.get("tools_ok", 0)) + 1
            else:
                context["tools_failed"] = int(context.get("tools_failed", 0)) + 1
            if event.get("cached"):
                context["tools_cached"] = int(context.get("tools_cached", 0)) + 1
            duration_text = self._duration_seconds(context.get("tool_starts", {}).pop(call_id, None), stamp)
            duration_segment = f" duration={duration_text}" if duration_text else ""
            payload = event.get("output") if success else event.get("error")
            if not debug_enabled:
                return False
            self._log_line(
                "DEBUG",
                f"[MTP TOOL END] status={success_text} tool={event.get('tool_name')} "
                f"id={event.get('call_id')} cached={event.get('cached')} approval={event.get('approval')}{duration_segment}  {meta}",
            )
            reasoning = event.get("reasoning")
            if isinstance(reasoning, str) and reasoning.strip():
                self._print_wrapped_block("  Reasoning", reasoning.strip(), indent="    ", width=100)
            self._print_json_block("  Result", payload, indent="    ", max_chars=900)
            member_id = context.get("delegated_calls", {}).pop(call_id, None)
            if member_id:
                print(
                    f"  Delegation: completed member={member_id} parent_call_id={call_id} "
                    f"status={success_text}"
                )
                print("  <member_output>")
                self._print_json_block(
                    "    Summary",
                    {"member_id": member_id, "status": success_text, "result": payload},
                    indent="      ",
                    max_chars=900,
                )
                print("  </member_output>")
            return False

        if event_type == "batch_started":
            if not debug_enabled:
                return False
            print(
                f"[MTP BATCH] round={event.get('round')} idx={event.get('batch_index')} "
                f"mode={event.get('mode')} call_ids={event.get('call_ids')}  {meta}"
            )
            return False

        if event_type == "assistant_tool_message":
            if not debug_enabled:
                return False
            message = event.get("message", {})
            tool_calls = message.get("tool_calls", []) if isinstance(message, dict) else []
            print(f"[MTP ASSISTANT TOOL MSG] round={event.get('round')} tool_calls={len(tool_calls)}  {meta}")
            for call in tool_calls:
                function = call.get("function", {})
                args = function.get("arguments")
                if isinstance(args, str):
                    try:
                        parsed = json.loads(args)
                        args = json.dumps(parsed, ensure_ascii=True, sort_keys=True)
                    except Exception:
                        pass
                print(f"  - name={function.get('name')} id={call.get('id')}")
                self._print_wrapped_block("    args", self._short(args), indent="      ", width=100)
            return False

        if event_type == "strict_violations":
            violations = list(event.get("violations", []))
            print(f"[MTP STRICT VIOLATIONS] round={event.get('round')} count={len(violations)}  {meta}")
            for violation in violations:
                print(
                    "  - "
                    f"call_id={violation.get('call_id')} tool={violation.get('tool_name')} "
                    f"message={violation.get('message')}"
                )
            return False

        if event_type == "text_chunk":
            chunk = str(event.get("chunk", ""))
            if chunk:
                print(chunk, end="", flush=True)
                return True
            return printed_chunk

        if event_type == "run_completed":
            if printed_chunk:
                print()
            elapsed = self._duration_seconds(context.get("run_started_at"), stamp)
            elapsed_segment = f" elapsed={elapsed}" if elapsed else ""
            print(f"\n{self._hr('=')}")
            self._log_line("INFO", f"[MTP RUN END] {meta}")
            print(self._hr("="))
            print(f"Rounds          : {event.get('rounds')}")
            print(f"Total Tool Calls: {event.get('total_tool_calls')}")
            print(f"Tools Succeeded : {context.get('tools_ok', 0)}")
            print(f"Tools Failed    : {context.get('tools_failed', 0)}")
            print(f"Tools Cached    : {context.get('tools_cached', 0)}")
            if elapsed_segment:
                print(f"Elapsed         : {elapsed_segment.removeprefix(' elapsed=')}")
            elapsed_seconds = self._as_float(elapsed_segment.removeprefix(" elapsed=").removesuffix("s")) if elapsed_segment else None
            metrics_level = "DEBUG" if debug_enabled else "INFO"
            self._print_metrics_block(context, duration_seconds=elapsed_seconds, title="RUN METRICS", level=metrics_level)
            final_text = str(event.get("final_text", ""))
            self._print_wrapped_block("Final Text", final_text, indent="  ", width=100)
            return False

        if event_type == "run_cancelled":
            elapsed = self._duration_seconds(context.get("run_started_at"), stamp)
            elapsed_segment = f" elapsed={elapsed}" if elapsed else ""
            self._log_line("INFO", f"[MTP RUN CANCELLED] round={event.get('round')}{elapsed_segment}  {meta}")
            return False

        if event_type == "run_paused":
            self._log_line("INFO", f"[MTP RUN PAUSED] round={event.get('round')} tool_name={event.get('tool_name')}  {meta}")
            self._print_wrapped_block("Reason", event.get("reason"), indent="  ", width=100)
            return False

        if event_type == "run_terminated":
            self._log_line(
                "INFO",
                f"[MTP RUN TERMINATED] round={event.get('round')} tool_name={event.get('tool_name')}  {meta}",
            )
            self._print_wrapped_block("Reason", event.get("reason"), indent="  ", width=100)
            return False

        if event_type == "run_failed":
            self._log_line(
                "ERROR",
                f"[MTP RUN FAILED] round={event.get('round')} error_type={event.get('error_type')}  {meta}",
            )
            self._print_wrapped_block("Error", event.get("error"), indent="  ", width=100)
            return False

        if event_type == "tool_retry_requested":
            self._log_line("INFO", f"[MTP TOOL RETRY] round={event.get('round')} tool_name={event.get('tool_name')}  {meta}")
            self._print_wrapped_block("Feedback", event.get("feedback"), indent="  ", width=100)
            return False

        print(json.dumps(event, default=str))
        return False
