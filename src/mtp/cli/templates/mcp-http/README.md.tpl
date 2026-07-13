# {{PROJECT_NAME}}

MTP scaffold with an MCP HTTP transport server.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e .
cp .env.example .env      # Windows: copy .env.example .env
```

## Run

```bash
mtp run
```

The server exposes:
- JSON-RPC: `POST /rpc`
- polling: `GET /events`
- SSE: `GET /events/sse`

