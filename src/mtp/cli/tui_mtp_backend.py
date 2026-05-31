"""
MTP Provider Backend Execution Module

This module handles chat execution for MTP SDK providers (non-Codex backends).
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from mtp import Agent

from .tui_model_context import format_context_usage, get_context_window
from .tui_theme import SYM_ERR, SYM_OK


@dataclass(slots=True)
class MTPRunResult:
    """Result from an MTP provider chat execution."""

    text: str
    tool_events: list[str]
    warnings: list[str]
    usage_lines: list[str]
    tool_details: list[dict[str, Any]]
    assistant_blocks: list[dict[str, Any]]
    thinking_text: str


def _merge_stream_text(existing: str, incoming: str) -> str:
    """Append streamed text while avoiding obvious duplicate full-payload replays."""
    if not incoming:
        return existing
    if not existing:
        return incoming
    if incoming in existing:
        return existing
    if existing in incoming:
        return incoming

    max_overlap = min(len(existing), len(incoming), 4000)
    for size in range(max_overlap, 0, -1):
        if existing.endswith(incoming[:size]):
            return existing + incoming[size:]
    return existing + incoming


def _append_unique_text(chunks: list[str], chunk: str) -> None:
    if not chunk:
        return
    merged = _merge_stream_text("".join(chunks), chunk)
    chunks[:] = [merged]


def _format_tool_result_preview(tool_name: str, payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return ""
        if tool_name.startswith("edit.") and "\n" in text:
            return f"```text\n{text[:12000]}\n```"
        return text[:12000]
    if isinstance(payload, dict):
        if tool_name == "edit.create_file" and isinstance(payload.get("content"), str):
            file_name = str(payload.get("file") or "file")
            content = payload["content"][:12000]
            return f"Created `{file_name}`:\n```text\n{content}\n```"
        if tool_name == "edit.apply_patch":
            diff = payload.get("diff")
            if isinstance(diff, str) and diff.strip():
                return f"```diff\n{diff[:12000]}\n```"
            new_text = payload.get("new_text")
            if isinstance(new_text, str) and new_text.strip():
                return f"```text\n{new_text[:12000]}\n```"
        if tool_name in {"fs.search", "fs.grep"} and isinstance(payload.get("hits"), list):
            lines = []
            for hit in payload["hits"][:8]:
                if not isinstance(hit, dict):
                    continue
                file_name = str(hit.get("file") or "")
                snippets = hit.get("snippets") or hit.get("match") or []
                snippet_text = ""
                if isinstance(snippets, list) and snippets:
                    snippet_text = str(snippets[0])
                lines.append(f"- {file_name}: {snippet_text}")
            if lines:
                return "\n".join(lines)
        if tool_name == "project.inspect":
            return "```json\n" + json.dumps(payload, indent=2, ensure_ascii=False)[:12000] + "\n```"
        return "```json\n" + json.dumps(payload, indent=2, ensure_ascii=False)[:12000] + "\n```"
    if isinstance(payload, list):
        return "```json\n" + json.dumps(payload, indent=2, ensure_ascii=False)[:12000] + "\n```"
    return str(payload)[:12000]


def _append_text_block(blocks: list[dict[str, Any]], text: str) -> None:
    if not text:
        return
    if blocks and blocks[-1].get("type") == "text":
        blocks[-1]["text"] = _merge_stream_text(str(blocks[-1].get("text") or ""), text)
        return
    blocks.append({"type": "text", "text": text})


def _append_thinking_block(blocks: list[dict[str, Any]], text: str) -> None:
    if not text:
        return
    if blocks and blocks[-1].get("type") == "thinking":
        blocks[-1]["text"] = _merge_stream_text(str(blocks[-1].get("text") or ""), text)
        return
    blocks.append({"type": "thinking", "text": text})


def _ensure_tool_group(
    blocks: list[dict[str, Any]],
    *,
    batch_index: Any = None,
    mode: str | None = None,
) -> dict[str, Any]:
    if blocks and blocks[-1].get("type") == "tool_group":
        last = blocks[-1]
        if batch_index is None or last.get("batch_index") == batch_index:
            if mode and not last.get("mode"):
                last["mode"] = mode
            return last
    group = {"type": "tool_group", "batch_index": batch_index, "mode": mode or "unknown", "items": []}
    blocks.append(group)
    return group


def _upsert_tool_item(
    blocks: list[dict[str, Any]],
    *,
    call_id: Any,
    tool_name: str,
    status: str,
    reasoning: str | None = None,
    batch_index: Any = None,
    mode: str | None = None,
    cached: Any = None,
    error: str | None = None,
    started_at_ms: int | None = None,
    finished_at_ms: int | None = None,
    result_preview: str | None = None,
) -> None:
    call_id_str = str(call_id or tool_name)
    for block in blocks:
        if block.get("type") != "tool_group":
            continue
        for item in block.get("items") or []:
            if str(item.get("call_id") or "") == call_id_str:
                item["status"] = status
                if reasoning:
                    item["reasoning"] = reasoning
                if cached is not None:
                    item["cached"] = cached
                if error:
                    item["error"] = error
                if result_preview is not None:
                    item["result_preview"] = result_preview
                if started_at_ms is not None:
                    item["started_at_ms"] = started_at_ms
                if finished_at_ms is not None:
                    item["finished_at_ms"] = finished_at_ms
                return

    group = _ensure_tool_group(blocks, batch_index=batch_index, mode=mode)
    group["items"].append(
        {
            "call_id": call_id_str,
            "tool_name": tool_name,
            "status": status,
            "reasoning": reasoning,
            "cached": cached,
            "error": error,
            "started_at_ms": started_at_ms,
            "finished_at_ms": finished_at_ms,
            "result_preview": result_preview,
        }
    )


def _extract_tool_events_from_agent(agent: Agent.MTPAgent) -> list[str]:
    """
    Extract tool call events from agent's last run.

    TODO: Implement proper event extraction from agent state.
    For now, returns empty list.
    """
    # This will be populated when we add event streaming support
    return []


def _extract_usage_metrics(agent: Agent.MTPAgent, result: str) -> list[str]:
    """
    Extract usage metrics from agent execution.

    TODO: Implement proper metrics extraction from agent metadata.
    For now, returns placeholder.
    """
    # This will be populated with actual token counts, context usage, etc.
    return [
        "tokens(in/out/total/reasoning)=unknown/unknown/unknown/unknown",
        "context_window=unknown",
    ]


def run_mtp_prompt(
    *,
    agent: Agent.MTPAgent,
    prompt: str,
    max_rounds: int,
    emit_live: Callable[[str, Any], None] | None = None,
    provider_name: str | None = None,
    model_name: str | None = None,
    run_id: str | None = None,
) -> MTPRunResult:
    """
    Execute a prompt using an MTP provider agent with event streaming.

    Args:
        agent: Initialized MTP agent instance
        prompt: User prompt to execute
        max_rounds: Maximum number of tool-use rounds
        emit_live: Optional callback for live event streaming (kind, message)
        provider_name: Provider name for context window detection
        model_name: Model name for context window detection

    Returns:
        MTPRunResult with response text, tool events, warnings, and usage metrics
    """
    warnings: list[str] = []
    tool_events: list[str] = []
    tool_details: list[dict[str, Any]] = []
    final_text_chunks: list[str] = []
    thinking_text = ""
    saw_tool_round = False
    assistant_blocks: list[dict[str, Any]] = []
    planning_duration = 0.0
    tool_execution_duration = 0.0
    finalize_duration = 0.0
    memory_waited = False
    tool_phase_started_at: int | None = None
    tool_phase_finished_at: int | None = None

    # Metrics tracking
    total_input_tokens = 0
    total_output_tokens = 0
    total_tokens = 0
    reasoning_tokens = 0
    cached_input_tokens = 0
    cache_write_tokens = 0
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0
    llm_calls = 0
    total_duration = 0.0

    # Token generation speed tracking
    first_token_time = None
    last_token_time = None
    generation_start_time = None

    try:
        if emit_live:
            emit_live("status", "Sending request to provider...")

        # Use run_events to get streaming events (MTPAgent wrapper method)
        for event in agent.run_events(
            prompt=prompt,
            max_rounds=max_rounds,
            stream_final=True,
            stream_tool_events=True,
            stream_tool_results=True,
            run_id=run_id,
        ):
            event_type = event.get("type")

            # Handle LLM response events to capture metrics
            if event_type == "llm_response":
                if generation_start_time is None:
                    generation_start_time = perf_counter()

                usage = event.get("usage", {})
                if isinstance(usage, dict):
                    total_input_tokens += usage.get("input_tokens", 0)
                    total_output_tokens += usage.get("output_tokens", 0)
                    total_tokens += usage.get("total_tokens", 0)
                    reasoning_tokens += usage.get("reasoning_tokens", 0)
                    cached_input_tokens += usage.get("cached_input_tokens", 0)
                    cache_write_tokens += usage.get("cache_write_tokens", 0)
                    cache_creation_input_tokens += usage.get("cache_creation_input_tokens", 0)
                    cache_read_input_tokens += usage.get("cache_read_input_tokens", 0)
                    llm_calls += 1

                reasoning_text = event.get("reasoning")
                if reasoning_text and isinstance(reasoning_text, str):
                    thinking_text = _merge_stream_text(thinking_text, reasoning_text)

                duration = event.get("duration_seconds", 0.0)
                if duration:
                    total_duration += duration
                    if event.get("stage") == "finalize":
                        finalize_duration += duration
                    else:
                        planning_duration += duration

            elif event_type == "plan_received":
                saw_tool_round = True
                final_text_chunks = []
                detail = {
                    "type": "plan_received",
                    "tool_call_source": event.get("tool_call_source"),
                    "raw_tool_call_count": event.get("raw_tool_call_count"),
                    "derived_batch_count": event.get("derived_batch_count"),
                    "derived_batch_modes": list(event.get("derived_batch_modes") or []),
                    "batches": list(event.get("batches") or []),
                }
                tool_details.append(detail)
                if detail["derived_batch_count"] is not None:
                    detail["observed_at_ms"] = int(perf_counter() * 1000)
                if emit_live:
                    emit_live("tool_detail", detail)

            elif event_type == "batch_started":
                saw_tool_round = True
                final_text_chunks = []
                observed_at_ms = int(perf_counter() * 1000)
                detail = {
                    "type": "batch_started",
                    "batch_index": event.get("batch_index"),
                    "mode": event.get("mode"),
                    "call_ids": list(event.get("call_ids") or []),
                    "observed_at_ms": observed_at_ms,
                }
                tool_details.append(detail)
                _ensure_tool_group(
                    assistant_blocks,
                    batch_index=event.get("batch_index"),
                    mode=str(event.get("mode") or "unknown"),
                )
                if tool_phase_started_at is None:
                    tool_phase_started_at = observed_at_ms
                if emit_live:
                    emit_live("tool_detail", detail)

            elif event_type == "tool_started":
                saw_tool_round = True
                final_text_chunks = []
                tool_name = event.get("tool_name", "unknown")
                reasoning = event.get("reasoning", "")
                observed_at_ms = int(perf_counter() * 1000)
                if reasoning:
                    tool_event_msg = f"🔧 {tool_name}: {reasoning}"
                else:
                    tool_event_msg = f"🔧 {tool_name}"
                tool_events.append(tool_event_msg)
                detail = {
                    "type": "tool_started",
                    "tool_name": tool_name,
                    "call_id": event.get("call_id"),
                    "batch_index": event.get("batch_index"),
                    "depends_on": list(event.get("depends_on") or []),
                    "arguments": event.get("arguments"),
                    "reasoning": reasoning or None,
                    "started_at_ms": observed_at_ms,
                }
                tool_details.append(detail)
                _upsert_tool_item(
                    assistant_blocks,
                    call_id=event.get("call_id"),
                    tool_name=tool_name,
                    status="running",
                    reasoning=reasoning or None,
                    batch_index=event.get("batch_index"),
                    started_at_ms=observed_at_ms,
                )
                if emit_live:
                    emit_live("tool", tool_event_msg)
                    emit_live("tool_detail", detail)

            elif event_type == "tool_finished":
                tool_name = event.get("tool_name", "unknown")
                success = event.get("success", False)
                observed_at_ms = int(perf_counter() * 1000)
                detail = {
                    "type": "tool_finished",
                    "tool_name": tool_name,
                    "call_id": event.get("call_id"),
                    "success": success,
                    "cached": event.get("cached"),
                    "approval": event.get("approval"),
                    "media_counts": event.get("media_counts"),
                    "reasoning": event.get("reasoning"),
                    "finished_at_ms": observed_at_ms,
                    "error": event.get("error"),
                    "output": event.get("output"),
                    "result_preview": _format_tool_result_preview(tool_name, event.get("output") if success else event.get("error")),
                }
                tool_details.append(detail)
                tool_phase_finished_at = observed_at_ms
                _upsert_tool_item(
                    assistant_blocks,
                    call_id=event.get("call_id"),
                    tool_name=tool_name,
                    status="completed" if success else "failed",
                    reasoning=event.get("reasoning"),
                    cached=event.get("cached"),
                    error=event.get("error"),
                    finished_at_ms=observed_at_ms,
                    result_preview=detail["result_preview"],
                )
                if success:
                    msg = f"  {SYM_OK} {tool_name} completed"
                    tool_events.append(msg)
                    if emit_live:
                        emit_live("tool_end", f"{SYM_OK} {tool_name} completed")
                        emit_live("tool_detail", detail)
                else:
                    msg = f"  {SYM_ERR} {tool_name} failed"
                    tool_events.append(msg)
                    if emit_live:
                        emit_live("tool_end", f"{SYM_ERR} {tool_name} failed")
                        emit_live("tool_detail", detail)

            elif event_type == "reasoning_chunk":
                chunk = event.get("chunk", "")
                thinking_text = _merge_stream_text(thinking_text, chunk)
                _append_thinking_block(assistant_blocks, chunk)
                if emit_live:
                    emit_live("reasoning", chunk)

            elif event_type == "text_chunk":
                current_time = perf_counter()
                if first_token_time is None:
                    first_token_time = current_time
                last_token_time = current_time

                chunk = event.get("chunk", "")
                source = str(event.get("source") or "")
                if source in {"finalize_stream", "finalize_fallback"} or not saw_tool_round:
                    _append_unique_text(final_text_chunks, chunk)
                _append_text_block(assistant_blocks, chunk)
                if emit_live:
                    emit_live("text", chunk)

            elif event_type == "run_completed":
                final_text = event.get("final_text", "")
                if final_text:
                    if saw_tool_round or not final_text_chunks or not "".join(final_text_chunks).strip():
                        final_text_chunks = [final_text]
                    if not any(block.get("type") == "text" for block in assistant_blocks):
                        assistant_blocks = [block for block in assistant_blocks if block.get("type") != "text"]
                        _append_text_block(assistant_blocks, final_text)

            elif event_type == "round_started":
                memory_waited = False

        if emit_live:
            emit_live("status", "Processing response...")

        result_text = "".join(final_text_chunks) if final_text_chunks else ""
        if tool_phase_started_at is not None and tool_phase_finished_at is not None:
            tool_execution_duration = max(0.0, (tool_phase_finished_at - tool_phase_started_at) / 1000)

        usage_lines: list[str] = []
        if llm_calls > 0:
            generation_duration = 0.0
            if first_token_time is not None and last_token_time is not None:
                generation_duration = last_token_time - first_token_time

            if generation_duration <= 0:
                generation_duration = total_duration

            tokens_per_sec = 0.0
            if generation_duration > 0 and total_output_tokens > 0:
                tokens_per_sec = total_output_tokens / generation_duration
            elif total_duration > 0 and total_tokens > 0:
                tokens_per_sec = total_tokens / total_duration

            context_window, source = get_context_window(provider_name, model_name)
            _ = (context_window, source)

            context_str, _context_pct = format_context_usage(total_tokens, provider_name, model_name)
            usage_lines.append(f"context_window={context_str}")
            usage_lines.append(
                f"tokens(in/out/total/reasoning)={total_input_tokens}/{total_output_tokens}/{total_tokens}/{reasoning_tokens}"
            )

            if any([cached_input_tokens, cache_write_tokens, cache_creation_input_tokens, cache_read_input_tokens]):
                usage_lines.append(
                    f"cache(input/write/create/read)={cached_input_tokens}/{cache_write_tokens}/{cache_creation_input_tokens}/{cache_read_input_tokens}"
                )

            usage_lines.append(f"llm_calls={llm_calls}")
            usage_lines.append(f"duration={total_duration:.2f}s")
            usage_lines.append(
                f"phases(plan/tools/finalize)={planning_duration:.2f}s/{tool_execution_duration:.2f}s/{finalize_duration:.2f}s"
            )
            usage_lines.append(f"memory_refresh_waited={'yes' if memory_waited else 'no'}")
            if tokens_per_sec > 0:
                usage_lines.append(f"speed={tokens_per_sec:.1f} tokens/s")

            if generation_start_time is not None and first_token_time is not None:
                ttft = first_token_time - generation_start_time
                if ttft > 0:
                    usage_lines.append(f"ttft={ttft:.2f}s")
        else:
            usage_lines.append("tokens(in/out/total/reasoning)=unknown/unknown/unknown/unknown")

        return MTPRunResult(
            text=result_text,
            tool_events=tool_events,
            warnings=warnings,
            usage_lines=usage_lines,
            tool_details=tool_details,
            assistant_blocks=assistant_blocks,
            thinking_text=thinking_text.strip(),
        )

    except Exception as exc:
        error_msg = str(exc)
        warnings.append(f"Execution error: {error_msg}")

        return MTPRunResult(
            text=f"Error: {error_msg}",
            tool_events=[],
            warnings=warnings,
            usage_lines=["tokens(in/out/total/reasoning)=error/error/error/error"],
            tool_details=[],
            assistant_blocks=[],
            thinking_text="",
        )


def build_mtp_agent(
    *,
    provider: Any,
    tools: Agent.ToolRegistry,
    cwd: Path,
    max_rounds: int,
    autoresearch: bool,
    research_instructions: str | None,
    debug_mode: bool = False,
) -> Agent.MTPAgent:
    """
    Build an MTP agent with the given provider and configuration.

    Args:
        provider: Provider instance (OpenAI, Groq, Claude, etc.)
        tools: Tool registry
        cwd: Working directory
        max_rounds: Maximum rounds for execution
        autoresearch: Enable autoresearch mode
        research_instructions: Custom research instructions
        debug_mode: Enable debug logging

    Returns:
        Configured MTP agent instance
    """
    if debug_mode:
        print(f"[DEBUG] Building MTP agent with autoresearch={autoresearch}")

    agent = Agent.MTPAgent(
        provider=provider,
        tools=tools,
        instructions=f"You are a helpful AI assistant. Current working directory: {cwd}",
        debug_mode=debug_mode,
        strict_dependency_mode=True,
        autoresearch=autoresearch,
        research_instructions=research_instructions,
        stream_tool_events=True,
        stream_tool_results=False,
    )

    if debug_mode:
        print(f"[DEBUG] Agent created with autoresearch={agent.autoresearch}")
        tool_names = [tool.name for tool in agent.registry.list_tools()]
        has_terminate = "agent.terminate" in tool_names
        print(f"[DEBUG] agent.terminate tool registered: {has_terminate}")

    return agent
