from __future__ import annotations

import re
from pathlib import Path

import mtp


ROOT = Path(__file__).resolve().parents[1]


def test_readme_doc_links_exist() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    links = re.findall(r"\]\((docs/[^)]+)\)", readme)

    assert links, "README should link to project docs."
    missing = [link for link in links if not (ROOT / link).exists()]
    assert missing == []


def test_pyproject_version_matches_package_version() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', pyproject, flags=re.MULTILINE)

    assert match is not None
    assert match.group(1) == mtp.__version__
