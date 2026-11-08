"""Generic entry points work without consumer headers, packages, or services."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

from tests.inspection_support import jsonl


ROOT = Path(__file__).resolve().parents[1]


class GenericStandaloneTests(unittest.TestCase):
    def _environment(self, columns=80):
        return dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1",
                    NO_COLOR="1", COLUMNS=str(columns), LINES="30")

    def test_plain_file_cli_needs_no_identity_arguments_and_preserves_source(self):
        source = jsonl({"level": "error", "message": "timeout"})
        digest = hashlib.sha256(source).digest()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "audit.jsonl"
            path.write_bytes(source)
            for columns in (80, 180):
                with self.subTest(columns=columns):
                    result = subprocess.run([sys.executable, "-W", "error", "-m", "jsonl_viewer", str(path)],
                        input="p\nq\n", capture_output=True, text=True, timeout=10,
                                            cwd=temporary, env=self._environment(columns))
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('"message": "timeout"', result.stdout)
                    self.assertIn("record", result.stdout.casefold())
                    self.assertNotIn("Session:", result.stdout)
                    self.assertNotIn("Conversation:", result.stdout)
                    self.assertNotIn("Agent:", result.stdout)
                    self.assertNotIn("\x1b", result.stdout)
                    self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), digest)

    def test_stdin_cli_and_scalar_roots_need_no_context(self):
        for columns in (80, 180):
            with self.subTest(columns=columns):
                result = subprocess.run([sys.executable, "-W", "error", "-m", "jsonl_viewer", "-"],
                                        input=jsonl("standalone scalar"), capture_output=True, timeout=10,
                                        cwd=ROOT, env=self._environment(columns))
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertIn("standalone scalar", result.stdout.decode())
                self.assertNotIn("Session:", result.stdout.decode())

    def test_partial_legacy_context_is_a_content_safe_cli_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "audit.jsonl"
            path.write_bytes(jsonl({"message": "private-record-value"}))
            result = subprocess.run([sys.executable, "-W", "error", "-m", "jsonl_viewer", str(path),
                                     "--session", "one-part-only"], input="q\n", capture_output=True,
                                    text=True, timeout=10, cwd=temporary, env=self._environment())
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)
            self.assertNotIn("private-record-value", result.stdout + result.stderr)

    def test_isolated_generic_ui_uses_only_source_and_inert_descriptors(self):
        script = textwrap.dedent(r'''
            import hashlib, importlib.abc, importlib.util, json, pathlib, socket, subprocess, sys
            assert importlib.util.find_spec("story_writing_agents") is None
            class RefuseConsumer(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname == "story_writing_agents" or fullname.startswith("story_writing_agents."):
                        raise AssertionError("Consumer import attempted")
            sys.meta_path.insert(0, RefuseConsumer())
            sys.path.insert(0, sys.argv[1])
            from jsonl_viewer import ViewerSpec, view_jsonl
            from jsonl_viewer.inspection import Context, Facet, Group, Inspection, Option, Section
            source = b'{"message":"ready"}\n{"message":"timeout"}\n{"message":"sensor timeout"}\n'
            digest = hashlib.sha256(source).digest()
            description = Inspection(
                groups=(Group("batch-104", "Audit bundle", (1,2),
                              sections=(Section("events", "Events", (1,2)),),
                              values=(("severity", "error"),)),
                        Group("sensor", "센서 묶음", (3,), values=(("severity", "error"),))),
                facets=(Facet("severity", "Severity", (Option("error", "Error"),)),),
            )
            class Host:
                def __init__(self):
                    self.frames = []; self.closed = 0
                    self.events = iter(("text\tf", "key\tescape", "text\t/", "text\ttimeout",
                        "key\tenter", "next_match", "next_match", "key\ttab", "key\tescape",
                        "key\ttab", "text\tt", "text\t센서", "key\tenter", "key\ttab", "text\tt",
                        "key\tctrl_u", "text\tbatch-104", "key\tenter", "text\tq"))
                def terminal_size(self): return (180,30)
                def color_enabled(self): return False
                def present(self, frame): self.frames.append(frame)
                def read_event(self): return next(self.events, None)
                def close_view(self): self.closed += 1
            def forbidden(*args, **kwargs): raise AssertionError("External metadata/service access")
            socket.create_connection = forbidden
            subprocess.Popen = forbidden
            pathlib.Path.read_text = forbidden
            pathlib.Path.read_bytes = forbidden
            host = Host()
            view_jsonl(source, ViewerSpec(context=(Context("Dataset", "Audit"),),
                                         inspection=description, input_protocol="keys"), host)
            frames = "\n".join(host.frames)
            assert "Audit bundle" in frames and "Severity" in frames and "Events" in frames
            assert "센서 묶음" in frames and "Name/ID:" in frames
            assert "Record 2/2" in host.frames[-1]
            assert "timeout" in frames and host.closed == 1
            assert hashlib.sha256(source).digest() == digest
            assert not any(name == "story_writing_agents" or name.startswith("story_writing_agents.")
                           for name in sys.modules)
            print(json.dumps({"closed":host.closed,"frames":len(host.frames),"source_unchanged":True}))
        ''')
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([sys.executable, "-I", "-S", "-B", "-W", "error", "-c", script,
                                     str(ROOT / "src")], capture_output=True, text=True, timeout=10,
                                    cwd=temporary, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["closed"], 1)
        self.assertGreater(report["frames"], 3)
        self.assertTrue(report["source_unchanged"])


if __name__ == "__main__":
    unittest.main()
