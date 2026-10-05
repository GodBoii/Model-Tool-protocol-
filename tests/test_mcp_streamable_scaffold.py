"""Generate and execute the modern CLI scaffold with its real MCP endpoint."""

from __future__ import annotations

import importlib.util

import pytest

pytest.importorskip("jsonschema")

from mtp.cli.main import build_parser
from mtp.cli.scaffold import scaffold_project


def test_modern_scaffold_has_installable_dependency_and_valid_tool(
    tmp_path, monkeypatch
):
    parsed = build_parser().parse_args(
        ["new", "modern_server", "--template", "mcp-streamable-http"]
    )
    assert parsed.template == "mcp-streamable-http"
    project = scaffold_project(
        name="modern_server", template=parsed.template, base_dir=tmp_path
    )
    assert {path.name for path in project.written_files} == {
        "server.py",
        "README.md",
        "pyproject.toml",
        ".env.example",
    }
    assert (
        "mtpx[mcp-modern]>=0.1.40"
        in (project.project_dir / "pyproject.toml").read_text()
    )
    spec = importlib.util.spec_from_file_location(
        "modern_fixture", project.project_dir / "server.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    captured = []

    class Transport:
        def __init__(self, host, port, server):
            captured.append(server)

        def start(self):
            pass

    monkeypatch.setattr(module, "MCPStreamableHTTPTransportServer", Transport)
    monkeypatch.setattr(module, "load_dotenv_if_available", lambda: None)
    monkeypatch.setenv("MTP_HTTP_HOST", "127.0.0.1")
    monkeypatch.delenv("MTP_MCP_TOKEN", raising=False)
    module.main()
    from test_mcp_modern import request

    response = captured[0].handle_request(
        request("tools/call", name="calc.add", arguments={"a": 17, "b": 25})
    )
    assert response["result"]["result"]["output"] == 42
    monkeypatch.setenv("MTP_HTTP_HOST", "0.0.0.0")
    with pytest.raises(ValueError, match="MTP_MCP_TOKEN"):
        module.main()
