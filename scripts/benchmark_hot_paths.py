"""Repeatable, lightweight performance checks for MTP's interactive hot paths.

This is intentionally an opt-in benchmark rather than a default pytest test:
wall-clock assertions are useful regression tripwires on a developer machine,
but are too sensitive to noisy shared CI runners. Run it from the repository
root with ``python scripts/benchmark_hot_paths.py``.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import gc
import json
from pathlib import Path
import sys
import tempfile
import time
import tracemalloc
from typing import Callable, Iterator


# Make the source checkout importable without requiring an editable install.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from textual.app import App, ComposeResult  # noqa: E402

from mtp.cli.tui_limits import append_bounded, trim_display_blocks  # noqa: E402
from mtp.cli.tui_widgets.chat_log import (  # noqa: E402
    AssistantMessageWidget,
    ChatMessage,
)
from mtp.cli.workspace_file_index import WorkspaceFileIndex  # noqa: E402
from mtp.protocol import ExecutionPlan, ToolBatch, ToolCall, ToolSpec  # noqa: E402
from mtp.runtime import ToolRegistry  # noqa: E402


MiB = 1024 * 1024


@dataclass(frozen=True, slots=True)
class Budget:
    seconds: float
    peak_mib: float
    retained_mib: float


@dataclass(frozen=True, slots=True)
class Measurement:
    seconds: float
    peak_mib: float
    retained_mib: float
    rss_delta_mib: float | None


# Budgets are regression guardrails, not competitive targets. They are
# deliberately generous enough for supported Python versions and ordinary
# laptops while still catching an accidental unbounded scan or full widget
# rebuild on every streaming update.
BUDGETS = {
    "tui_streaming": Budget(seconds=5.0, peak_mib=24.0, retained_mib=8.0),
    "workspace_index": Budget(seconds=2.0, peak_mib=8.0, retained_mib=4.0),
    "runtime_parallel_cache": Budget(seconds=4.0, peak_mib=16.0, retained_mib=8.0),
    "transient_histories": Budget(seconds=3.0, peak_mib=4.0, retained_mib=2.0),
}


def _rss_bytes() -> int | None:
    """Return current RSS when the optional psutil package is available."""
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        return None
    return int(psutil.Process().memory_info().rss)


@contextmanager
def measured() -> Iterator[Callable[[], Measurement]]:
    """Measure a block and expose its result after the block exits."""
    gc.collect()
    rss_before = _rss_bytes()
    tracemalloc.start()
    started = time.perf_counter()
    result: Measurement | None = None

    def finish() -> Measurement:
        if result is None:
            raise RuntimeError("measurement is only available after the block exits")
        return result

    try:
        yield finish
    finally:
        elapsed = time.perf_counter() - started
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rss_after = _rss_bytes()
        rss_delta = None
        if rss_before is not None and rss_after is not None:
            rss_delta = max(0, rss_after - rss_before) / MiB
        result = Measurement(
            seconds=elapsed,
            peak_mib=peak / MiB,
            retained_mib=current / MiB,
            rss_delta_mib=rss_delta,
        )


class _StreamApp(App[None]):
    def compose(self) -> ComposeResult:
        yield AssistantMessageWidget(
            ChatMessage(
                "assistant",
                "",
                is_live=True,
                assistant_blocks=[{"type": "text", "text": ""}],
            )
        )


async def _update_stream() -> None:
    app = _StreamApp()
    async with app.run_test(size=(100, 30)) as pilot:
        widget = app.query_one(AssistantMessageWidget)
        text = ""
        original = widget._block_widgets[0]
        for index in range(600):
            text += f" token-{index:04d}"
            widget.update_message(
                ChatMessage(
                    "assistant",
                    "",
                    is_live=True,
                    assistant_blocks=[{"type": "text", "text": text}],
                )
            )
        await pilot.pause()
        if widget._block_widgets[0] is not original:
            raise AssertionError("streaming replaced its existing text widget")


def benchmark_tui_streaming() -> Measurement:
    with measured() as result:
        asyncio.run(_update_stream())
    return result()


def benchmark_workspace_index() -> Measurement:
    with tempfile.TemporaryDirectory(prefix="mtp-index-bench-") as raw_root:
        root = Path(raw_root)
        for directory_number in range(25):
            directory = root / f"package_{directory_number:02d}"
            directory.mkdir()
            for file_number in range(100):
                (directory / f"module_{file_number:03d}.py").touch()

        index = WorkspaceFileIndex(
            cache_ttl=60,
            max_files=3_000,
            max_depth=2,
            scan_time_budget=5.0,
        )
        with measured() as result:
            files = index.refresh(root)
            for _ in range(300):
                index.suggest(root, "module_099", limit=20)
        if len(files) != 2_500:
            raise AssertionError(f"expected 2500 indexed files, got {len(files)}")
        return result()


async def _exercise_runtime() -> None:
    registry = ToolRegistry(max_concurrency=16, max_cache_entries=256)

    async def identity(value: int) -> dict[str, int]:
        await asyncio.sleep(0)
        return {"value": value}

    registry.register_tool(
        ToolSpec(
            name="bench.identity",
            description="benchmark identity tool",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            cache_ttl_seconds=60,
        ),
        identity,
    )
    calls = [
        ToolCall(id=f"call-{index}", name="bench.identity", arguments={"value": index})
        for index in range(256)
    ]
    plan = ExecutionPlan(batches=[ToolBatch(mode="parallel", calls=calls)])
    first = await registry.execute_plan(plan)
    second = await registry.execute_plan(plan)
    if len(first) != 256 or not all(result.success for result in first):
        raise AssertionError("parallel runtime benchmark did not complete")
    if not all(result.cached for result in second):
        raise AssertionError("second runtime pass did not use the cache")


def benchmark_runtime_parallel_cache() -> Measurement:
    with measured() as result:
        asyncio.run(_exercise_runtime())
    return result()


def benchmark_transient_histories() -> Measurement:
    history: list[str] = []
    blocks: list[dict[str, str]] = []
    with measured() as result:
        for index in range(100_000):
            append_bounded(history, f"command {index}", 200)
        # Thousands of independently appended blocks plus 100k commands model
        # a long-lived session while keeping this diagnostic lightweight under
        # tracemalloc (which intentionally magnifies allocation overhead).
        for index in range(3_000):
            append_bounded(
                blocks,
                {"type": "text", "text": f"event {index} " + ("x" * 80)},
                300,
            )
            trim_display_blocks(blocks, max_blocks=300, max_chars=20_000)
    if len(history) != 200 or len(blocks) > 300:
        raise AssertionError("transient collections exceeded their configured bounds")
    return result()


BENCHMARKS: dict[str, Callable[[], Measurement]] = {
    "tui_streaming": benchmark_tui_streaming,
    "workspace_index": benchmark_workspace_index,
    "runtime_parallel_cache": benchmark_runtime_parallel_cache,
    "transient_histories": benchmark_transient_histories,
}


def _failures(results: dict[str, Measurement]) -> list[str]:
    failures: list[str] = []
    for name, measurement in results.items():
        budget = BUDGETS[name]
        for metric in ("seconds", "peak_mib", "retained_mib"):
            actual = getattr(measurement, metric)
            limit = getattr(budget, metric)
            if actual > limit:
                failures.append(f"{name}.{metric}: {actual:.3f} > {limit:.3f}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "names",
        nargs="*",
        choices=tuple(BENCHMARKS),
        help="benchmarks to run (default: all)",
    )
    parser.add_argument(
        "--no-enforce",
        action="store_true",
        help="report measurements without failing regression budgets",
    )
    args = parser.parse_args()
    selected = args.names or list(BENCHMARKS)
    results: dict[str, Measurement] = {}
    for name in selected:
        results[name] = BENCHMARKS[name]()

    payload = {
        "results": {name: asdict(result) for name, result in results.items()},
        "budgets": {name: asdict(BUDGETS[name]) for name in selected},
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    failures = [] if args.no_enforce else _failures(results)
    if failures:
        print("Performance budget failures:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
