"""The package imports its public names on demand but keeps the same API."""
from __future__ import annotations

import subprocess
import sys


def _run(code: str) -> str:
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def test_importing_the_package_does_not_load_the_agent_loop() -> None:
    out = _run("import sys, mtp; print('mtp.agent' in sys.modules, 'mtp.mcp' in sys.modules)")
    assert out == "False False"


def test_session_store_import_stays_light() -> None:
    out = _run("import sys; from mtp import JsonSessionStore; print('mtp.agent' in sys.modules)")
    assert out == "False"


def test_every_public_name_resolves() -> None:
    import mtp

    for name in mtp.__all__:
        getattr(mtp, name)  # raises if an export is broken
    assert set(mtp.__all__) <= set(dir(mtp))


def test_star_import_matches_all() -> None:
    namespace: dict[str, object] = {}
    exec("from mtp import *", namespace)
    import mtp

    assert set(mtp.__all__) <= set(namespace)


def test_agent_aliases_work_even_when_agent_is_imported_directly() -> None:
    out = _run(
        "from mtp.agent import Agent\n"
        "from mtp.simple_agent import MTPAgent\n"
        "from mtp.runtime import ToolRegistry\n"
        "print(Agent.MTPAgent is MTPAgent, Agent.ToolRegistry is ToolRegistry, callable(Agent.mtp_tool))"
    )
    assert out == "True True True"


def test_function_aliases_stay_static_on_instances() -> None:
    from mtp import Agent
    from mtp.tools import mtp_tool

    assert Agent.mtp_tool is mtp_tool
    # A staticmethod alias must not bind the instance as the first argument.
    assert Agent.__dict__["mtp_tool"].__func__ is mtp_tool


def test_submodules_resolve_as_attributes() -> None:
    out = _run("import mtp; print(mtp.providers.__name__, mtp.protocol.__name__)")
    assert out == "mtp.providers mtp.protocol"


def test_unknown_attribute_raises_attribute_error() -> None:
    import mtp

    try:
        mtp.definitely_not_a_name  # noqa: B018
    except AttributeError:
        return
    raise AssertionError("expected AttributeError")
