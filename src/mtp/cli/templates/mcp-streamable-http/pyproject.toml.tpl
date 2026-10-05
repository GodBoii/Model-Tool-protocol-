[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{{PROJECT_NAME}}"
version = "0.1.0"
description = "Stateless MCP Streamable HTTP server"
readme = "README.md"
requires-python = ">=3.11"
dependencies = ["mtpx[mcp-modern]>=0.1.40", "python-dotenv"]

[tool.setuptools]
py-modules = ["server"]
