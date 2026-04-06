# {{PROJECT_NAME}}

Minimal MTP agent scaffold.

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

or:

```bash
python app.py
```

