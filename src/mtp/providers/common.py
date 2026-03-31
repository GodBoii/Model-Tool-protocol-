from __future__ import annotations

import json
import re
from typing import Any

from ..protocol import ToolBatch, ToolCall


def extract_refs(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, dict):
        if "$ref" in value and isinstance(value["$ref"], str):
            refs.append(value["$ref"])
        for item in value.values():
            refs.extend(extract_refs(item))
        return refs
    if isinstance(value, list):
        for item in value:
            refs.extend(extract_refs(item))
    return refs


def normalize_refs(value: Any, id_by_index: dict[int, str]) -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if key == "$ref":
                if isinstance(item, int) and item in id_by_index:
                    normalized[key] = id_by_index[item]
                elif isinstance(item, str) and item.isdigit():
                    idx = int(item)
                    normalized[key] = id_by_index.get(idx, item)
                elif isinstance(item, str):
                    match = re.search(r"(\d+)$", item)
                    if match:
                        idx = int(match.group(1))
                        normalized[key] = id_by_index.get(idx, item)
                    else:
                        normalized[key] = item
                else:
                    normalized[key] = item
            else:
                normalized[key] = normalize_refs(item, id_by_index)
        return normalized
    if isinstance(value, list):
        return [normalize_refs(item, id_by_index) for item in value]
    return value


def safe_load_arguments(raw_args: str | None) -> dict[str, Any]:
    raw = raw_args or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw_arguments": raw}
    if isinstance(parsed, dict):
        return parsed
    return {"_raw_arguments": raw}


def calls_to_dependency_batches(calls: list[ToolCall]) -> list[ToolBatch]:
    remaining: dict[str, ToolCall] = {call.id: call for call in calls}
    ordered_ids = [call.id for call in calls]
    done: set[str] = set()
    batches: list[ToolBatch] = []

    while remaining:
        ready_ids = [
            call_id
            for call_id in ordered_ids
            if call_id in remaining and all(dep in done for dep in remaining[call_id].depends_on)
        ]
        if not ready_ids:
            unresolved_calls = [remaining[call_id] for call_id in ordered_ids if call_id in remaining]
            batches.append(ToolBatch(mode="sequential", calls=unresolved_calls))
            break

        ready_calls = [remaining.pop(call_id) for call_id in ready_ids]
        mode = "parallel" if len(ready_calls) > 1 else "sequential"
        batches.append(ToolBatch(mode=mode, calls=ready_calls))
        done.update(ready_ids)

    return batches
