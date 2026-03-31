from __future__ import annotations

from typing import Any


def ref_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"$ref": {"type": "string"}},
        "required": ["$ref"],
        "additionalProperties": False,
    }


def allow_ref(base_schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [base_schema, ref_schema()]}
