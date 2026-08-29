"""Standalone command and source-distribution boundary tests."""

from __future__ import annotations

import ast
import io
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from jsonl_viewer import _standalone
from jsonl_viewer._input import MAX_SOURCE_BYTES
from jsonl_viewer._standalone import _TerminalHost, _read_bounded


ROOT = Path(__file__).resolve().parents[1]


class StandaloneTests(unittest.TestCase):
    def test_no_color_environment_overrides_a_capable_terminal(self) -> None:
        class TerminalBuffer(io.StringIO):
            def isatty(self) -> bool:
                return True

        output = TerminalBuffer()
        host = _TerminalHost(TerminalBuffer(), output, no_color=False)
        with mock.patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=True):
            self.assertTrue(host.color_enabled())
        with mock.patch.dict(
            os.environ,
            {"TERM": "xterm-256color", "NO_COLOR": "1"},
            clear=True,
        ):
            self.assertFalse(host.color_enabled())

    def test_plain_line_standalone_smoke_preserves_source(self) -> None:
        source = b'{"timestamp":"t","request_type":"request","content":"hello"}\n'
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "records.jsonl"
            path.write_bytes(source)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT / "src")
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment["NO_COLOR"] = "1"
            environment["COLUMNS"] = "200"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "jsonl_viewer",
                    str(path),
                    "--session",
                    "standalone-session",
                    "--conversation",
                    "standalone-conversation",
                    "--conversation-label",
                    "Debate",
                    "--conversation-subject",
                    "Provider diagnostics",
                    "--agent",
                    "standalone-agent",
                    "--searchable-field",
                    "content",
                ],
                input="q\n",
                capture_output=True,
                text=True,
                env=environment,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("READ ONLY", completed.stdout)
            self.assertIn("standalone-session", completed.stdout)
            self.assertIn(
                "Debate: standalone-conversation — Provider diagnostics",
                completed.stdout,
            )
            self.assertIn('"content": "hello"', completed.stdout)
            self.assertNotIn("\x1b", completed.stdout)
            self.assertEqual(path.read_bytes(), source)

    def test_stdin_snapshot_renders_once_then_closes_on_eof(self) -> None:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "src")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["NO_COLOR"] = "1"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "jsonl_viewer",
                "-",
                "--session",
                "stdin-session",
                "--conversation",
                "stdin-conversation",
                "--agent",
                "stdin-agent",
                "--searchable-field",
                "content",
            ],
            input=b'{"content":"from stdin"}\n',
            capture_output=True,
            env=environment,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        output = completed.stdout.decode()
        self.assertIn("stdin-session", output)
        self.assertIn("Conversation: stdin-conversation", output)
        self.assertNotIn("Conversation: stdin-conversation —", output)
        self.assertIn('"content": "from stdin"', output)
        self.assertNotIn("\x1b", output)

    def test_bounded_reader_requests_only_one_byte_beyond_snapshot_limit(self) -> None:
        handle = mock.Mock(spec=io.BytesIO)
        handle.read.return_value = b"bounded"
        self.assertEqual(_read_bounded(handle), b"bounded")
        handle.read.assert_called_once_with(MAX_SOURCE_BYTES + 1)

    def test_interactive_terminal_restores_after_hosted_failure(self) -> None:
        class TerminalBuffer(io.StringIO):
            def isatty(self) -> bool:
                return True

            def fileno(self) -> int:
                return 17

        input_stream = TerminalBuffer()
        output_stream = TerminalBuffer()
        fake_termios = mock.Mock()
        fake_termios.TCSADRAIN = 1
        fake_termios.tcgetattr.return_value = ["saved"]
        fake_tty = mock.Mock()
        with (
            mock.patch.object(_standalone, "termios", fake_termios),
            mock.patch.object(_standalone, "tty", fake_tty),
            self.assertRaisesRegex(RuntimeError, "hosted failure"),
        ):
            with _TerminalHost(input_stream, output_stream, no_color=True):
                raise RuntimeError("hosted failure")

        fake_tty.setcbreak.assert_called_once_with(17)
        fake_termios.tcsetattr.assert_called_once_with(17, 1, ["saved"])
        output = output_stream.getvalue()
        self.assertTrue(output.startswith("\x1b[?1049h\x1b[?25l"))
        self.assertTrue(output.endswith("\x1b[?25h\x1b[?1049l"))

    def test_missing_source_is_content_safe_cli_error(self) -> None:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "src")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "jsonl_viewer",
                "/definitely/missing/viewer-input.jsonl",
                "--session",
                "s",
                "--conversation",
                "c",
                "--agent",
                "a",
            ],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("FileNotFoundError", completed.stderr)


class PackagingBoundaryTests(unittest.TestCase):
    def _modules(self) -> dict[str, Path]:
        package = ROOT / "src" / "jsonl_viewer"
        modules: dict[str, Path] = {}
        for path in sorted(package.rglob("*.py")):
            parts = list(path.relative_to(package).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            name = "jsonl_viewer" + ("." + ".".join(parts) if parts else "")
            modules[name] = path
        return modules

    def test_metadata_has_no_runtime_or_development_dependency(self) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        project = metadata["project"]
        self.assertEqual(project["name"], "jsonl-viewer")
        self.assertEqual(project["requires-python"], ">=3.11")
        self.assertEqual(project["dependencies"], [])
        self.assertEqual(project["license"], "MIT")
        self.assertNotIn("optional-dependencies", project)
        self.assertEqual(
            project["scripts"],
            {"jsonl-viewer": "jsonl_viewer._standalone:main"},
        )

    def test_inventory_edges_cycles_and_story_isolation(self) -> None:
        modules = self._modules()
        graph: dict[str, set[str]] = {module: set() for module in modules}
        external: set[str] = set()
        for module, path in modules.items():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            package = (
                module if path.name == "__init__.py" else module.rpartition(".")[0]
            )
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level:
                    dependency = importlib.util.resolve_name(
                        "." * node.level + (node.module or ""),
                        package,
                    )
                    if dependency in modules:
                        graph[module].add(dependency)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in modules:
                            graph[module].add(alias.name)
                        elif alias.name.startswith("story_writing_agents"):
                            external.add(alias.name)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.startswith("story_writing_agents"):
                        external.add(node.module)

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(module: str) -> None:
            if module in visiting:
                self.fail(f"internal cycle reaches {module}")
            if module in visited:
                return
            visiting.add(module)
            for dependency in graph[module]:
                visit(dependency)
            visiting.remove(module)
            visited.add(module)

        for module in graph:
            visit(module)
        self.assertEqual(len(modules), 8)
        self.assertEqual(sum(len(value) for value in graph.values()), 13)
        self.assertEqual(external, set())
        facade = (ROOT / "src/jsonl_viewer/__init__.py").read_text(encoding="utf-8")
        self.assertNotIn("_standalone", facade)

        index = (ROOT / "PYTHON_MODULE_INDEX.md").read_text(encoding="utf-8")
        indexed = set(re.findall(r"^### `([^`]+)`$", index, flags=re.MULTILINE))
        self.assertEqual(indexed, set(modules))
        self.assertIn("Importable production units indexed: 8.", index)
        self.assertIn("Direct internal dependency edges indexed: 13.", index)
        self.assertIn("Directed internal dependency cycles indexed: 0.", index)
        self.assertTrue(
            (
                ROOT / ".codex/agents/modularity_maintainer/PYTHON_MODULARITY_POLICY.md"
            ).is_file()
        )

    def test_mit_license_and_intentional_source_tree(self) -> None:
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertTrue(license_text.startswith("MIT License\n"))
        self.assertIn("Copyright (c) 2026 Kim's Developers Group", license_text)
        package_files = {
            path.relative_to(ROOT / "src/jsonl_viewer").as_posix()
            for path in (ROOT / "src/jsonl_viewer").iterdir()
            if path.is_file()
        }
        self.assertEqual(
            package_files,
            {
                "__init__.py",
                "__main__.py",
                "_input.py",
                "_model.py",
                "_render.py",
                "_standalone.py",
                "contracts.py",
                "engine.py",
                "py.typed",
            },
        )


if __name__ == "__main__":
    unittest.main()
