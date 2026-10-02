"""Opt-in Groq async streaming and session audit using synthetic data only.

Run: python examples/audit_groq_sdk.py --output tmp/groq-sdk-audit.json
The token budget is added by this audit subclass, not the shipped Groq adapter.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from audit_groq_protocol import CASES, ObservedGroq, make_registry

from mtp.agent import Agent
from mtp.config import load_dotenv_if_available
from mtp.session_store import JsonSessionStore


class BudgetedGroq(ObservedGroq):
    def __init__(self, *, max_completion_tokens: int = 256, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.max_completion_tokens = max_completion_tokens

    def _create_completion(self, request_args: dict[str, Any]) -> Any:
        return super()._create_completion(
            {
                **request_args,
                "max_completion_tokens": self.max_completion_tokens,
            }
        )


async def events_probe(model: str) -> dict[str, Any]:
    timeline: list[dict[str, Any]] = []
    provider = BudgetedGroq(model=model, strict_dependency_mode=True)
    try:
        agent = Agent(
            provider=provider,
            tools=make_registry(timeline),
            strict_dependency_mode=True,
        )
        events = [
            event
            async for event in agent.arun_loop_events(
                CASES["parallel"][0],
                max_rounds=1,
                stream_final=True,
                stream_tool_events=True,
                stream_tool_results=True,
            )
        ]
        contiguous = [e["sequence"] for e in events] == list(range(1, len(events) + 1))
        outputs = [e["output"] for e in timeline if e["phase"] == "end"]
        types = [e["type"] for e in events]
        return {
            "events": events,
            "timeline": timeline,
            "sequences_contiguous": contiguous,
            "tool_outputs": outputs,
            "event_types": types,
            "stream_usage": provider._last_stream_usage,
            "passed": contiguous
            and outputs == [42, 42, 91]
            and "text_chunk" in types
            and "run_completed" in types,
        }
    finally:
        provider._client.close()


def session_probe(model: str, store_dir: Path) -> dict[str, Any]:
    provider = BudgetedGroq(model=model)
    try:
        store = JsonSessionStore(db_path=store_dir)
        session_id = "audit-live-sdk"
        Agent(
            provider=provider, tools=make_registry([]), session_store=store
        ).run_output(
            "Remember this synthetic audit code: LYRA-271. Reply only acknowledged.",
            max_rounds=1,
            session_id=session_id,
            user_id="audit-owner",
        )
        restored = Agent(
            provider=provider, tools=make_registry([]), session_store=store
        )
        reply = restored.run_output(
            "What is the synthetic audit code I gave you?",
            max_rounds=1,
            session_id=session_id,
            user_id="audit-owner",
        )
        remembered = "LYRA-271" in reply.final_text
        scoped = store.get_session(session_id, user_id="other-user") is None
        return {
            "final_text": reply.final_text,
            "remembered": remembered,
            "other_user_cannot_read": scoped,
            "passed": remembered and scoped,
        }
    finally:
        provider._client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen/qwen3.8-27b")
    parser.add_argument("--output", type=Path, default=Path("tmp/groq-sdk-audit.json"))
    args = parser.parse_args()
    load_dotenv_if_available(str(Path(__file__).resolve().parents[1] / ".env"))
    evidence: dict[str, Any] = {}
    try:
        evidence["async_events"] = asyncio.run(events_probe(args.model))
        evidence["session"] = session_probe(
            args.model, args.output.parent / "groq-audit-sessions"
        )
    except Exception as exc:  # noqa: BLE001 - record errors without saving sensitive SDK bodies
        evidence["error"] = {
            "type": type(exc).__name__,
            "cause_type": type(exc.__cause__).__name__ if exc.__cause__ else None,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, indent=2, default=str), encoding="utf-8"
    )
    passed = "error" not in evidence and all(
        result.get("passed") for result in evidence.values()
    )
    print(json.dumps({"passed": passed, "error": evidence.get("error")}), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
