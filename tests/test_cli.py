from __future__ import annotations

import contextlib
import io
import pathlib
import textwrap
import unittest

import sys
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mtp.cli.main import main
from tests.harness_utils import safe_rmtree, workspace_tempdir


@contextlib.contextmanager
def _workspace_tempdir() -> pathlib.Path:
    temp_path = workspace_tempdir(prefix="cli_test")
    try:
        yield temp_path
    finally:
        safe_rmtree(temp_path)


pytestmark = pytest.mark.integration


class CLITests(unittest.TestCase):
    def test_new_minimal_scaffold_writes_expected_files(self) -> None:
        with _workspace_tempdir() as base:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main(["new", "demo_app", "--template", "minimal", "--dir", str(base)])
            self.assertEqual(code, 0, stderr.getvalue())
            project = base / "demo_app"
            self.assertTrue((project / "app.py").exists())
            self.assertTrue((project / "README.md").exists())
            self.assertTrue((project / "pyproject.toml").exists())
            self.assertTrue((project / ".env.example").exists())
            self.assertIn("Created project:", stdout.getvalue())
            app_text = (project / "app.py").read_text(encoding="utf-8")
            self.assertIn("Agent.MTPAgent", app_text)
            self.assertIn("Groq", app_text)

    def test_new_mcp_http_template_contains_server(self) -> None:
        with _workspace_tempdir() as base:
            code = main(["new", "demo_server", "--template", "mcp-http", "--dir", str(base)])
            self.assertEqual(code, 0)
            project = base / "demo_server"
            self.assertTrue((project / "server.py").exists())
            server_text = (project / "server.py").read_text(encoding="utf-8")
            self.assertIn("MCPHTTPTransportServer", server_text)
            self.assertIn("MCPJsonRpcServer", server_text)

    def test_providers_list_command_prints_table(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["providers", "list"])
        self.assertEqual(code, 0, stderr.getvalue())
        out = stdout.getvalue()
        self.assertIn("name", out)
        self.assertIn("groq", out)
        self.assertIn("openai", out)

    def test_doctor_unknown_provider_fails(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["doctor", "--provider", "not-real"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown provider", stderr.getvalue())

    def test_run_executes_entry_script(self) -> None:
        with _workspace_tempdir() as project:
            (project / "app.py").write_text(
                textwrap.dedent(
                    """
                    def main():
                        print("hello-from-cli-run")

                    if __name__ == "__main__":
                        main()
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main(["run", "--path", str(project)])
            self.assertEqual(code, 0, stderr.getvalue())
            self.assertIn("hello-from-cli-run", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
