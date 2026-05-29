from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import runpy
import sys
from typing import Iterable

from .doctor import run_doctor
from ..agent_os import launch as launch_agent_os
from ..codebase import CodebaseMemory
from .providers import get_provider, providers_as_rows
from .scaffold import VALID_TEMPLATES, scaffold_project
from .tui import run_tui


@contextmanager
def _pushd(path: Path):
    prev = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    if not rows:
        print("(none)")
        return
    widths = [len(header) for header in headers]
    for row in rows:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(value))
    line = "  ".join(h.ljust(widths[idx]) for idx, h in enumerate(headers))
    print(line)
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[idx]) for idx, value in enumerate(row)))


def _cmd_new(args: argparse.Namespace) -> int:
    base_dir = Path(args.dir)
    try:
        result = scaffold_project(
            name=args.name,
            template=args.template,
            base_dir=base_dir,
            force=bool(args.force),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to scaffold project: {exc}", file=sys.stderr)
        return 1

    print(f"Created project: {result.project_dir}")
    print("Files:")
    for file_path in result.written_files:
        print(f"- {file_path.relative_to(result.project_dir)}")
    print("\nNext steps:")
    print(f"1. cd {result.project_dir}")
    print("2. python -m venv .venv")
    print("3. .venv\\Scripts\\activate")
    print("4. pip install -e .[groq]")
    print("5. copy .env.example .env and set API keys")
    print("6. mtp run")
    return 0


def _resolve_entrypoint(path: Path, explicit: str | None) -> Path:
    if explicit:
        candidate = path / explicit
        if not candidate.exists():
            raise FileNotFoundError(f"Entry script not found: {candidate}")
        return candidate
    for default_name in ("app.py", "server.py", "main.py"):
        candidate = path / default_name
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError("Could not find an entry script. Expected app.py, server.py, or main.py.")


def _cmd_run(args: argparse.Namespace) -> int:
    project_path = Path(args.path)
    if not project_path.exists():
        print(f"Path does not exist: {project_path}", file=sys.stderr)
        return 1
    try:
        entry = _resolve_entrypoint(project_path, args.entry)
    except Exception as exc:  # noqa: BLE001
        print(f"Run failed: {exc}", file=sys.stderr)
        return 1

    print(f"Running {entry.name} in {project_path}")
    with _pushd(project_path):
        try:
            runpy.run_path(str(entry), run_name="__main__")
        except SystemExit as exc:
            if exc.code in (None, 0):
                return 0
            print(f"Entry script exited with code: {exc.code}", file=sys.stderr)
            return int(exc.code) if isinstance(exc.code, int) else 1
        except Exception as exc:  # noqa: BLE001
            print(f"Entry script crashed: {exc}", file=sys.stderr)
            return 1
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    provider_filter: set[str] | None = None
    if args.provider:
        provider_filter = set()
        for item in args.provider:
            info = get_provider(item)
            if info is None:
                print(f"Unknown provider: {item}", file=sys.stderr)
                return 1
            provider_filter.add(info.name)
            provider_filter.add(info.alias.lower())
    items = run_doctor(provider_filter=provider_filter)
    rows = [[row.name, row.status, row.detail] for row in items]
    _print_table(["check", "status", "detail"], rows)
    has_warn = any(row.status != "OK" for row in items)
    return 1 if has_warn else 0


def _cmd_providers_list(_args: argparse.Namespace) -> int:
    rows_data = providers_as_rows()
    rows = [
        [
            str(row["name"]),
            str(row["alias"]),
            str(row["class"]),
            str(row["sdk"]),
            str(row["sdk_status"]),
            str(row["env"]),
        ]
        for row in rows_data
    ]
    _print_table(["name", "alias", "class", "sdk", "sdk_status", "env"], rows)
    return 0


def _cmd_tui(args: argparse.Namespace) -> int:
    return int(run_tui(args))


def _cmd_agent_os(_args: argparse.Namespace) -> int:
    return int(launch_agent_os())


def _prompt_codebase_root(start: Path) -> Path:
    suggested = CodebaseMemory.discover_root(start)
    print(f"Current folder: {start.resolve()}")
    if suggested != start.resolve():
        print(f"Detected project root: {suggested}")
    raw = input("Press Enter to use this root, or enter another path: ").strip()
    root = Path(raw).expanduser() if raw else suggested
    root = root.resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Project root not found: {root}")
    return root


def _cmd_codebase_memory(args: argparse.Namespace) -> int:
    try:
        root = Path(args.path).expanduser().resolve() if args.path else _prompt_codebase_root(Path.cwd())
        memory = CodebaseMemory(root)
        if args.off:
            memory.set_enabled(False)
            print(f"Codebase memory OFF for {root}")
            return 0

        enabled = args.on
        if not args.on:
            choice = input("Codebase memory: [on/off]: ").strip().lower()
            if choice not in {"on", "off"}:
                print("Expected 'on' or 'off'.", file=sys.stderr)
                return 1
            enabled = choice == "on"
        if not enabled:
            memory.set_enabled(False)
            print(f"Codebase memory OFF for {root}")
            return 0

        last_percent = {"value": -1}

        def progress(stats) -> None:
            if stats.percent == last_percent["value"]:
                return
            last_percent["value"] = stats.percent
            print(
                f"\rScanning codebase... {stats.percent:3d}% "
                f"files={stats.files_seen} changed={stats.changed_files} skipped={stats.files_skipped}",
                end="",
                flush=True,
            )

        stats = memory.scan(enable=True, progress=progress)
        print()
        print(
            "Codebase memory scan complete: "
            f"100% files={stats.files_indexed} changed={stats.changed_files} "
            f"deleted={stats.files_deleted} chunks={stats.chunks_indexed}"
        )
        print(f"Saved memory database: {memory.db_path}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Codebase memory failed: {exc}", file=sys.stderr)
        return 1


def _cmd_codebase_status(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser().resolve() if args.path else CodebaseMemory.discover_root(Path.cwd())
    status = CodebaseMemory(root).status()
    rows = [
        ["root", str(status.root)],
        ["enabled", str(status.enabled)],
        ["db", str(status.db_path)],
        ["files", str(status.file_count)],
        ["chunks", str(status.chunk_count)],
        ["conversation_summaries", str(status.summary_count)],
        ["last_scan_at", status.last_scan_at or "(never)"],
    ]
    _print_table(["key", "value"], rows)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mtp",
        description="MTP command line tools: scaffold, run, diagnostics, provider introspection.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    new_cmd = sub.add_parser("new", help="Create a new MTP project from a template.")
    new_cmd.add_argument("name", help="Project directory name to create.")
    new_cmd.add_argument(
        "--template",
        choices=sorted(VALID_TEMPLATES),
        default="minimal",
        help="Scaffold template.",
    )
    new_cmd.add_argument("--dir", default=".", help="Base directory to create the project in.")
    new_cmd.add_argument("--force", action="store_true", help="Allow writing into an existing target directory.")
    new_cmd.set_defaults(handler=_cmd_new)

    run_cmd = sub.add_parser("run", help="Run a scaffolded project entry script.")
    run_cmd.add_argument("--path", default=".", help="Project directory.")
    run_cmd.add_argument("--entry", default=None, help="Explicit entry script path relative to --path.")
    run_cmd.set_defaults(handler=_cmd_run)

    doctor_cmd = sub.add_parser("doctor", help="Validate environment and provider setup.")
    doctor_cmd.add_argument(
        "--provider",
        action="append",
        default=[],
        help="Filter checks to one or more providers (repeatable).",
    )
    doctor_cmd.set_defaults(handler=_cmd_doctor)

    providers_cmd = sub.add_parser("providers", help="Provider metadata commands.")
    providers_sub = providers_cmd.add_subparsers(dest="providers_command", required=True)
    providers_list = providers_sub.add_parser("list", help="List known providers, SDK modules, and key env vars.")
    providers_list.set_defaults(handler=_cmd_providers_list)

    tui_cmd = sub.add_parser("tui", help="Launch interactive TUI for MTP + Codex bridge.")
    tui_cmd.add_argument(
        "--backend",
        choices=[
            "codex",
            "openai",
            "groq",
            "claude",
            "gemini",
            "openrouter",
            "mistral",
            "cohere",
            "sambanova",
            "cerebras",
            "deepseek",
            "togetherai",
            "fireworksai",
            "xiaomi",
            "ollama",
            "lmstudio",
        ],
        default="codex",
        help="Chat backend to use.",
    )
    tui_cmd.add_argument(
        "--codex-model",
        default="gpt-5.3-codex",
        help="Model for Codex backend.",
    )
    tui_cmd.add_argument("--openai-model", default="gpt-5.4-mini", help="Initial model for the OpenAI MTP backend.")
    tui_cmd.add_argument("--max-rounds", type=int, default=6, help="max_rounds for MTP SDK provider backends.")
    tui_cmd.add_argument("--cwd", default=".", help="Working directory used by tools and Codex backend.")
    tui_cmd.add_argument(
        "--session-db",
        default=str(Path.home() / ".mtp" / "sessions"),
        help="Directory used to persist TUI chat sessions (centralized storage).",
    )
    tui_cmd.add_argument(
        "--session-id",
        default=None,
        help="Optional existing session id to load on startup.",
    )
    tui_cmd.add_argument(
        "--reasoning-effort",
        choices=["none", "low", "medium", "high", "xhigh"],
        default="medium",
        help="Reasoning effort preference used by codex backend.",
    )
    tui_cmd.add_argument(
        "--mode",
        choices=["plan", "code", "debug", "review"],
        default="code",
        help="MTP harness mode for SDK providers.",
    )
    tui_cmd.add_argument("--autoresearch", action="store_true", help="Enable autoresearch for MTP SDK provider backends.")
    tui_cmd.add_argument(
        "--research-instructions",
        default=None,
        help="Custom research instructions for MTP SDK provider backends when autoresearch is enabled.",
    )
    tui_cmd.set_defaults(handler=_cmd_tui)

    agent_os_cmd = sub.add_parser("agent-os", help="Launch Streamlit Agent OS UI.")
    agent_os_cmd.set_defaults(handler=_cmd_agent_os)

    codebase_cmd = sub.add_parser("codebase", help="Codebase memory and indexing commands.")
    codebase_sub = codebase_cmd.add_subparsers(dest="codebase_command", required=True)
    memory_cmd = codebase_sub.add_parser("memory", help="Turn codebase memory on/off and scan the project.")
    memory_cmd.add_argument("--path", default=None, help="Project root to index. Defaults to detected root.")
    memory_state = memory_cmd.add_mutually_exclusive_group()
    memory_state.add_argument("--on", action="store_true", help="Enable memory and scan without prompting.")
    memory_state.add_argument("--off", action="store_true", help="Disable memory without scanning.")
    memory_cmd.set_defaults(handler=_cmd_codebase_memory)

    codebase_status = codebase_sub.add_parser("status", help="Show codebase memory status.")
    codebase_status.add_argument("--path", default=None, help="Project root. Defaults to detected root.")
    codebase_status.set_defaults(handler=_cmd_codebase_status)

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 1
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
