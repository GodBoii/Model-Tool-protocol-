from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from .session_store import SessionRecord


SESSION_EXPORT_SCHEMA = "https://json-schema.org/draft/2020-12/schema"
SESSION_EXPORT_FORMAT = "mtp.session-export"
SESSION_EXPORT_VERSION = 1
_SESSION_FIELDS = {
    "session_id", "user_id", "metadata", "messages", "runs", "created_at", "updated_at"
}
_RUN_FIELDS = {
    "run_id", "input", "final_text", "cancelled", "paused", "total_tool_calls", "created_at"
}


class SessionTransferError(ValueError):
    """Raised when a session transfer bundle does not match the supported schema."""


def make_session_export(sessions: list[SessionRecord]) -> dict[str, Any]:
    """Build the stable, versioned JSON envelope used for session transfers."""
    return {
        "$schema": SESSION_EXPORT_SCHEMA,
        "format": SESSION_EXPORT_FORMAT,
        "version": SESSION_EXPORT_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "sessions": [session.to_dict() for session in sessions],
    }


def parse_session_export(payload: Any) -> list[SessionRecord]:
    """Validate a v1 transfer payload before constructing any session records."""
    if not isinstance(payload, dict):
        raise SessionTransferError("Session export must be a JSON object")
    allowed_root = {"$schema", "format", "version", "exported_at", "sessions"}
    _reject_unknown(payload, allowed_root, "export")
    if payload.get("$schema") != SESSION_EXPORT_SCHEMA:
        raise SessionTransferError("Unsupported or missing session export $schema")
    if payload.get("format") != SESSION_EXPORT_FORMAT:
        raise SessionTransferError("Unsupported or missing session export format")
    if payload.get("version") != SESSION_EXPORT_VERSION or isinstance(payload.get("version"), bool):
        raise SessionTransferError(f"Unsupported session export version: {payload.get('version')!r}")
    _require_timestamp(payload.get("exported_at"), "export.exported_at")
    rows = payload.get("sessions")
    if not isinstance(rows, list):
        raise SessionTransferError("export.sessions must be an array")

    identities: set[tuple[str, str | None]] = set()
    records: list[SessionRecord] = []
    for index, row in enumerate(rows):
        path = f"export.sessions[{index}]"
        _validate_session(row, path)
        record = SessionRecord.from_dict(row)
        identity = (record.session_id, record.user_id)
        if identity in identities:
            raise SessionTransferError(
                f"{path} duplicates session identity {record.session_id!r}, user={record.user_id!r}"
            )
        identities.add(identity)
        records.append(record)
    return records


def load_session_export(path: str | Path) -> list[SessionRecord]:
    source = Path(path).expanduser()
    try:
        with source.open("r", encoding="utf-8") as handle:
            payload = json.load(handle, parse_constant=_reject_json_constant)
    except json.JSONDecodeError as exc:
        raise SessionTransferError(f"Invalid JSON in session export: {source}") from exc
    return parse_session_export(payload)


def write_session_export(path: str | Path, payload: dict[str, Any], *, overwrite: bool = False) -> Path:
    """Durably publish a JSON export, refusing replacement by default."""
    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        fd = os.open(str(temp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temp, destination)
        else:
            # A hard link publishes the fully-written inode and atomically fails
            # when the destination exists. It avoids the race in exists()+rename.
            try:
                os.link(temp, destination)
            except FileExistsError:
                raise FileExistsError(f"Export already exists: {destination}") from None
            except OSError as exc:
                raise OSError(
                    f"Cannot atomically create export on this filesystem: {destination}"
                ) from exc
            temp.unlink(missing_ok=True)
        try:
            os.chmod(destination, 0o600)
        except OSError:
            pass
        _fsync_parent(destination.parent)
        return destination
    finally:
        temp.unlink(missing_ok=True)


def _validate_session(value: Any, path: str) -> None:
    if not isinstance(value, dict):
        raise SessionTransferError(f"{path} must be an object")
    _reject_unknown(value, _SESSION_FIELDS, path)
    _require_string(value.get("session_id"), f"{path}.session_id")
    user_id = value.get("user_id")
    if user_id is not None and not isinstance(user_id, str):
        raise SessionTransferError(f"{path}.user_id must be a string or null")
    metadata = value.get("metadata", {})
    if not isinstance(metadata, dict):
        raise SessionTransferError(f"{path}.metadata must be an object")
    messages = value.get("messages", [])
    if not isinstance(messages, list) or any(not isinstance(item, dict) for item in messages):
        raise SessionTransferError(f"{path}.messages must be an array of objects")
    runs = value.get("runs", [])
    if not isinstance(runs, list):
        raise SessionTransferError(f"{path}.runs must be an array")
    for index, run in enumerate(runs):
        _validate_run(run, f"{path}.runs[{index}]")
    for field in ("created_at", "updated_at"):
        if field in value:
            _require_timestamp(value[field], f"{path}.{field}")


def _validate_run(value: Any, path: str) -> None:
    if not isinstance(value, dict):
        raise SessionTransferError(f"{path} must be an object")
    _reject_unknown(value, _RUN_FIELDS, path)
    for field in ("run_id", "input", "final_text"):
        _require_string(value.get(field), f"{path}.{field}", allow_empty=field != "run_id")
    for field in ("cancelled", "paused"):
        if field in value and not isinstance(value[field], bool):
            raise SessionTransferError(f"{path}.{field} must be a boolean")
    calls = value.get("total_tool_calls", 0)
    if isinstance(calls, bool) or not isinstance(calls, int) or calls < 0:
        raise SessionTransferError(f"{path}.total_tool_calls must be a non-negative integer")
    if "created_at" in value:
        _require_timestamp(value["created_at"], f"{path}.created_at")


def _require_string(value: Any, path: str, *, allow_empty: bool = False) -> None:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        suffix = "a string" if allow_empty else "a non-empty string"
        raise SessionTransferError(f"{path} must be {suffix}")


def _require_timestamp(value: Any, path: str) -> None:
    _require_string(value, path)
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SessionTransferError(f"{path} must be an ISO 8601 timestamp") from exc


def _reject_unknown(value: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise SessionTransferError(f"{path} contains unknown field: {unknown[0]}")


def _reject_json_constant(value: str) -> None:
    raise SessionTransferError(f"Non-finite JSON number is not allowed: {value}")


def _fsync_parent(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
