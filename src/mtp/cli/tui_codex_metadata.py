"""Read-only Codex app-server metadata and model catalog."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import threading
import time
from typing import Any


@dataclass(frozen=True, slots=True)
class CodexModel:
    model: str
    display_name: str
    description: str
    efforts: tuple[str, ...]
    default_effort: str
    is_default: bool = False
    context_window: int | None = None


# Used only before discovery or when neither app-server nor its cache is available.
FALLBACK_MODELS = (
    CodexModel("gpt-6.1-sol", "GPT-6.1 Sol", "Coding and everyday work", ("low", "medium", "high", "xhigh", "max", "ultra"), "low", True),
    CodexModel("gpt-6-astra", "GPT-6 Astra", "Complex reasoning", ("low", "medium", "high", "xhigh", "max", "ultra"), "low"),
    CodexModel("gpt-6-sol", "GPT-6 Sol", "General coding", ("low", "medium", "high", "xhigh", "max", "ultra"), "medium"),
    CodexModel("gpt-6-luna", "GPT-6 Luna", "Faster tasks", ("low", "medium", "high", "xhigh", "max"), "medium"),
)
_models: tuple[CodexModel, ...] = ()
_model_lock = threading.Lock()


def get_codex_models() -> tuple[CodexModel, ...]:
    """Return in-memory picker metadata without disk or network I/O."""
    with _model_lock:
        return _models or FALLBACK_MODELS


def get_codex_model(model: str | None) -> CodexModel | None:
    models = get_codex_models()
    if model:
        return next((item for item in models if item.model == model), None)
    return next((item for item in models if item.is_default), models[0])


def parse_models(rows: Any) -> tuple[CodexModel, ...]:
    """Accept app-server rows and local-cache rows, ignoring hidden entries."""
    if not isinstance(rows, list):
        return ()
    models = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("hidden") or row.get("visibility") in {"hide", "hidden"}:
            continue
        name = row.get("model") or row.get("slug") or row.get("id")
        if not isinstance(name, str) or not name or name in seen:
            continue
        raw_efforts = row.get("supportedReasoningEfforts", row.get("supported_reasoning_levels", []))
        if not isinstance(raw_efforts, list):
            continue
        efforts = tuple(dict.fromkeys(
            value for item in raw_efforts if isinstance(item, dict)
            if isinstance(value := item.get("reasoningEffort", item.get("effort")), str) and value
        ))
        default = row.get("defaultReasoningEffort", row.get("default_reasoning_level", "medium"))
        if not efforts:
            efforts = (default,) if isinstance(default, str) and default else ("medium",)
        if default not in efforts:
            default = efforts[0]
        context = row.get("contextWindow", row.get("context_window"))
        models.append(CodexModel(
            model=name,
            display_name=str(row.get("displayName") or row.get("display_name") or name),
            description=str(row.get("description") or ""),
            efforts=efforts, default_effort=default,
            is_default=bool(row.get("isDefault", False)),
            context_window=context if isinstance(context, int) and context > 0 else None,
        ))
        seen.add(name)
    return tuple(models)


class CodexMetadataError(RuntimeError):
    pass


class CodexMetadataClient:
    """Short-lived stdio RPC client. Never starts inference or changes auth."""

    def __init__(self, codex_bin: str, *, timeout: float = 12) -> None:
        self.timeout = timeout
        self._next_id = 0
        self._responses: Queue[dict[str, Any] | None] = Queue()
        self._proc = subprocess.Popen(
            [codex_bin, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        self._reader = threading.Thread(target=self._read, name="mtp-codex-metadata", daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self._proc.stdout is not None
        try:
            for line in self._proc.stdout:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    self._responses.put(data)
        finally:
            self._responses.put(None)

    def _send(self, message: dict[str, Any]) -> None:
        assert self._proc.stdin is not None
        try:
            self._proc.stdin.write(json.dumps(message) + "\n")
            self._proc.stdin.flush()
        except OSError as exc:
            raise CodexMetadataError("Codex metadata connection closed") from exc

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        self._send({"id": request_id, "method": method, "params": params or {}})
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                response = self._responses.get(timeout=max(deadline - time.monotonic(), 0))
            except Empty as exc:
                raise CodexMetadataError(f"Codex {method} timed out") from exc
            if response is None:
                raise CodexMetadataError(f"Codex exited during {method}")
            if response.get("id") != request_id:
                # Notifications are irrelevant. Explicitly reject server requests.
                if "method" in response and "id" in response:
                    self._send({"id": response["id"], "error": {"code": -32601, "message": "Read-only metadata client"}})
                continue
            if "error" in response:
                # Do not expose raw server errors, which may contain auth data.
                raise CodexMetadataError(f"Codex {method} unavailable")
            result = response.get("result")
            if not isinstance(result, dict):
                raise CodexMetadataError(f"Invalid Codex {method} response")
            return result

    def __enter__(self) -> CodexMetadataClient:
        try:
            self.request("initialize", {"clientInfo": {"name": "mtp_tui", "version": "1.0"}})
            self._send({"method": "initialized", "params": {}})
        except BaseException:
            self.close()
            raise
        return self

    def close(self) -> None:
        from .tui_codex_backend import _kill_process_tree

        _kill_process_tree(self._proc)
        try:
            self._proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=3)
        self._reader.join(timeout=1)
        for stream in (self._proc.stdin, self._proc.stdout):
            if stream is not None:
                stream.close()

    def __exit__(self, *args: Any) -> None:
        self.close()


def refresh_codex_models(codex_bin: str) -> str:
    """Discover models off the UI thread, falling back to Codex's local cache."""
    global _models
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        cache = json.loads((home / "models_cache.json").read_text(encoding="utf-8"))
        cached = parse_models(cache.get("models")) if isinstance(cache, dict) else ()
    except (OSError, ValueError):
        cached = ()
    try:
        with CodexMetadataClient(codex_bin) as client:
            rows = []
            cursor = None
            seen_cursors = set()
            while True:
                page = client.request("model/list", {"limit": 100, "includeHidden": False, "cursor": cursor})
                data = page.get("data")
                if not isinstance(data, list):
                    raise CodexMetadataError("Invalid Codex model catalog")
                rows.extend(data)
                cursor = page.get("nextCursor")
                if cursor is None:
                    break
                if not isinstance(cursor, str) or cursor in seen_cursors:
                    raise CodexMetadataError("Invalid Codex model pagination")
                seen_cursors.add(cursor)
            models = parse_models(rows)
            if not models:
                raise CodexMetadataError("Codex model catalog is empty")
            cached_windows = {item.model: item.context_window for item in cached}
            models = tuple(replace(item, context_window=item.context_window or cached_windows.get(item.model)) for item in models)
        source = "Codex app-server"
    except (OSError, CodexMetadataError, subprocess.SubprocessError):
        models = cached
        source = "Codex cached catalog" if models else "Fallback catalog; /codex models retries discovery"
    if models:
        with _model_lock:
            _models = models
    return source


def read_codex_account(codex_bin: str) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    with CodexMetadataClient(codex_bin) as client:
        account = client.request("account/read", {"refreshToken": False})
        details = account.get("account")
        if not isinstance(details, dict) or details.get("type") not in {"chatgpt", "chatgptAuthTokens"}:
            return account, {}, None
        try:
            limits = client.request("account/rateLimits/read")
        except CodexMetadataError as exc:
            return account, {}, str(exc)
        return account, limits, None
