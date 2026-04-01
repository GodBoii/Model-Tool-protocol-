from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
import json
import textwrap
from typing import Any

from .agent import Agent, RunOutput
from .protocol import ToolResult
from .runtime import ToolRegistry
from .agent import ProviderAdapter


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
        mode: str = "standalone",
        members: dict[str, Agent] | None = None,
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
            mode=mode,
            members=members,
        )

    def run(self, prompt: str, *, max_rounds: int = 5, tool_call_limit: int | None = None) -> str:
        return self._agent.run_loop(user_input=prompt, max_rounds=max_rounds, tool_call_limit=tool_call_limit)

    def run_output(
        self,
        prompt: str,
        *,
        max_rounds: int = 5,
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
        tool_call_limit: int | None = None,
        run_id: str | None = None,
        input_schema: dict[str, Any] | None = None,
    ) -> Iterator[str]:
        return self._agent.run_loop_stream(
            user_input=prompt,
            max_rounds=max_rounds,
            tool_call_limit=tool_call_limit,
            run_id=run_id,
            input_schema=input_schema,
        )

    async def arun(self, prompt: str, *, max_rounds: int = 5, tool_call_limit: int | None = None) -> str:
        return await self._agent.arun_loop(user_input=prompt, max_rounds=max_rounds, tool_call_limit=tool_call_limit)

    async def arun_output(
        self,
        prompt: str,
        *,
        max_rounds: int = 5,
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
        stream_final: bool = True,
        run_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tool_call_limit: int | None = None,
        input_schema: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        return self._agent.run_loop_events(
            user_input=prompt,
            max_rounds=max_rounds,
            stream_final=stream_final,
            run_id=run_id,
            user_id=user_id,
            session_id=session_id,
            metadata=metadata,
            tool_call_limit=tool_call_limit,
            input_schema=input_schema,
        )

    def arun_events(
        self,
        prompt: str,
        *,
        max_rounds: int = 5,
        stream_final: bool = True,
        run_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tool_call_limit: int | None = None,
        input_schema: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        return self._agent.arun_loop_events(
            user_input=prompt,
            max_rounds=max_rounds,
            stream_final=stream_final,
            run_id=run_id,
            user_id=user_id,
            session_id=session_id,
            metadata=metadata,
            tool_call_limit=tool_call_limit,
            input_schema=input_schema,
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
        stream: bool = False,
        stream_events: bool = False,
        run_id: str | None = None,
        tool_call_limit: int | None = None,
        event_format: str = "pretty",
    ) -> None:
        if stream_events:
            if event_format not in {"pretty", "json"}:
                raise ValueError("event_format must be 'pretty' or 'json'")
            printed_chunk = False
            pretty_context: dict[str, Any] = {
                "member_map": {},
                "delegated_calls": {},
                "tool_starts": {},
            }
            for event in self.run_events(
                prompt,
                max_rounds=max_rounds,
                stream_final=stream,
                run_id=run_id,
                tool_call_limit=tool_call_limit,
            ):
                if event_format == "json":
                    print(json.dumps(event, default=str))
                    continue
                printed_chunk = self._print_pretty_event(event, printed_chunk=printed_chunk, context=pretty_context)
            return
        if not stream:
            print(self.run(prompt, max_rounds=max_rounds, tool_call_limit=tool_call_limit))
            return
        for chunk in self.run_stream(
            prompt,
            max_rounds=max_rounds,
            tool_call_limit=tool_call_limit,
            run_id=run_id,
        ):
            print(chunk, end="", flush=True)
        print()

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

        def meta() -> str:
            return f"[{sequence}|{stamp}]"

        def section(title: str) -> None:
            print(f"\n------{title}------ {meta()}")

        def list_block(title: str, values: list[Any]) -> None:
            print(f"{title}:")
            if not values:
                print("  - (none)")
                return
            for value in values:
                print(f"  - {value}")

        if event_type == "run_started":
            section("agent-run-started")
            print(f"run_id: {event.get('run_id')}")
            print(f"max_rounds: {event.get('max_rounds')}")
            print(f"tools_available: {event.get('tools_available')}")
            direct_tools = [str(tool) for tool in list(event.get("direct_tool_names", []))]
            if not direct_tools:
                all_tools = [str(tool) for tool in list(event.get("tool_names", []))]
                member_delegation_tools = {str(member.get("delegation_tool")) for member in list(event.get("member_agents", []))}
                direct_tools = [tool for tool in all_tools if tool not in member_delegation_tools and not tool.startswith("agent.member.")]
            list_block("tools", direct_tools)
            member_agents = list(event.get("member_agents", []))
            context["member_map"] = {str(member.get("id")): member for member in member_agents}
            print("sub_agents:")
            if not member_agents:
                print("  - (none)")
            else:
                for member in member_agents:
                    member_id = member.get("id")
                    print(f"  - id: {member_id}")
                    print(f"    mode: {member.get('mode')}")
                    print(f"    delegation_tool: {member.get('delegation_tool')}")
                    role = member.get("role")
                    if role:
                        print(f"    role: {role}")
                    member_tools = [str(tool) for tool in list(member.get("tools", []))]
                    if not member_tools:
                        print("    tools: (none)")
                    else:
                        print("    tools:")
                        for tool in member_tools:
                            print(f"      - {tool}")
            list_block("system_instructions", list(event.get("system_instructions", [])))
            list_block("user_instructions", list(event.get("user_instructions", [])))
            list_block("orchestration_instructions", list(event.get("orchestration_instructions", [])))
            print(f"user_message: {event.get('user_message')}")
            return False

        if event_type == "round_started":
            section(f"round-{event.get('round')}-started")
            return False

        if event_type == "plan_received":
            section("plan-received")
            print(f"round: {event.get('round')}")
            batches = event.get("batches", [])
            for idx, batch in enumerate(batches, start=1):
                print(f"batch#{idx}: mode={batch.get('mode')}")
                calls = list(batch.get("calls", []))
                call_ids = list(batch.get("call_ids", []))
                for call_name, call_id in zip(calls, call_ids, strict=False):
                    print(f"  - call: {call_name} ({call_id})")
            return False

        if event_type == "tool_started":
            call_id = str(event.get("call_id"))
            context.setdefault("tool_starts", {})[call_id] = stamp
            print(
                f"{meta()} [tool-started] round={event.get('round')} "
                f"tool={event.get('tool_name')} id={event.get('call_id')} "
                f"args={event.get('arguments')}"
            )
            tool_name = str(event.get("tool_name", ""))
            if tool_name.startswith("agent.member."):
                member_id = tool_name.removeprefix("agent.member.")
                context.setdefault("delegated_calls", {})[call_id] = member_id
                section(f"sub-agent-run-started:{member_id}")
                print(f"parent_call_id: {call_id}")
                print(f"task_args: {event.get('arguments')}")
                member = context.get("member_map", {}).get(member_id, {})
                role = member.get("role")
                if role:
                    print(f"role: {role}")
                member_tools = list(member.get("tools", []))
                list_block("member_tools", member_tools)
            return False

        if event_type == "tool_finished":
            success = "success" if event.get("success") else "failed"
            call_id = str(event.get("call_id"))
            start_stamp = context.get("tool_starts", {}).get(call_id)
            duration_text = ""
            if isinstance(start_stamp, str) and "T" in start_stamp and "T" in stamp:
                try:
                    start_dt = start_stamp.replace("Z", "+00:00")
                    end_dt = stamp.replace("Z", "+00:00")
                    from datetime import datetime

                    delta = datetime.fromisoformat(end_dt) - datetime.fromisoformat(start_dt)
                    duration_text = f" duration={delta.total_seconds():.3f}s"
                except Exception:
                    duration_text = ""
            print(
                f"{meta()} [tool-finished] {success} tool={event.get('tool_name')} "
                f"id={event.get('call_id')} cached={event.get('cached')}{duration_text} "
                f"output={event.get('output')}"
            )
            member_id = context.get("delegated_calls", {}).pop(call_id, None)
            if member_id:
                section(f"sub-agent-run-completed:{member_id}")
                print(f"parent_call_id: {call_id}")
                print(f"status: {success}")
                print(f"result: {event.get('output')}")
            return False

        if event_type == "batch_started":
            print(
                f"{meta()} [batch-started] round={event.get('round')} "
                f"batch_index={event.get('batch_index')} "
                f"mode={event.get('mode')} call_ids={event.get('call_ids')}"
            )
            return False

        if event_type == "assistant_tool_message":
            message = event.get("message", {})
            tool_calls = message.get("tool_calls", []) if isinstance(message, dict) else []
            print(f"{meta()} [assistant-tool-message] round={event.get('round')} tool_calls={len(tool_calls)}")
            for call in tool_calls:
                function = call.get("function", {})
                args = function.get("arguments")
                if isinstance(args, str):
                    try:
                        parsed = json.loads(args)
                        args = json.dumps(parsed, ensure_ascii=True, sort_keys=True)
                    except Exception:
                        pass
                print(
                    "  - "
                    f"name={function.get('name')} id={call.get('id')} args={args}"
                )
            return False

        if event_type == "strict_violations":
            section("strict-violations")
            print(f"round: {event.get('round')}")
            violations = list(event.get("violations", []))
            if not violations:
                print("violations: (none)")
            else:
                print("violations:")
                for violation in violations:
                    print(
                        "  - "
                        f"call_id={violation.get('call_id')} "
                        f"tool={violation.get('tool_name')} "
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
            section("agent-run-completed")
            print(f"rounds: {event.get('rounds')}")
            print(f"total_tool_calls: {event.get('total_tool_calls')}")
            final_text = str(event.get("final_text", ""))
            wrapped = textwrap.wrap(final_text, width=100) or [""]
            print("final_text:")
            for line in wrapped:
                print(f"  {line}")
            return False

        if event_type == "run_cancelled":
            section("agent-run-cancelled")
            print(f"round: {event.get('round')}")
            return False

        if event_type == "run_paused":
            section("agent-run-paused")
            print(f"round: {event.get('round')}")
            print(f"tool_name: {event.get('tool_name')}")
            print(f"reason: {event.get('reason')}")
            return False

        if event_type == "tool_retry_requested":
            section("tool-retry-requested")
            print(f"round: {event.get('round')}")
            print(f"tool_name: {event.get('tool_name')}")
            print(f"feedback: {event.get('feedback')}")
            return False

        print(json.dumps(event, default=str))
        return False
