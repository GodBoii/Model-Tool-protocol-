from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from .protocol import ExecutionPlan

CURRENT_MTP_VERSION = "0.1.0"


@dataclass(slots=True)
class MessageEnvelope:
    mtp_version: str
    kind: str
    payload: dict[str, Any]
    metadata: dict[str, Any]

    @classmethod
    def create(
        cls,
        kind: str,
        payload: dict[str, Any],
        *,
        mtp_version: str = CURRENT_MTP_VERSION,
        metadata: dict[str, Any] | None = None,
    ) -> "MessageEnvelope":
        return cls(
            mtp_version=mtp_version,
            kind=kind,
            payload=payload,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mtp_version": self.mtp_version,
            "kind": self.kind,
            "payload": self.payload,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MessageEnvelope":
        return cls(
            mtp_version=str(data.get("mtp_version", CURRENT_MTP_VERSION)),
            kind=str(data["kind"]),
            payload=dict(data.get("payload", {})),
            metadata=dict(data.get("metadata", {})),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, raw: str) -> "MessageEnvelope":
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Envelope JSON must decode to an object.")
        return cls.from_dict(data)


class PlanValidationError(ValueError):
    pass


def validate_execution_plan(plan: ExecutionPlan) -> None:
    call_ids: list[str] = []
    deps_map: dict[str, list[str]] = {}

    for batch in plan.batches:
        for call in batch.calls:
            if call.id in deps_map:
                raise PlanValidationError(f"Duplicate call id: {call.id}")
            call_ids.append(call.id)
            deps_map[call.id] = list(call.depends_on)

    call_id_set = set(call_ids)
    for call_id, deps in deps_map.items():
        missing = [dep for dep in deps if dep not in call_id_set]
        if missing:
            raise PlanValidationError(
                f"Call {call_id} depends on missing call ids: {missing}"
            )

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {call_id: WHITE for call_id in call_ids}

    def visit(node: str, trail: list[str]) -> None:
        color[node] = GRAY
        for dep in deps_map[node]:
            if color[dep] == WHITE:
                visit(dep, trail + [dep])
            elif color[dep] == GRAY:
                raise PlanValidationError(
                    f"Cyclic dependency detected: {' -> '.join(trail + [dep])}"
                )
        color[node] = BLACK

    for call_id in call_ids:
        if color[call_id] == WHITE:
            visit(call_id, [call_id])
