from __future__ import annotations

import os
import platform
from pathlib import Path
from dataclasses import dataclass
import importlib.util

from ..config import load_dotenv_if_available
from .providers import ProviderInfo, list_providers


@dataclass(frozen=True, slots=True)
class DoctorItem:
    name: str
    status: str
    detail: str


def _status(ok: bool) -> str:
    return "OK" if ok else "WARN"


def _check_python() -> DoctorItem:
    version = platform.python_version()
    major, minor, *_ = platform.python_version_tuple()
    ok = int(major) > 3 or (int(major) == 3 and int(minor) >= 11)
    return DoctorItem("python", _status(ok), f"Python {version} (requires >= 3.11)")


def _check_dotenv() -> DoctorItem:
    installed = importlib.util.find_spec("dotenv") is not None
    return DoctorItem("python-dotenv", _status(installed), "optional helper for loading .env files")


def _check_provider(info: ProviderInfo, settings: dict) -> list[DoctorItem]:
    from .tui_settings import provider_api_key, provider_setup_status
    items: list[DoctorItem] = []
    if info.sdk_module is not None:
        installed = info.sdk_installed() is True
        items.append(
            DoctorItem(
                f"{info.name}.sdk",
                _status(installed),
                f"{info.sdk_module} {'installed' if installed else 'missing'}",
            )
        )
    if info.env_var is not None:
        provider = "claude" if info.name == "anthropic" else info.name
        present = bool(provider_api_key(settings, provider))
        source = provider_setup_status(settings, provider)
        items.append(
            DoctorItem(
                f"{info.name}.credentials",
                _status(present),
                f"{source}. Environment variable: {info.env_var}",
            )
        )
    return items


def run_doctor(provider_filter: set[str] | None = None, *, session_db: str | Path | None = None) -> list[DoctorItem]:
    from .tui_settings import load_provider_settings, provider_settings_path
    load_dotenv_if_available()
    settings = load_provider_settings(provider_settings_path(session_db or Path.home() / ".mtp" / "sessions"))
    rows: list[DoctorItem] = [_check_python(), _check_dotenv()]
    selected = list_providers()
    if provider_filter:
        selected = [p for p in selected if p.name in provider_filter or p.alias.lower() in provider_filter]
    for provider in selected:
        rows.extend(_check_provider(provider, settings))
    return rows

