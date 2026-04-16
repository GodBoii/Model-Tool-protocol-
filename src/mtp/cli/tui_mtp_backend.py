"""
MTP Provider Backend Execution Module

This module handles chat execution for MTP SDK providers (non-Codex backends).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from mtp import Agent
from .tui_model_context import get_context_window, format_context_usage


@dataclass(slots=True)
class MTPRunResult:
    """Result from an MTP provider chat execution."""
    text: str
    tool_events: list[str]
    warnings: list[str]
    usage_lines: list[str]


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
    emit_live: Callable[[str, str], None] | None = None,
    provider_name: str | None = None,
    model_name: str | None = None,
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
    final_text_chunks: list[str] = []
    thinking_chunks: list[str] = []  # Collect thinking/reasoning tokens
    
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
            stream_tool_events=True,  # Enable tool event streaming
            stream_tool_results=False,  # Disable tool result streaming
        ):
            event_type = event.get("type")
            
            # Handle LLM response events to capture metrics
            if event_type == "llm_response":
                # Mark generation start
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
                
                # Extract reasoning/thinking tokens from metadata
                reasoning_text = event.get("reasoning")
                if reasoning_text and isinstance(reasoning_text, str):
                    thinking_chunks.append(reasoning_text)
                    # Don't emit live thinking previews - will show full thinking at the end
                
                duration = event.get("duration_seconds", 0.0)
                if duration:
                    total_duration += duration
            
            # Handle tool events
            elif event_type == "tool_started":
                tool_name = event.get("tool_name", "unknown")
                reasoning = event.get("reasoning", "")
                if reasoning:
                    tool_event_msg = f"🔧 {tool_name}: {reasoning}"
                else:
                    tool_event_msg = f"🔧 {tool_name}"
                tool_events.append(tool_event_msg)
                if emit_live:
                    emit_live("tool", tool_event_msg)
            
            elif event_type == "tool_finished":
                tool_name = event.get("tool_name", "unknown")
                success = event.get("success", False)
                if success:
                    tool_events.append(f"  ✓ {tool_name} completed")
                else:
                    tool_events.append(f"  ✗ {tool_name} failed")
            elif event_type == "reasoning_chunk":
                chunk = event.get("chunk", "")
                thinking_chunks.append(chunk)
                if emit_live:
                    emit_live("reasoning", chunk)

            # Handle text chunks
            elif event_type == "text_chunk":
                # Track token generation timing
                current_time = perf_counter()
                if first_token_time is None:
                    first_token_time = current_time
                last_token_time = current_time
                
                chunk = event.get("chunk", "")
                final_text_chunks.append(chunk)
                if emit_live:
                    emit_live("text", chunk)
            
            # Handle completion
            elif event_type == "run_completed":
                final_text = event.get("final_text", "")
                if final_text and not final_text_chunks:
                    final_text_chunks.append(final_text)
        
        if emit_live:
            emit_live("status", "Processing response...")
        
        # Combine final text
        result_text = "".join(final_text_chunks) if final_text_chunks else ""
        
        # Format usage metrics
        usage_lines = []
        if llm_calls > 0:
            # Calculate token generation speed
            # Use actual generation time if available (time between first and last token)
            generation_duration = 0.0
            if first_token_time is not None and last_token_time is not None:
                generation_duration = last_token_time - first_token_time
            
            # Fallback to total duration if generation timing not available
            if generation_duration <= 0:
                generation_duration = total_duration
            
            # Calculate tokens per second (output tokens / generation time)
            tokens_per_sec = 0.0
            if generation_duration > 0 and total_output_tokens > 0:
                tokens_per_sec = total_output_tokens / generation_duration
            elif total_duration > 0 and total_tokens > 0:
                # Fallback: use total tokens / total duration
                tokens_per_sec = total_tokens / total_duration
            
            # Get context window for this model
            context_window, source = get_context_window(provider_name, model_name)
            
            # Context window line (for progress bar rendering)
            context_str, context_pct = format_context_usage(total_tokens, provider_name, model_name)
            usage_lines.append(f"context_window={context_str}")
            
            # Main token line
            usage_lines.append(
                f"tokens(in/out/total/reasoning)={total_input_tokens}/{total_output_tokens}/{total_tokens}/{reasoning_tokens}"
            )
            
            # Thinking tokens (if any) - show full text, not truncated
            if thinking_chunks:
                thinking_text = " | ".join(thinking_chunks)
                # Don't truncate - show full thinking process
                usage_lines.append(f"thinking={thinking_text}")
            
            # Cache tokens (only if any cache tokens exist)
            if any([cached_input_tokens, cache_write_tokens, cache_creation_input_tokens, cache_read_input_tokens]):
                usage_lines.append(
                    f"cache(input/write/create/read)={cached_input_tokens}/{cache_write_tokens}/{cache_creation_input_tokens}/{cache_read_input_tokens}"
                )
            
            # Performance metrics
            usage_lines.append(f"llm_calls={llm_calls}")
            usage_lines.append(f"duration={total_duration:.2f}s")
            if tokens_per_sec > 0:
                usage_lines.append(f"speed={tokens_per_sec:.1f} tokens/s")
            
            # Time to first token (TTFT) - important for perceived latency
            if generation_start_time is not None and first_token_time is not None:
                ttft = first_token_time - generation_start_time
                if ttft > 0:
                    usage_lines.append(f"ttft={ttft:.2f}s")
        else:
            # Fallback if no metrics captured
            usage_lines.append("tokens(in/out/total/reasoning)=unknown/unknown/unknown/unknown")
        
        return MTPRunResult(
            text=result_text,
            tool_events=tool_events,
            warnings=warnings,
            usage_lines=usage_lines,
        )
    
    except Exception as exc:
        error_msg = str(exc)
        warnings.append(f"Execution error: {error_msg}")
        
        return MTPRunResult(
            text=f"Error: {error_msg}",
            tool_events=[],
            warnings=warnings,
            usage_lines=["tokens(in/out/total/reasoning)=error/error/error/error"],
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
    # Debug: Print autoresearch state
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
        stream_tool_events=True,  # Enable tool event streaming
        stream_tool_results=False,  # Disable tool result streaming
    )
    
    # Debug: Verify autoresearch state after creation
    if debug_mode:
        print(f"[DEBUG] Agent created with autoresearch={agent.autoresearch}")
        # Check if agent.terminate tool is registered
        tool_names = [tool.name for tool in agent.registry.list_tools()]
        has_terminate = "agent.terminate" in tool_names
        print(f"[DEBUG] agent.terminate tool registered: {has_terminate}")
    
    return agent
