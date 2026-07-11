from __future__ import annotations

from collections.abc import Iterable


def main(argv: Iterable[str] | None = None) -> int:
    """Load the CLI lazily so ``python -m mtp.cli.main`` remains warning-free."""
    from .main import main as _main

    return _main(argv)

__all__ = ["main"]

