"""Concurrent newline-framed MCP with request-scoped stdio cancellation."""

from __future__ import annotations

import asyncio
import json
from typing import Any, TextIO

from .mcp_modern import is_modern_request


async def serve_modern_stdio(server: Any, reader: TextIO, writer: TextIO) -> None:
    pending: dict[tuple[type, str | int], asyncio.Task[None]] = {}
    cancelled: set[tuple[type, str | int]] = set()

    def write(response: dict[str, Any] | None) -> None:
        if response is not None:
            writer.write(json.dumps(response, ensure_ascii=True, default=str) + "\n")
            writer.flush()

    async def process(request: dict[str, Any], key: tuple[type, str | int]) -> None:
        try:
            response = await server.ahandle_request(request)
            if key not in cancelled:
                write(response)
        except asyncio.CancelledError:
            pass
        finally:
            pending.pop(key, None)
            cancelled.discard(key)

    try:
        while line := await asyncio.to_thread(reader.readline):
            try:
                request = json.loads(line)
            except (json.JSONDecodeError, UnicodeError):
                write(server._error_response(None, -32700, "Invalid JSON payload."))
                continue
            if not isinstance(request, dict):
                write(
                    server._error_response(
                        None, -32600, "Expected one JSON-RPC object."
                    )
                )
                continue
            if (
                request.get("method") == "notifications/cancelled"
                and "id" not in request
            ):
                params = request.get("params")
                request_id = (
                    params.get("requestId") if isinstance(params, dict) else None
                )
                if type(request_id) in (str, int):
                    key = (type(request_id), request_id)
                    task = pending.get(key)
                    if task is not None:
                        cancelled.add(key)
                        task.cancel()
                continue
            if not is_modern_request(request):
                # Legacy clients retain their serial initialization lifecycle.
                write(await server.ahandle_request(request))
                continue
            issue = server._modern_requests.validate(request)
            if issue is not None:
                if "id" in request:
                    write(issue)
                continue
            key = (type(request["id"]), request["id"])
            if key in pending:
                write(
                    server._error_response(
                        request["id"], -32600, "Request id is already in flight."
                    )
                )
                continue
            if len(pending) >= 256:
                write(
                    server._error_response(
                        request["id"], 1002, "Too many in-flight requests."
                    )
                )
                continue
            pending[key] = asyncio.create_task(process(request, key))
    finally:
        for key, task in list(pending.items()):
            cancelled.add(key)
            task.cancel()
        await asyncio.gather(*list(pending.values()), return_exceptions=True)
