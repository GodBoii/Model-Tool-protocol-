from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def launch() -> int:
    try:
        import streamlit  # noqa: F401
    except Exception:  # noqa: BLE001
        print("Streamlit is not installed. Install with: pip install mtpx[ui-streamlit]", file=sys.stderr)
        return 1

    app_path = Path(__file__).resolve().parent / "app.py"
    cmd = [sys.executable, "-m", "streamlit", "run", str(app_path)]
    proc = subprocess.run(cmd, check=False)
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(launch())
