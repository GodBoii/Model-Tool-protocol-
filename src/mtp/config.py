from __future__ import annotations

import os
from pathlib import Path


def load_dotenv_if_available(path: str | None = None) -> bool:
    try:
        from dotenv import load_dotenv
    except Exception:
        return False
    if path is not None:
        load_dotenv(dotenv_path=path, override=False)
        return True

    env_candidates = [Path(".env"), Path(".env.example")]
    loaded = False
    for candidate in env_candidates:
        if candidate.exists():
            load_dotenv(dotenv_path=candidate, override=False)
            loaded = True
    return loaded


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Environment variable {name} is required.")
    return value
