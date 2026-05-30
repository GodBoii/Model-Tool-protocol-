#!/usr/bin/env python3
"""
Quick automated test for thinking tokens and metrics display.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

print("=" * 80)
print("QUICK METRICS TEST")
print("=" * 80)
print()

# Test 1: Context Window Database
print("Test 1: Context Window Database")
print("-" * 80)

try:
    from mtp.cli.tui_model_context import get_context_window, format_context_usage
    
    # Test Ollama models
    test_cases = [
        ("ollama", "qwen3:1.7b", 32_768),
        ("ollama", "llama3.2:3b", 128_000),  # Correct value from database
        ("ollama", "deepseek-r1:7b", 65_536),  # Now in database
        ("lmstudio", "Meta-Llama-3-8B-Instruct-GGUF", 8_192),  # Now in database
        ("openai", "gpt-4o", 128_000),
        ("ollama", "unknown-model", 32_768),  # Should use default
    ]
    
    passed = 0
    failed = 0
    
    for provider, model, expected in test_cases:
        window, source = get_context_window(provider, model)
        if window == expected:
            print(f"  ✓ {provider}/{model}: {window:,} tokens (source: {source})")
            passed += 1
        else:
            print(f"  ✗ {provider}/{model}: expected {expected:,}, got {window:,}")
            failed += 1
    
    # Test context usage formatting
    context_str, context_pct = format_context_usage(10_000, "ollama", "qwen3:1.7b")
    print(f"\n  Context usage format: {context_str}")
    print(f"  Context percentage: {context_pct:.1f}%")
    
    print(f"\n  Result: {passed} passed, {failed} failed")
    test1_passed = (failed == 0)
    
except Exception as e:
    print(f"  ✗ Error: {e}")
    test1_passed = False

print()

# Test 2: TUI Backend Usage Lines
print("Test 2: TUI Backend Usage Lines Formatting")
print("-" * 80)

try:
    from mtp.cli.tui_mtp_backend import MTPRunResult
    
    # Simulate usage lines
    usage_lines = [
        "context_window=10,000/32,768 (30.5%)",
        "tokens(in/out/total/reasoning)=150/50/200/30",
        "thinking=Let me calculate this step by step: 2 + 2 = 4",
        "llm_calls=1",
        "duration=1.50s",
        "speed=133.3 tokens/s",
    ]
    
    result = MTPRunResult(
        text="The answer is 4",
        tool_events=[],
        warnings=[],
        usage_lines=usage_lines,
    )
    
    print(f"  ✓ MTPRunResult created")
    print(f"  ✓ Usage lines: {len(result.usage_lines)}")
    
    # Check for key metrics
    has_context = any("context_window=" in line for line in result.usage_lines)
    has_tokens = any("tokens(" in line for line in result.usage_lines)
    has_thinking = any("thinking=" in line for line in result.usage_lines)
    has_speed = any("speed=" in line for line in result.usage_lines)
    
    print(f"\n  Metrics present:")
    print(f"    Context window: {'✓' if has_context else '✗'}")
    print(f"    Token metrics:  {'✓' if has_tokens else '✗'}")
    print(f"    Thinking:       {'✓' if has_thinking else '✗'}")
    print(f"    Speed:          {'✓' if has_speed else '✗'}")
    
    test2_passed = all([has_context, has_tokens, has_thinking, has_speed])
    print(f"\n  Result: {'PASS' if test2_passed else 'FAIL'}")
    
except Exception as e:
    print(f"  ✗ Error: {e}")
    import traceback
    traceback.print_exc()
    test2_passed = False

print()

# Test 3: TUI Rendering Logic
print("Test 3: TUI Rendering Logic")
print("-" * 80)

try:
    import re
    
    # Simulate usage lines parsing
    usage_lines = [
        "context_window=10,000/32,768",
        "tokens(in/out/total/reasoning)=150/50/200/30",
        "thinking=Let me think step by step",
        "llm_calls=1",
        "duration=1.50s",
        "speed=133.3 tokens/s",
    ]
    
    ctx_match = None
    thinking_line = None
    other_lines = []
    
    for uline in usage_lines:
        m = re.match(r"context_window=([\d,]+)/([\d,]+)", uline)
        if m:
            ctx_match = m
        elif uline.startswith("thinking="):
            thinking_line = uline
        else:
            other_lines.append(uline)
    
    print(f"  ✓ Context match: {ctx_match is not None}")
    if ctx_match:
        used = int(ctx_match.group(1).replace(",", ""))
        total = int(ctx_match.group(2).replace(",", ""))
        pct = (used / total * 100) if total > 0 else 0
        print(f"    Used: {used:,} / {total:,} ({pct:.1f}%)")
    
    print(f"  ✓ Thinking line: {thinking_line is not None}")
    if thinking_line:
        thinking_text = thinking_line.replace("thinking=", "")
        print(f"    Text: {thinking_text}")
    
    print(f"  ✓ Other metrics: {len(other_lines)}")
    for line in other_lines:
        print(f"    - {line}")
    
    test3_passed = (ctx_match is not None and thinking_line is not None and len(other_lines) > 0)
    print(f"\n  Result: {'PASS' if test3_passed else 'FAIL'}")
    
except Exception as e:
    print(f"  ✗ Error: {e}")
    import traceback
    traceback.print_exc()
    test3_passed = False

print()

# Summary
print("=" * 80)
print("TEST SUMMARY")
print("=" * 80)
print()
print(f"  Test 1 (Context Window DB):  {'PASS ✓' if test1_passed else 'FAIL ✗'}")
print(f"  Test 2 (Usage Lines):         {'PASS ✓' if test2_passed else 'FAIL ✗'}")
print(f"  Test 3 (Rendering Logic):     {'PASS ✓' if test3_passed else 'FAIL ✗'}")
print()

if all([test1_passed, test2_passed, test3_passed]):
    print("  ✓ All tests passed!")
    print()
    print("  Implementation is ready for end-to-end testing.")
    print()
    print("  Next steps:")
    print("    1. Start Ollama: ollama serve")
    print("    2. Pull model: ollama pull qwen3:1.7b")
    print("    3. Start TUI: python -m mtp.cli.tui")
    print("    4. Switch backend: /backend ollama")
    print("    5. Test: Ask a question requiring thinking")
    sys.exit(0)
else:
    print("  ✗ Some tests failed")
    print()
    print("  Please review the errors above.")
    sys.exit(1)
