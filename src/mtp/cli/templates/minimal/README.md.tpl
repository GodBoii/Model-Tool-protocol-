# {{PROJECT_NAME}}

Minimal MTP agent scaffold.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[groq]"
cp .env.example .env      # Windows: copy .env.example .env
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

