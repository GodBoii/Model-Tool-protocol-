"""Opt-in live Groq audit with synthetic tools and recorded execution evidence.

Run: python examples/audit_groq_protocol.py --output tmp/groq-audit.json
No workspace files or credentials are included in provider prompts.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mtp.agent import Agent
from mtp.config import load_dotenv_if_available
from mtp.protocol import ToolSpec
from mtp.providers.groq_provider import GroqToolCallingProvider
from mtp.runtime import ToolRegistry


class ObservedGroq(GroqToolCallingProvider):
    """Record derived plans without recording credentials or request headers."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.actions: list[dict[str, Any]] = []

    def next_action(self, messages: list[dict[str, Any]], tools: list[ToolSpec]):
        action = super().next_action(messages, tools)
        self.actions.append({
            "plan": asdict(action.plan) if action.plan else None,
            "usage": action.metadata.get("usage"),
            "response_text": action.response_text,
        })
        return action


def make_registry(timeline: list[dict[str, Any]]) -> ToolRegistry:
    registry = ToolRegistry()
    value_schema = {
        "anyOf": [
            {"type": "number"},
            {"type": "object", "properties": {"$ref": {"type": ["string", "integer"]}},
             "required": ["$ref"], "additionalProperties": False},
        ]
    }

    async def calculate(a: float, b: float, operation: str) -> float:
        started = time.monotonic()
        timeline.append({"phase": "start", "operation": operation, "a": a, "b": b, "at": started})
        await asyncio.sleep(0.15)
        if operation == "add":
            value = a + b
        elif operation == "multiply":
            value = a * b
        elif operation == "subtract":
            value = a - b
        elif operation == "divide":
            value = a / b
        else:
            raise ValueError("Unsupported operation")
        timeline.append({"phase": "end", "operation": operation, "output": value, "at": time.monotonic()})
        return value

    registry.register_tool(ToolSpec(
        name="audit_calculate",
        description="Perform arithmetic. Use $ref objects for inputs derived from preceding calls.",
        input_schema={"type": "object", "properties": {
            "a": value_schema, "b": value_schema,
            "operation": {"type": "string", "enum": ["add", "subtract", "multiply", "divide"]},
        }, "required": ["a", "b", "operation"], "additionalProperties": False},
    ), calculate)
    return registry


CASES = {
    "parallel": (
        "Use audit_calculate to calculate 17+25, 6*7, and 100-9. These are independent. "
        "Return all THREE native tool calls together in ONE response, then report the results.",
        [42, 42, 91],
    ),
    "sequential_refs": (
        "Use audit_calculate to compute ((18-6)*4)/3+15. Return all FOUR native calls "
        "in ONE response. Later calls must use JSON $ref objects referencing the zero-based "
        "index of the preceding call for their dependent input. Do not calculate intermediate "
        "values yourself. The call order is subtract, multiply, divide, add.",
        [12, 48, 16, 31],
    ),
    "mixed": (
        "Use audit_calculate. In ONE response issue 3 calls: first add 17+25, second "
        "subtract 100-9 independently, third multiply the first result by 2 using "
        "{\"$ref\":0} for a and 2 for b. Do not hardcode the first result.",
        [42, 91, 84],
    ),
}


def overlapping(timeline: list[dict[str, Any]]) -> bool:
    active = 0
    for event in timeline:
        active += 1 if event["phase"] == "start" else -1
        if active > 1:
            return True
    return False


def run_case(model: str, name: str) -> dict[str, Any]:
    timeline: list[dict[str, Any]] = []
    provider = ObservedGroq(model=model, strict_dependency_mode=True)
    agent = Agent(provider=provider, tools=make_registry(timeline), strict_dependency_mode=True)
    prompt, expected = CASES[name]
    started = time.monotonic()
    evidence: dict[str, Any] = {"model": model, "case": name, "expected_outputs": expected}
    try:
        output = agent.run_output(prompt, max_rounds=3)
        first_plan = next((a["plan"] for a in provider.actions if a["plan"]), None)
        outputs = [r.output for r in output.tool_results]
        modes = [batch["mode"] for batch in first_plan["batches"]] if first_plan else []
        count = sum(len(batch["calls"]) for batch in first_plan["batches"]) if first_plan else 0
        expected_modes = {"parallel": ["parallel"], "sequential_refs": ["sequential"] * 4,
                          "mixed": ["parallel", "sequential"]}[name]
        evidence.update({
            "final_text": output.final_text, "tool_results": [asdict(r) for r in output.tool_results],
            "actions": provider.actions, "timeline": timeline,
            "observed_overlap": overlapping(timeline), "first_plan_modes": modes,
            "first_plan_call_count": count, "outputs_match": outputs == expected,
            "single_response_batch_verified": count == len(expected) and modes == expected_modes,
            "passed": outputs == expected and count == len(expected) and modes == expected_modes
                      and all(r.success for r in output.tool_results),
        })
    except Exception as exc:
        # Do not save exception response bodies, which can contain credentials or headers.
        evidence.update({"passed": False, "error_type": type(exc).__name__,
                         "cause_type": type(exc.__cause__).__name__ if exc.__cause__ else None,
                         "actions": provider.actions, "timeline": timeline})
    finally:
        close = getattr(provider._client, "close", None)
        if callable(close):
            close()
    evidence["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("tmp/groq-audit.json"))
    parser.add_argument("--models", nargs="+", default=["qwen/qwen3.8-27b", "openai/gpt-oss-120b"])
    parser.add_argument("--cases", nargs="+", choices=list(CASES), default=list(CASES))
    args = parser.parse_args()
    load_dotenv_if_available(str(Path(__file__).resolve().parents[1] / ".env"))
    if not os.environ.get("GROQ_API_KEY"):
        parser.error("GROQ_API_KEY is required")
    results: list[dict[str, Any]] = []
    for model in args.models:
        for case in args.cases:
            result = run_case(model, case)
            results.append(result)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
            print(json.dumps({k: result.get(k) for k in (
                "model", "case", "passed", "first_plan_modes", "observed_overlap", "error_type")}), flush=True)
            time.sleep(2)
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
