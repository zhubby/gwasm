#!/usr/bin/env python3
"""CLI parsing and distribution-boundary tests."""

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import getsnapshot
import gwasm
import gwpatch
from gwasm_core.patch import MAX_JOBS


class ToolArgumentTests(unittest.TestCase):
    def test_getsnapshot_default_uses_maximum(self):
        with mock.patch.object(
                gwpatch, "make_session", side_effect=AssertionError("network")):
            args = getsnapshot.parse_args([])
        self.assertEqual(args.jobs, MAX_JOBS)

    def test_job_boundaries_are_accepted(self):
        for value in (1, MAX_JOBS):
            with self.subTest(value=value):
                self.assertEqual(getsnapshot.parse_args(["-j", str(value)]).jobs,
                                 value)
                self.assertEqual(gwpatch.parse_args(["-j", str(value)]).jobs,
                                 value)

    def test_out_of_range_jobs_are_rejected(self):
        for parser in (getsnapshot.parse_args, gwpatch.parse_args):
            for value in (0, MAX_JOBS + 1):
                with self.subTest(parser=parser.__module__, value=value):
                    with contextlib.redirect_stderr(io.StringIO()), \
                            self.assertRaises(SystemExit) as raised:
                        parser(["-j", str(value)])
                    self.assertEqual(raised.exception.code, 2)

    def test_help_documents_consistent_limit_and_current_launcher(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "getsnapshot.py"), "--help"],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("default: 8, maximum: 8", result.stdout)
        self.assertIn("gwasm.py serves", result.stdout)
        self.assertNotIn("run.py", result.stdout)

    def test_tools_use_standard_library_http(self):
        source = (ROOT / "gwpatch.py").read_text()
        self.assertNotIn("import requests", source)
        self.assertNotIn("from requests", source)
        self.assertEqual(
            gwpatch.make_session("test-key").headers["X-Access-Key"],
            "test-key")

    def test_launcher_help_keeps_public_flags(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "gwasm.py"), "--help"],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in ("--bind", "--origin", "--offline", "--image", "--build"):
            self.assertIn(flag, result.stdout)

    def test_missing_core_has_friendly_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "gwasm.py"
            target.write_bytes((ROOT / "gwasm.py").read_bytes())
            result = subprocess.run(
                [sys.executable, str(target), "--help"],
                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("gwasm_core folder is missing", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_required_files_cover_core_and_host_layers(self):
        required = set(gwasm.REQUIRED)
        for name in (
            "gwasm_core/http.py", "gwasm_core/patch.py",
            "gwasm_core/runtime.py", "gwasm_core/server.py",
            "harness/harness-cache.js", "harness/harness-module.js",
            "harness/harness-input.js", "harness/harness-boot.js",
        ):
            self.assertIn(name, required)
            self.assertTrue((ROOT / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
