"""Opt-in cloud certification using synthetic tools and configured credentials.

No keys, HTTP headers, or SDK exception bodies are written to the report.
Run: python examples/certify_configured_providers.py --output tmp/live-certification.json
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from mtp import Agent, ToolRegistry, ToolSpec, load_dotenv_if_available
from mtp.providers.defaults import DEFAULT_PROVIDER_MODELS


def certify(name: str) -> dict[str, Any]:
    from openai import OpenAI

    from mtp import providers

    keys = {
        "groq": "GROQ_API_KEY",
        "openai_responses": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "xiaomi": "MIMO_API_KEY",
    }
    key = os.getenv(keys[name])
    result: dict[str, Any] = {"provider": name, "model": DEFAULT_PROVIDER_MODELS[name]}
    if not key:
        return {**result, "status": "blocked", "reason": "credential_not_configured"}
    provider = None
    try:
        if name == "groq":
            from groq import Groq

            provider = providers.Groq(
                model=result["model"],
                client=Groq(api_key=key, timeout=30, max_retries=0),
                max_completion_tokens=512,
            )
        elif name == "openai_responses":
            provider = providers.OpenAIResponses(
                model=result["model"],
                client=OpenAI(api_key=key, timeout=30, max_retries=0),
                max_output_tokens=256,
            )
        elif name == "gemini":
            from google import genai

            provider = providers.Gemini(
                model=result["model"],
                client=genai.Client(api_key=key, http_options={"timeout": 30000}),
            )
        elif name == "openrouter":
            provider = providers.OpenAICompatible(
                model=result["model"],
                provider_name=name,
                base_url="https://openrouter.ai/api/v1",
                api_key=key,
                max_tokens=256,
                client=OpenAI(
                    api_key=key,
                    base_url="https://openrouter.ai/api/v1",
                    timeout=30,
                    max_retries=0,
                ),
            )
        else:
            endpoint = "https://token-plan-ams.xiaomimimo.com/v1"
            provider = providers.Xiaomi(
                model=result["model"],
                client=OpenAI(
                    api_key=key, base_url=endpoint, timeout=30, max_retries=0
                ),
            )
        registry = ToolRegistry()
        registry.register_tool(
            ToolSpec(
                "calc_add",
                "Add two numbers",
                input_schema={
                    "type": "object",
                    "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                    "required": ["a", "b"],
                    "additionalProperties": False,
                },
            ),
            lambda a, b: a + b,
        )
        events = list(
            Agent(provider=provider, tools=registry).run_loop_events(
                "Use calc_add to add 17 and 25, then report the tool result.",
                max_rounds=2,
                stream_tool_results=True,
            )
        )
        calls = [event for event in events if event["type"] == "tool_finished"]
        passed = any(call.get("success") and call.get("output") == 42 for call in calls)
        result.update(
            status="passed" if passed else "failed",
            tool_calls=len(calls),
            final_event=events[-1]["type"] if events else None,
            outputs=[call.get("output") for call in calls],
        )
    except Exception as exc:  # noqa: BLE001
        status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        result.update(
            status="failed",
            error_type=type(exc).__name__,
            status_code=status_code if isinstance(status_code, (str, int)) else None,
        )
    finally:
        client = getattr(provider, "_client", None)
        close = getattr(client, "close", None)
        if callable(close):
            close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--providers",
        nargs="+",
        choices=["groq", "openai_responses", "gemini", "openrouter", "xiaomi"],
        default=["groq", "openai_responses", "gemini", "openrouter", "xiaomi"],
    )
    args = parser.parse_args()
    load_dotenv_if_available()
    results = []
    for name in args.providers:
        item = certify(name)
        print(json.dumps(item), flush=True)
        results.append(item)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
