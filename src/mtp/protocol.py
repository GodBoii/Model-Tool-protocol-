from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from .media import Audio, File, Image, Video


class ToolRiskLevel(str, Enum):
    READ_ONLY = "read_only"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    risk_level: ToolRiskLevel = ToolRiskLevel.READ_ONLY
    cost_hint: str = "unknown"
    side_effects: str = "none"
    cache_ttl_seconds: int = 0


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ToolResult:
    call_id: str
    tool_name: str
    output: Any
    success: bool = True
    error: str | None = None
    cached: bool = False
    approval: str | None = None
    skipped: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    images: list[Image] | None = None
    videos: list[Video] | None = None
    audios: list[Audio] | None = None
    files: list[File] | None = None


@dataclass(slots=True)
class ToolOutput:
    content: Any
    images: list[Image] | None = None
    videos: list[Video] | None = None
    audios: list[Audio] | None = None
    files: list[File] | None = None


@dataclass(slots=True)
class ToolBatch:
    mode: str  # "parallel" | "sequential"
    calls: list[ToolCall]

    def __post_init__(self) -> None:
        if self.mode not in {"parallel", "sequential"}:
            raise ValueError("ToolBatch.mode must be 'parallel' or 'sequential'")


@dataclass(slots=True)
class ExecutionPlan:
    batches: list[ToolBatch] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
