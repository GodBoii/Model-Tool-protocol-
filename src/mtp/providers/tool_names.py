"""Deterministic, collision-checked names for providers with restricted identifiers."""

from __future__ import annotations

import hashlib
import re


class ToolNameMap:
    def __init__(self, max_length: int = 128) -> None:
        self.max_length = max_length
        self._original_by_wire: dict[str, str] = {}

    def wire_name(self, original: str) -> str:
        if (
            re.fullmatch(r"[a-zA-Z0-9_-]+", original)
            and len(original) <= self.max_length
            and not original.startswith("mtp_")
        ):
            wire = original
        else:
            digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:24]
            stem = re.sub(r"[^a-zA-Z0-9_-]", "_", original)[: self.max_length - 29]
            wire = f"mtp_{stem}_{digest}"
        existing = self._original_by_wire.get(wire)
        if existing is not None and existing != original:
            raise ValueError(
                f"Provider tool name collision for {original!r} and {existing!r}"
            )
        self._original_by_wire[wire] = original
        return wire

    def original_name(self, wire: str) -> str:
        return self._original_by_wire.get(wire, wire)
