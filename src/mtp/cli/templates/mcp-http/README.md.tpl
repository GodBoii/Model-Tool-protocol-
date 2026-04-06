# {{PROJECT_NAME}}

MTP scaffold with an MCP HTTP transport server.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
copy .env.example .env
```

## Run

```bash
mtp run
```

The server exposes:
- JSON-RPC: `POST /rpc`
- polling: `GET /events`
- SSE: `GET /events/sse`

