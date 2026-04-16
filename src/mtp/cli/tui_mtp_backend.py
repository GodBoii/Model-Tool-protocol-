"""
MTP Provider Backend Execution Module

This module handles chat execution for MTP SDK providers (non-Codex backends).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from mtp import Agent


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
) -> MTPRunResult:
    """
    Execute a prompt using an MTP provider agent with event streaming.
    
    Args:
        agent: Initialized MTP agent instance
        prompt: User prompt to execute
        max_rounds: Maximum number of tool-use rounds
        emit_live: Optional callback for live event streaming (kind, message)
    
    Returns:
        MTPRunResult with response text, tool events, warnings, and usage metrics
    """
    warnings: list[str] = []
    tool_events: list[str] = []
    final_text_chunks: list[str] = []
    
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
            
            # Handle tool events
            if event_type == "tool_started":
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
            
            # Handle text chunks
            elif event_type == "text_chunk":
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
        
        # Extract usage metrics
        usage_lines = _extract_usage_metrics(agent, result_text)
        
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
            usage_lines=[],
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
    return Agent.MTPAgent(
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
