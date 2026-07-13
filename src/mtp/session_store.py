from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Protocol
from uuid import uuid4

from .media import coerce_audios, coerce_files, coerce_images, coerce_videos


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if is_dataclass(value):
        return _json_safe(asdict(value))
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return _json_safe(to_dict())
        except Exception:
            return str(value)
    return str(value)


def _restore_message_media(message: dict[str, Any]) -> dict[str, Any]:
    restored = dict(message)
    if "images" in restored:
        restored["images"] = coerce_images(restored.get("images"))
    if "audios" in restored:
        restored["audios"] = coerce_audios(restored.get("audios"))
    if "videos" in restored:
        restored["videos"] = coerce_videos(restored.get("videos"))
    if "files" in restored:
        restored["files"] = coerce_files(restored.get("files"))
    return restored


@dataclass(slots=True)
class SessionRun:
    run_id: str
    input: str
    final_text: str
    cancelled: bool = False
    paused: bool = False
    total_tool_calls: int = 0
    created_at: str = field(default_factory=_utc_now_iso)


@dataclass(slots=True)
class SessionRecord:
    session_id: str
    user_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, Any]] = field(default_factory=list)
    runs: list[SessionRun] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["messages"] = [_json_safe(message) for message in self.messages]
        payload["metadata"] = _json_safe(self.metadata)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionRecord:
        runs_data = data.get("runs") or []
        runs: list[SessionRun] = []
        for item in runs_data:
            if not isinstance(item, dict):
                continue
            runs.append(
                SessionRun(
                    run_id=str(item.get("run_id") or ""),
                    input=str(item.get("input") or ""),
                    final_text=str(item.get("final_text") or ""),
                    cancelled=bool(item.get("cancelled")),
                    paused=bool(item.get("paused")),
                    total_tool_calls=int(item.get("total_tool_calls") or 0),
                    created_at=str(item.get("created_at") or _utc_now_iso()),
                )
            )

        messages_data = data.get("messages") or []
        messages: list[dict[str, Any]] = []
        for item in messages_data:
            if isinstance(item, dict):
                messages.append(_restore_message_media(item))

        return cls(
            session_id=str(data.get("session_id") or ""),
            user_id=data.get("user_id"),
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
            messages=messages,
            runs=runs,
            created_at=str(data.get("created_at") or _utc_now_iso()),
            updated_at=str(data.get("updated_at") or _utc_now_iso()),
        )


class SessionStore(Protocol):
    def get_session(self, session_id: str, *, user_id: str | None = None) -> SessionRecord | None:
        ...

    def upsert_session(self, session: SessionRecord) -> SessionRecord:
        ...


class JsonSessionStore:
    def __init__(
        self,
        *,
        db_path: str | Path = "tmp/mtp_json_db",
        session_table: str = "mtp_sessions",
        lock_timeout_seconds: float = 10.0,
        stale_lock_seconds: float = 60.0,
    ) -> None:
        self.db_path = Path(db_path)
        # The table name becomes a filename, so reject separators and traversal.
        self.session_table = _validate_sql_identifier(session_table)
        self.lock_timeout_seconds = max(0.1, float(lock_timeout_seconds))
        self.stale_lock_seconds = max(self.lock_timeout_seconds, float(stale_lock_seconds))
        self._lock = threading.RLock()

    @property
    def file_path(self) -> Path:
        return self.db_path / f"{self.session_table}.json"

    @property
    def lock_path(self) -> Path:
        return self.db_path / f".{self.session_table}.lock"

    @contextmanager
    def _file_lock(self):
        self.db_path.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.lock_timeout_seconds
        fd: int | None = None
        lock_token = json.dumps({"pid": os.getpid(), "created_at": time.time(), "token": uuid4().hex})
        while fd is None:
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(fd, lock_token.encode("ascii"))
                os.fsync(fd)
            except FileExistsError as exc:
                if self._remove_stale_lock():
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for session store lock: {self.lock_path}") from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            if fd is not None:
                os.close(fd)
            # Only remove the lock we acquired. This avoids deleting a successor's
            # lock if an administrator recovered/replaced ours while it was held.
            try:
                if self.lock_path.read_text(encoding="ascii") == lock_token:
                    self.lock_path.unlink()
            except FileNotFoundError:
                pass

    def _remove_stale_lock(self) -> bool:
        """Best-effort recovery for abandoned locks without stealing live locks."""
        try:
            stat = self.lock_path.stat()
            if time.time() - stat.st_mtime < self.stale_lock_seconds:
                return False
            raw = self.lock_path.read_text(encoding="ascii")
            payload = json.loads(raw)
            pid = int(payload.get("pid", 0))
            if pid > 0 and _process_is_alive(pid):
                return False
            # Re-check identity after inspection to limit races with replacement.
            if self.lock_path.read_text(encoding="ascii") != raw:
                return False
            self.lock_path.unlink()
            return True
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            return False

    def _read_all(self) -> list[dict[str, Any]]:
        self.db_path.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            self._write_all([])
            return []
        raw = self.file_path.read_text(encoding="utf-8")
        if not raw.strip():
            return []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in session store: {self.file_path}") from exc
        if not isinstance(payload, list):
            raise ValueError(f"Session store payload must be a list: {self.file_path}")
        return [item for item in payload if isinstance(item, dict)]

    def _write_all(self, rows: list[dict[str, Any]]) -> None:
        self.db_path.mkdir(parents=True, exist_ok=True)
        tmp_path = self.file_path.with_name(f".{self.file_path.name}.{uuid4().hex}.tmp")
        try:
            fd = os.open(str(tmp_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(rows, handle, indent=2, ensure_ascii=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.file_path)
            try:
                os.chmod(self.file_path, 0o600)
            except OSError:
                pass
            _fsync_directory(self.db_path)
        finally:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass

    def get_session(self, session_id: str, *, user_id: str | None = None) -> SessionRecord | None:
        with self._lock:
            with self._file_lock():
                for row in self._read_all():
                    if row.get("session_id") != session_id:
                        continue
                    stored_user_id = row.get("user_id")
                    if not _session_user_matches(stored_user_id, user_id):
                        continue
                    return SessionRecord.from_dict(row)
        return None

    def upsert_session(self, session: SessionRecord) -> SessionRecord:
        with self._lock:
            with self._file_lock():
                rows = self._read_all()
                serialized = session.to_dict()
                serialized["updated_at"] = _utc_now_iso()
                if not serialized.get("created_at"):
                    serialized["created_at"] = serialized["updated_at"]

                for idx, row in enumerate(rows):
                    if row.get("session_id") == session.session_id and row.get("user_id") == session.user_id:
                        rows[idx] = serialized
                        break
                else:
                    rows.append(serialized)

                self._write_all(rows)
                return SessionRecord.from_dict(serialized)

    def list_sessions(
        self,
        *,
        user_id: str | None = None,
        limit: int | None = None,
    ) -> list[SessionRecord]:
        """Return newest sessions first, optionally scoped to one user."""
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")
        with self._lock:
            with self._file_lock():
                records = [
                    SessionRecord.from_dict(row)
                    for row in self._read_all()
                    if user_id is None or row.get("user_id") == user_id
                ]
        records.sort(key=lambda record: record.updated_at, reverse=True)
        return records if limit is None else records[:limit]

    def delete_session(self, session_id: str, *, user_id: str | None = None) -> bool:
        """Delete an exact session identity and report whether it existed."""
        with self._lock:
            with self._file_lock():
                rows = self._read_all()
                kept = [
                    row
                    for row in rows
                    if not (row.get("session_id") == session_id and row.get("user_id") == user_id)
                ]
                if len(kept) == len(rows):
                    return False
                self._write_all(kept)
                return True

    def import_sessions(
        self,
        sessions: list[SessionRecord],
        *,
        overwrite: bool = False,
    ) -> int:
        """Atomically import session records.

        Session identity is the ``(session_id, user_id)`` pair used by the JSON
        store. By default, any existing identity aborts the entire import. This
        method deliberately writes the database once so a validation/conflict
        failure cannot leave a partially imported bundle.
        """
        serialized: list[dict[str, Any]] = []
        incoming_identities: set[tuple[str, str | None]] = set()
        for session in sessions:
            if not isinstance(session, SessionRecord):
                raise TypeError("sessions must contain SessionRecord instances")
            if not session.session_id:
                raise ValueError("session_id must not be empty")
            identity = (session.session_id, session.user_id)
            if identity in incoming_identities:
                raise ValueError(
                    f"Duplicate session identity in import: {session.session_id!r}, user={session.user_id!r}"
                )
            incoming_identities.add(identity)
            serialized.append(session.to_dict())

        if not serialized:
            return 0

        with self._lock:
            with self._file_lock():
                rows = self._read_all()
                positions = {
                    (str(row.get("session_id") or ""), row.get("user_id")): idx
                    for idx, row in enumerate(rows)
                }
                conflicts = [identity for identity in incoming_identities if identity in positions]
                if conflicts and not overwrite:
                    session_id, user_id = sorted(conflicts, key=lambda item: (item[0], item[1] or ""))[0]
                    raise FileExistsError(
                        f"Session already exists: {session_id!r}, user={user_id!r}"
                    )

                for row in serialized:
                    identity = (str(row["session_id"]), row.get("user_id"))
                    position = positions.get(identity)
                    if position is None:
                        positions[identity] = len(rows)
                        rows.append(row)
                    else:
                        rows[position] = row
                self._write_all(rows)
        return len(serialized)


def _process_is_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _fsync_directory(path: Path) -> None:
    """Persist a rename on platforms that support opening directories."""
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


def _validate_sql_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"Invalid SQL identifier: {value!r}")
    return value


def _parse_json_blob(value: Any, *, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        if not value.strip():
            return fallback
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return fallback
        return parsed
    return fallback


def _session_user_matches(stored_user_id: Any, requested_user_id: str | None) -> bool:
    if stored_user_id is None:
        return True
    if requested_user_id is None:
        return False
    return str(stored_user_id) == requested_user_id


class PostgresSessionStore:
    def __init__(self, *, db_url: str, session_table: str = "mtp_sessions") -> None:
        self.db_url = db_url
        self.session_table = _validate_sql_identifier(session_table)
        self._lock = threading.RLock()
        try:
            import psycopg  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise ImportError("psycopg is required for PostgresSessionStore. Install with: pip install psycopg") from exc
        self._psycopg = psycopg
        self._ensure_schema()

    def _connect(self) -> Any:
        return self._psycopg.connect(self.db_url)

    def _ensure_schema(self) -> None:
        query = f"""
        CREATE TABLE IF NOT EXISTS {self.session_table} (
            session_id TEXT PRIMARY KEY,
            user_id TEXT NULL,
            metadata JSONB NOT NULL,
            messages JSONB NOT NULL,
            runs JSONB NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query)
            conn.commit()

    def _fetch_row(self, session_id: str) -> dict[str, Any] | None:
        query = (
            f"SELECT session_id, user_id, metadata, messages, runs, created_at, updated_at "
            f"FROM {self.session_table} WHERE session_id = %s"
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (session_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                keys = [item[0] for item in cur.description or []]
        if isinstance(row, dict):
            return row
        if isinstance(row, tuple):
            return dict(zip(keys, row, strict=False))
        return None

    def get_session(self, session_id: str, *, user_id: str | None = None) -> SessionRecord | None:
        with self._lock:
            row = self._fetch_row(session_id)
            if row is None:
                return None
            stored_user_id = row.get("user_id")
            if not _session_user_matches(stored_user_id, user_id):
                return None
            payload = {
                "session_id": row.get("session_id"),
                "user_id": stored_user_id,
                "metadata": _parse_json_blob(row.get("metadata"), fallback={}),
                "messages": _parse_json_blob(row.get("messages"), fallback=[]),
                "runs": _parse_json_blob(row.get("runs"), fallback=[]),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
            }
            return SessionRecord.from_dict(payload)

    def upsert_session(self, session: SessionRecord) -> SessionRecord:
        with self._lock:
            existing = self._fetch_row(session.session_id)
            serialized = session.to_dict()
            serialized["created_at"] = (existing or {}).get("created_at") or serialized.get("created_at") or _utc_now_iso()
            serialized["updated_at"] = _utc_now_iso()
            query = (
                f"INSERT INTO {self.session_table} "
                "(session_id, user_id, metadata, messages, runs, created_at, updated_at) "
                "VALUES (%s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s) "
                "ON CONFLICT (session_id) DO UPDATE SET "
                "user_id = EXCLUDED.user_id, "
                "metadata = EXCLUDED.metadata, "
                "messages = EXCLUDED.messages, "
                "runs = EXCLUDED.runs, "
                "created_at = EXCLUDED.created_at, "
                "updated_at = EXCLUDED.updated_at"
            )
            params = (
                serialized["session_id"],
                serialized.get("user_id"),
                json.dumps(serialized.get("metadata") or {}, ensure_ascii=True),
                json.dumps(serialized.get("messages") or [], ensure_ascii=True),
                json.dumps(serialized.get("runs") or [], ensure_ascii=True),
                serialized["created_at"],
                serialized["updated_at"],
            )
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                conn.commit()
            return SessionRecord.from_dict(serialized)


class MySQLSessionStore:
    def __init__(self, *, host: str, user: str, password: str, database: str, port: int = 3306, session_table: str = "mtp_sessions") -> None:
        self.host = host
        self.user = user
        self.password = password
        self.database = database
        self.port = port
        self.session_table = _validate_sql_identifier(session_table)
        self._lock = threading.RLock()
        self._driver = self._resolve_driver()
        self._ensure_schema()

    def _resolve_driver(self) -> str:
        try:
            import pymysql  # type: ignore
        except Exception:  # noqa: BLE001
            pymysql = None
        if pymysql is not None:
            self._pymysql = pymysql
            return "pymysql"
        try:
            import mysql.connector  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise ImportError(
                "MySQLSessionStore requires either pymysql or mysql-connector-python. "
                "Install with: pip install pymysql"
            ) from exc
        self._mysql_connector = mysql.connector
        return "mysql-connector"

    def _connect(self) -> Any:
        if self._driver == "pymysql":
            return self._pymysql.connect(  # type: ignore[attr-defined]
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                port=self.port,
                charset="utf8mb4",
                autocommit=False,
            )
        return self._mysql_connector.connect(  # type: ignore[attr-defined]
            host=self.host,
            user=self.user,
            password=self.password,
            database=self.database,
            port=self.port,
        )

    def _ensure_schema(self) -> None:
        query = f"""
        CREATE TABLE IF NOT EXISTS {self.session_table} (
            session_id VARCHAR(191) PRIMARY KEY,
            user_id VARCHAR(191) NULL,
            metadata LONGTEXT NOT NULL,
            messages LONGTEXT NOT NULL,
            runs LONGTEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
        conn = self._connect()
        try:
            cur = conn.cursor()
            try:
                cur.execute(query)
            finally:
                cur.close()
            conn.commit()
        finally:
            conn.close()

    def _fetch_row(self, session_id: str) -> dict[str, Any] | None:
        query = (
            f"SELECT session_id, user_id, metadata, messages, runs, created_at, updated_at "
            f"FROM {self.session_table} WHERE session_id = %s"
        )
        conn = self._connect()
        try:
            cur = conn.cursor()
            try:
                cur.execute(query, (session_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                keys = [item[0] for item in cur.description or []]
            finally:
                cur.close()
        finally:
            conn.close()
        if isinstance(row, dict):
            return row
        if isinstance(row, tuple):
            return dict(zip(keys, row, strict=False))
        return None

    def get_session(self, session_id: str, *, user_id: str | None = None) -> SessionRecord | None:
        with self._lock:
            row = self._fetch_row(session_id)
            if row is None:
                return None
            stored_user_id = row.get("user_id")
            if not _session_user_matches(stored_user_id, user_id):
                return None
            payload = {
                "session_id": row.get("session_id"),
                "user_id": stored_user_id,
                "metadata": _parse_json_blob(row.get("metadata"), fallback={}),
                "messages": _parse_json_blob(row.get("messages"), fallback=[]),
                "runs": _parse_json_blob(row.get("runs"), fallback=[]),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
            }
            return SessionRecord.from_dict(payload)

    def upsert_session(self, session: SessionRecord) -> SessionRecord:
        with self._lock:
            existing = self._fetch_row(session.session_id)
            serialized = session.to_dict()
            serialized["created_at"] = (existing or {}).get("created_at") or serialized.get("created_at") or _utc_now_iso()
            serialized["updated_at"] = _utc_now_iso()
            query = (
                f"INSERT INTO {self.session_table} "
                "(session_id, user_id, metadata, messages, runs, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE "
                "user_id = VALUES(user_id), "
                "metadata = VALUES(metadata), "
                "messages = VALUES(messages), "
                "runs = VALUES(runs), "
                "created_at = VALUES(created_at), "
                "updated_at = VALUES(updated_at)"
            )
            params = (
                serialized["session_id"],
                serialized.get("user_id"),
                json.dumps(serialized.get("metadata") or {}, ensure_ascii=True),
                json.dumps(serialized.get("messages") or [], ensure_ascii=True),
                json.dumps(serialized.get("runs") or [], ensure_ascii=True),
                serialized["created_at"],
                serialized["updated_at"],
            )
            conn = self._connect()
            try:
                cur = conn.cursor()
                try:
                    cur.execute(query, params)
                finally:
                    cur.close()
                conn.commit()
            finally:
                conn.close()
            return SessionRecord.from_dict(serialized)
