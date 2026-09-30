from __future__ import annotations

from dataclasses import dataclass

from .tui_settings import (
    ensure_provider_entry,
    load_provider_settings,
    provider_settings_path,
    save_provider_settings,
)
from .tui_state import REASONING_EFFORTS, TUIState, active_model_name, resolve_reasoning


_XIAOMI_THINKING_MODELS = {
    "mimo-v2.5-pro",
    "mimo-v2-pro",
    "mimo-v2.5",
    "mimo-v2-omni",
    "mimo-v2-flash",
}


@dataclass(frozen=True, slots=True)
class ThinkingOption:
    value: str
    label: str


@dataclass(frozen=True, slots=True)
class ThinkingCapability:
    backend: str
    label: str
    options: tuple[ThinkingOption, ...]
    current_value: str
    current_label: str


def xiaomi_model_supports_thinking(model_name: str) -> bool:
    normalized = model_name.strip().lower()
    return normalized in _XIAOMI_THINKING_MODELS


def _load_provider_entry_for_state(state: TUIState) -> dict:
    settings_path = provider_settings_path(state.session_store.file_path)
    settings = load_provider_settings(settings_path)
    return ensure_provider_entry(settings, state.backend)


def get_thinking_capability(state: TUIState) -> ThinkingCapability | None:
    if state.backend == "codex":
        from .tui_codex_metadata import get_codex_model

        model = get_codex_model(state.codex_model)
        efforts = model.efforts if model else REASONING_EFFORTS
        current = state.reasoning_effort if state.reasoning_effort in efforts else model.default_effort if model else "medium"
        return ThinkingCapability(
            backend="codex",
            label="reasoning",
            options=tuple(ThinkingOption(value=name, label=name) for name in efforts),
            current_value=current,
            current_label=current,
        )

    if state.backend != "xiaomi":
        return None

    model_name = active_model_name(state)
    if not xiaomi_model_supports_thinking(model_name):
        return None

    entry = _load_provider_entry_for_state(state)
    raw_mode = str(entry.get("thinking_mode") or "").strip().lower()
    current = "disabled" if raw_mode == "disabled" else "enabled"
    current_label = "off" if current == "disabled" else "on"
    return ThinkingCapability(
        backend="xiaomi",
        label="thinking",
        options=(
            ThinkingOption(value="enabled", label="on"),
            ThinkingOption(value="disabled", label="off"),
        ),
        current_value=current,
        current_label=current_label,
    )


def apply_thinking_value(state: TUIState, value: str, *, persist: bool = True) -> str:
    """Apply a reasoning/thinking choice. With ``persist=False`` the caller saves."""
    if state.backend == "codex":
        resolved = resolve_reasoning(value)
        capability = get_thinking_capability(state)
        if resolved is None or capability is None or resolved not in {option.value for option in capability.options}:
            raise ValueError("Unsupported reasoning level")
        state.reasoning_effort = resolved
        if persist:
            from .tui_workers import save_tui_session

            save_tui_session(state)
        return f"✓ Reasoning set to {resolved}"

    if state.backend != "xiaomi":
        raise ValueError(f"Thinking controls are not supported for backend {state.backend!r}")

    normalized = str(value).strip().lower()
    if normalized in {"on", "enabled"}:
        thinking_mode = "enabled"
        label = "on"
    elif normalized in {"off", "disabled"}:
        thinking_mode = "disabled"
        label = "off"
    else:
        raise ValueError("Unsupported Xiaomi thinking mode")

    settings_path = provider_settings_path(state.session_store.file_path)
    settings = load_provider_settings(settings_path)
    entry = ensure_provider_entry(settings, state.backend)
    entry["thinking_mode"] = thinking_mode
    entry["final_thinking_mode"] = thinking_mode
    save_provider_settings(settings_path, settings)
    state.agent = None

    if persist:
        from .tui_workers import save_tui_session

        save_tui_session(state)
    return f"✓ Xiaomi thinking set to {label}"
