# {{PROJECT_NAME}}

MTP scaffold with JSON session persistence.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .[groq]
copy .env.example .env
```

Set `GROQ_API_KEY` in `.env`.

## Run

```bash
mtp run
```

This scaffold writes session data to `tmp/mtp_json_db`.

