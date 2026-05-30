#!/usr/bin/env python3
"""
Test script to verify thinking tokens display in TUI.

This script tests:
1. Ollama provider with think=True returns reasoning metadata
2. Agent passes reasoning through llm_response events
3. TUI backend extracts thinking tokens
4. TUI rendering displays thinking tokens prominently
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from mtp import Agent
from mtp.providers import OllamaToolCallingProvider
from mtp.toolkits.local import register_local_toolkits


def test_ollama_thinking_tokens():
    """Test Ollama provider with thinking tokens enabled."""
    print("=" * 80)
    print("TEST: Ollama Thinking Tokens Display")
    print("=" * 80)
    print()
    
    # Create provider with think=True
    print("1. Creating Ollama provider with think=True...")
    provider = OllamaToolCallingProvider(
        model="qwen3:1.7b",
        host="http://localhost:11434",
        think=True,  # Enable thinking tokens
    )
    print("   ✓ Provider created")
    print()
    
    # Create registry
    print("2. Creating tool registry...")
    registry = Agent.ToolRegistry()
    register_local_toolkits(registry)
    print(f"   ✓ Registry created with {len(registry.list_tools())} tools")
    print()
    
    # Create agent
    print("3. Creating MTP agent...")
    agent = Agent.MTPAgent(
        provider=provider,
        tools=registry,
        instructions="You are a helpful assistant.",
        debug_mode=False,
        stream_tool_events=True,
        stream_tool_results=False,
    )
    print("   ✓ Agent created")
    print()
    
    # Test prompt
    prompt = "What is 2 + 2? Think step by step."
    print(f"4. Running prompt: '{prompt}'")
    print()
    
    # Track events
    events_captured = {
        "llm_response": [],
        "text_chunk": [],
        "run_completed": [],
    }
    
    print("5. Streaming events:")
    print("-" * 80)
    
    for event in agent.run_events(
        prompt=prompt,
        max_rounds=3,
        stream_final=True,
        stream_tool_events=True,
        stream_tool_results=False,
    ):
        event_type = event.get("type")
        
        if event_type == "llm_response":
            events_captured["llm_response"].append(event)
            usage = event.get("usage", {})
            reasoning = event.get("reasoning")
            
            print(f"   [llm_response]")
            print(f"      usage: {usage}")
            if reasoning:
                print(f"      reasoning: {reasoning[:100]}...")
            else:
                print(f"      reasoning: None")
            print()
        
        elif event_type == "text_chunk":
            chunk = event.get("chunk", "")
            events_captured["text_chunk"].append(event)
            sys.stdout.write(chunk)
            sys.stdout.flush()
        
        elif event_type == "run_completed":
            events_captured["run_completed"].append(event)
            print()
            print(f"   [run_completed]")
            print(f"      final_text length: {len(event.get('final_text', ''))}")
            print()
    
    print("-" * 80)
    print()
    
    # Verify results
    print("6. Verification:")
    print()
    
    llm_responses = events_captured["llm_response"]
    print(f"   ✓ Captured {len(llm_responses)} llm_response events")
    
    has_reasoning = any(event.get("reasoning") for event in llm_responses)
    if has_reasoning:
        print(f"   ✓ Reasoning metadata found in llm_response events")
        for i, event in enumerate(llm_responses):
            reasoning = event.get("reasoning")
            if reasoning:
                print(f"      Event {i+1}: {reasoning[:80]}...")
    else:
        print(f"   ✗ No reasoning metadata found in llm_response events")
        print(f"      This may indicate:")
        print(f"      - Ollama model doesn't support thinking tokens")
        print(f"      - think=True not working")
        print(f"      - Provider not passing reasoning through")
    
    print()
    
    has_usage = any(event.get("usage") for event in llm_responses)
    if has_usage:
        print(f"   ✓ Usage metrics found in llm_response events")
        for i, event in enumerate(llm_responses):
            usage = event.get("usage")
            if usage:
                print(f"      Event {i+1}: {usage}")
    else:
        print(f"   ✗ No usage metrics found")
    
    print()
    print("=" * 80)
    print("TEST COMPLETE")
    print("=" * 80)
    print()
    
    return has_reasoning


def test_tui_backend_extraction():
    """Test TUI backend extracts thinking tokens correctly."""
    print("=" * 80)
    print("TEST: TUI Backend Thinking Token Extraction")
    print("=" * 80)
    print()
    
    from mtp.cli import tui_mtp_backend
    
    # Create provider with think=True
    print("1. Creating Ollama provider with think=True...")
    provider = OllamaToolCallingProvider(
        model="qwen3:1.7b",
        host="http://localhost:11434",
        think=True,
    )
    print("   ✓ Provider created")
    print()
    
    # Create registry
    print("2. Creating tool registry...")
    registry = Agent.ToolRegistry()
    register_local_toolkits(registry)
    print(f"   ✓ Registry created")
    print()
    
    # Build agent
    print("3. Building MTP agent...")
    agent = tui_mtp_backend.build_mtp_agent(
        provider=provider,
        tools=registry,
        cwd=Path.cwd(),
        max_rounds=3,
        autoresearch=False,
        research_instructions=None,
        debug_mode=False,
    )
    print("   ✓ Agent built")
    print()
    
    # Run prompt
    prompt = "Calculate 5 * 7. Think step by step."
    print(f"4. Running prompt: '{prompt}'")
    print()
    
    # Track live events
    live_events = []
    
    def emit_live(kind: str, message: str):
        live_events.append((kind, message))
        print(f"   [live:{kind}] {message[:80]}")
    
    print("5. Executing with TUI backend:")
    print("-" * 80)
    
    result = tui_mtp_backend.run_mtp_prompt(
        agent=agent,
        prompt=prompt,
        max_rounds=3,
        emit_live=emit_live,
        provider_name="ollama",
        model_name="qwen3:1.7b",
    )
    
    print("-" * 80)
    print()
    
    # Verify results
    print("6. Verification:")
    print()
    
    print(f"   Response text length: {len(result.text)}")
    print(f"   Tool events: {len(result.tool_events)}")
    print(f"   Warnings: {len(result.warnings)}")
    print(f"   Usage lines: {len(result.usage_lines)}")
    print()
    
    print("   Usage lines:")
    for i, line in enumerate(result.usage_lines):
        print(f"      {i+1}. {line}")
    print()
    
    # Check for thinking tokens
    has_thinking = any("thinking=" in line for line in result.usage_lines)
    if has_thinking:
        print(f"   ✓ Thinking tokens found in usage_lines")
        for line in result.usage_lines:
            if "thinking=" in line:
                print(f"      {line}")
    else:
        print(f"   ✗ No thinking tokens in usage_lines")
    
    print()
    
    # Check for context window
    has_context = any("context_window=" in line for line in result.usage_lines)
    if has_context:
        print(f"   ✓ Context window found in usage_lines")
        for line in result.usage_lines:
            if "context_window=" in line:
                print(f"      {line}")
    else:
        print(f"   ✗ No context window in usage_lines")
    
    print()
    
    # Check for token metrics
    has_tokens = any("tokens(" in line for line in result.usage_lines)
    if has_tokens:
        print(f"   ✓ Token metrics found in usage_lines")
        for line in result.usage_lines:
            if "tokens(" in line:
                print(f"      {line}")
    else:
        print(f"   ✗ No token metrics in usage_lines")
    
    print()
    
    # Check for speed metrics
    has_speed = any("speed=" in line for line in result.usage_lines)
    if has_speed:
        print(f"   ✓ Speed metrics found in usage_lines")
        for line in result.usage_lines:
            if "speed=" in line:
                print(f"      {line}")
    else:
        print(f"   ✗ No speed metrics in usage_lines")
    
    print()
    
    # Check live events
    thinking_events = [e for e in live_events if e[0] == "thinking"]
    if thinking_events:
        print(f"   ✓ Thinking events emitted during execution ({len(thinking_events)} events)")
        for kind, msg in thinking_events[:3]:
            print(f"      {msg[:80]}")
    else:
        print(f"   ✗ No thinking events emitted")
    
    print()
    print("=" * 80)
    print("TEST COMPLETE")
    print("=" * 80)
    print()
    
    return has_thinking


if __name__ == "__main__":
    print()
    print("THINKING TOKENS DISPLAY TEST SUITE")
    print("=" * 80)
    print()
    print("Prerequisites:")
    print("  - Ollama server running on http://localhost:11434")
    print("  - qwen3:1.7b model available")
    print()
    print("This test verifies:")
    print("  1. Ollama provider returns reasoning metadata")
    print("  2. Agent passes reasoning through events")
    print("  3. TUI backend extracts thinking tokens")
    print("  4. Usage lines include thinking tokens")
    print()
    input("Press Enter to start tests...")
    print()
    
    # Run tests
    test1_passed = test_ollama_thinking_tokens()
    print()
    
    test2_passed = test_tui_backend_extraction()
    print()
    
    # Summary
    print("=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    print()
    print(f"  Test 1 (Agent Events):      {'PASS' if test1_passed else 'FAIL'}")
    print(f"  Test 2 (TUI Backend):       {'PASS' if test2_passed else 'FAIL'}")
    print()
    
    if test1_passed and test2_passed:
        print("  ✓ All tests passed!")
        print()
        print("  Next step: Test in actual TUI CLI")
        print("  Run: python -m mtp.cli.tui")
        print("  Then: /backend ollama")
        print("  Then: Ask a question that requires thinking")
    else:
        print("  ✗ Some tests failed")
        print()
        print("  Troubleshooting:")
        if not test1_passed:
            print("    - Check if Ollama model supports thinking tokens")
            print("    - Verify think=True is being passed to provider")
        if not test2_passed:
            print("    - Check TUI backend event handling")
            print("    - Verify usage_lines formatting")
    
    print()
    print("=" * 80)
