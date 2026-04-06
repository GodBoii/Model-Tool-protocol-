from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


TEMPLATE_ROOT = Path(__file__).resolve().parent / "templates"

VALID_TEMPLATES = {"minimal", "mcp-http", "session-json"}


@dataclass(frozen=True, slots=True)
class ScaffoldResult:
    project_dir: Path
    written_files: list[Path]


def _normalize_project_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("Project name cannot be empty.")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", cleaned):
        raise ValueError("Project name must contain only letters, numbers, '_' or '-'.")
    return cleaned


def _safe_render(template_text: str, *, project_name: str) -> str:
    return template_text.replace("{{PROJECT_NAME}}", project_name)


def _template_dir(template: str) -> Path:
    if template not in VALID_TEMPLATES:
        raise ValueError(f"Unknown template '{template}'. Expected one of: {sorted(VALID_TEMPLATES)}")
    path = TEMPLATE_ROOT / template
    if not path.exists():
        raise FileNotFoundError(f"Template directory is missing: {path}")
    return path


def scaffold_project(
    *,
    name: str,
    template: str,
    base_dir: Path,
    force: bool = False,
) -> ScaffoldResult:
    project_name = _normalize_project_name(name)
    tpl_dir = _template_dir(template)
    project_dir = base_dir / project_name

    if project_dir.exists():
        if not force:
            raise FileExistsError(f"Target directory already exists: {project_dir}")
    else:
        project_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for source in tpl_dir.rglob("*"):
        if source.is_dir():
            continue
        rel = source.relative_to(tpl_dir)
        target = project_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix == ".tpl":
            content = source.read_text(encoding="utf-8")
            rendered = _safe_render(content, project_name=project_name)
            target = target.with_suffix("")
            target.write_text(rendered, encoding="utf-8")
        else:
            target.write_bytes(source.read_bytes())
        written.append(target)
    return ScaffoldResult(project_dir=project_dir, written_files=sorted(written))
