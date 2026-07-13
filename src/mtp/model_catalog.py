"""Stable, SDK-owned model defaults and context metadata.

Keep provider constructors and the CLI/TUI on the same production model IDs.
Only stable generally available models belong here; preview, experimental, and
``latest`` aliases are intentionally excluded from defaults.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Mapping


ANTHROPIC_DEFAULT_MODEL: Final = "claude-sonnet-4-6"
GEMINI_DEFAULT_MODEL: Final = "gemini-3.5-flash"

# ``claude`` is the user-facing TUI provider name while ``anthropic`` is the
# SDK/provider package name. Both resolve to the same pinned production model.
DEFAULT_MODEL_BY_PROVIDER: Mapping[str, str] = MappingProxyType(
    {
        "anthropic": ANTHROPIC_DEFAULT_MODEL,
        "claude": ANTHROPIC_DEFAULT_MODEL,
        "gemini": GEMINI_DEFAULT_MODEL,
    }
)

# Values come from the providers' model specification pages. These are input
# limits, which is what the TUI needs when reporting conversation usage.
MODEL_CONTEXT_WINDOWS: Mapping[str, int] = MappingProxyType(
    {
        ANTHROPIC_DEFAULT_MODEL: 1_000_000,
        GEMINI_DEFAULT_MODEL: 1_048_576,
    }
)

PROVIDER_DEFAULT_CONTEXT_WINDOWS: Mapping[str, int] = MappingProxyType(
    {
        "anthropic": MODEL_CONTEXT_WINDOWS[ANTHROPIC_DEFAULT_MODEL],
        "claude": MODEL_CONTEXT_WINDOWS[ANTHROPIC_DEFAULT_MODEL],
        "gemini": MODEL_CONTEXT_WINDOWS[GEMINI_DEFAULT_MODEL],
    }
)
