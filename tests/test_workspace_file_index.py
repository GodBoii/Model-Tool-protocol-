from pathlib import Path

from mtp.cli.workspace_file_index import WorkspaceFileIndex


def test_suggestions_are_cached_and_case_insensitive(tmp_path: Path) -> None:
    (tmp_path / "Alpha.py").write_text("")
    index = WorkspaceFileIndex(cache_ttl=60)

    assert index.suggest(tmp_path, "ALP") == ["Alpha.py"]
    (tmp_path / "alphabet.md").write_text("")
    assert index.suggest(tmp_path, "alp") == ["Alpha.py"]

    index.invalidate()
    assert index.suggest(tmp_path, "alp") == ["Alpha.py", "alphabet.md"]


def test_refreshes_after_ttl_and_when_root_changes(tmp_path: Path) -> None:
    now = [0.0]
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "one.txt").write_text("")
    (second / "two.txt").write_text("")
    index = WorkspaceFileIndex(cache_ttl=5, clock=lambda: now[0])

    assert index.suggest(first, "") == ["one.txt"]
    (first / "new.txt").write_text("")
    now[0] = 4
    assert index.suggest(first, "") == ["one.txt"]
    now[0] = 5
    assert index.suggest(first, "") == ["new.txt", "one.txt"]
    assert index.suggest(second, "") == ["two.txt"]


def test_scan_excludes_hidden_large_and_deep_directories(tmp_path: Path) -> None:
    (tmp_path / ".secret").write_text("")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "package.js").write_text("")
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (tmp_path / "a" / "visible.txt").write_text("")
    (deep / "too-deep.txt").write_text("")

    files = WorkspaceFileIndex().refresh(tmp_path)

    assert files == ("a/visible.txt",)


def test_scan_honors_file_cap(tmp_path: Path) -> None:
    for number in range(10):
        (tmp_path / f"{number}.txt").write_text("")

    assert len(WorkspaceFileIndex(max_files=3).refresh(tmp_path)) == 3


def test_scan_honors_time_budget_and_unreadable_paths(tmp_path: Path) -> None:
    (tmp_path / "one.txt").write_text("")
    ticks = iter((0.0, 0.0, 1.0, 1.0))
    index = WorkspaceFileIndex(scan_time_budget=0.1, clock=lambda: next(ticks, 1.0))

    assert index.refresh(tmp_path) == ()


def test_limit_zero_returns_no_suggestions(tmp_path: Path) -> None:
    (tmp_path / "one.txt").write_text("")
    assert WorkspaceFileIndex().suggest(tmp_path, "", limit=0) == []
