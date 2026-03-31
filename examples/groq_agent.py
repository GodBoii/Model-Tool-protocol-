from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp import MTPAgent

def main() -> None:
    agent = MTPAgent(
        model="moonshotai/kimi-k2-instruct",
        instructions="Use tools for concrete computation or file operations and be concise.",
        debug_mode=True,
        strict_dependency_mode=True,
        base_dir=pathlib.Path.cwd(),
    )
    reply = agent.run(
        "Calculate (25 * 4) + 10 and then list files in the current directory.  "
        "Give a short summary.",
        max_rounds=4,
    )
    print(reply)


if __name__ == "__main__":
    main()
