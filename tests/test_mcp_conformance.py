from __future__ import annotations

import json
from pathlib import Path
import shutil
import unittest
from uuid import uuid4

import pytest

from tests.conformance.run_conformance import main as conformance_main

pytestmark = pytest.mark.integration


class MCPConformanceTests(unittest.TestCase):
    def test_conformance_generates_reports_without_critical_failures(self) -> None:
        tmp = Path("tmp") / f"conformance_test_{uuid4().hex}"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            json_path = tmp / "report.json"
            md_path = tmp / "matrix.md"
            code = conformance_main(
                [
                    "--profile",
                    "all",
                    "--server-feature-set",
                    "resumable",
                    "--report-json",
                    str(json_path),
                    "--matrix-doc",
                    str(md_path),
                    "--fail-on-critical",
                ]
            )
            self.assertEqual(code, 0)
            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())

            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertIn("clients", payload)
            self.assertTrue(payload["clients"])
            for client in payload["clients"]:
                self.assertIn("scenarios", client)
                self.assertTrue(client["scenarios"])
                critical_failures = [
                    scenario for scenario in client["scenarios"] if (not scenario["passed"]) and scenario["severity"] == "critical"
                ]
                self.assertFalse(critical_failures)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
