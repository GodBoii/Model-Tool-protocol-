from __future__ import annotations

import pytest

from mtp.mcp import MCPJsonRpcServer
from mtp.runtime import ToolRegistry
from mtp.toolkits.website_toolkit import WebsiteToolkit


def test_mcp_require_auth_needs_real_verifier() -> None:
    with pytest.raises(ValueError, match="require_auth=True requires"):
        MCPJsonRpcServer(tools=ToolRegistry(), require_auth=True)


def test_mcp_auth_token_rejects_arbitrary_token() -> None:
    server = MCPJsonRpcServer(tools=ToolRegistry(), require_auth=True, auth_token="expected")

    rejected = server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"auth_token": "wrong"}}
    )
    accepted = server.handle_request(
        {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"auth_token": "expected"}}
    )

    assert rejected is not None
    assert rejected["error"]["code"] == -32001
    assert accepted is not None
    assert "result" in accepted


def test_website_toolkit_blocks_local_targets_by_default() -> None:
    toolkit = WebsiteToolkit()

    for url in ("http://localhost:8000", "http://127.0.0.1", "http://[::1]/"):
        with pytest.raises(ValueError, match="localhost|private|non-public"):
            toolkit._validate_url(url)


def test_website_toolkit_can_opt_into_private_targets() -> None:
    toolkit = WebsiteToolkit(allow_private_hosts=True)

    assert toolkit._validate_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
