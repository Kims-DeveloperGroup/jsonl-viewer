"""Native clipboard acknowledgements remain local, bounded, and content safe."""

from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from jsonl_viewer import _clipboard


class ClipboardTests(unittest.TestCase):
    def test_local_helpers_receive_utf8_on_stdin_and_acknowledge_success(self):
        text = "한글 e\u0301\n$(do-not-execute) `literal`"
        for platform, environment, executables, expected in (
            ("darwin", {}, {"pbcopy": "/usr/bin/pbcopy"}, ["/usr/bin/pbcopy"]),
            ("darwin", {"TMUX": "/tmp/local-tmux,1,0", "STY": "local-screen"},
             {"pbcopy": "/usr/bin/pbcopy"}, ["/usr/bin/pbcopy"]),
            ("linux", {"WAYLAND_DISPLAY": "wayland-0"},
             {"wl-copy": "/usr/bin/wl-copy"}, ["/usr/bin/wl-copy"]),
            ("linux", {"DISPLAY": ":0"}, {"xclip": "/usr/bin/xclip"},
             ["/usr/bin/xclip", "-selection", "clipboard"]),
            ("linux", {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"},
             {"xclip": "/usr/bin/xclip"}, ["/usr/bin/xclip", "-selection", "clipboard"]),
        ):
            with self.subTest(platform=platform, environment=environment), \
                    mock.patch.object(_clipboard.sys, "platform", platform), \
                    mock.patch.dict(_clipboard.os.environ, environment, clear=True), \
                    mock.patch.object(_clipboard.shutil, "which", side_effect=executables.get), \
                    mock.patch.object(_clipboard.subprocess, "run",
                                      return_value=mock.Mock(returncode=0)) as run:
                self.assertTrue(_clipboard.copy_text(text))
                run.assert_called_once()
                self.assertEqual(run.call_args.args, (expected,))
                self.assertEqual(run.call_args.kwargs["input"], text.encode("utf-8"))
                self.assertIs(run.call_args.kwargs["shell"], False)
                self.assertEqual(run.call_args.kwargs["timeout"], 2)
                self.assertEqual(run.call_args.kwargs["stdout"], subprocess.DEVNULL)
                self.assertEqual(run.call_args.kwargs["stderr"], subprocess.DEVNULL)

    def test_failed_helper_timeout_and_invalid_unicode_never_acknowledge_copy(self):
        for result, error, text in (
            (1, None, "secret"),
            (0, OSError("helper failed"), "secret"),
            (0, subprocess.TimeoutExpired(["pbcopy"], 2), "secret"),
            (0, None, "invalid\ud800"),
        ):
            with self.subTest(result=result, error=error, text=ascii(text)), \
                    mock.patch.object(_clipboard.sys, "platform", "darwin"), \
                    mock.patch.dict(_clipboard.os.environ, {}, clear=True), \
                    mock.patch.object(_clipboard.shutil, "which", return_value="/usr/bin/pbcopy"), \
                    mock.patch.object(_clipboard.subprocess, "run", side_effect=error,
                                      return_value=mock.Mock(returncode=result)) as run:
                self.assertFalse(_clipboard.copy_text(text))
                if "\ud800" in text:
                    run.assert_not_called()

    def test_absent_or_remote_clipboards_do_not_launch_a_helper(self):
        for platform, environment, executable in (
            ("darwin", {}, None),
            ("linux", {}, "/usr/bin/wl-copy"),
            ("win32", {}, "/usr/bin/pbcopy"),
            ("darwin", {"SSH_CONNECTION": "remote"}, "/usr/bin/pbcopy"),
            ("darwin", {"SSH_TTY": "/dev/tty"}, "/usr/bin/pbcopy"),
            ("linux", {"MOSH_CONNECTION": "remote"}, "/usr/bin/wl-copy"),
        ):
            with self.subTest(platform=platform, environment=environment), \
                    mock.patch.object(_clipboard.sys, "platform", platform), \
                    mock.patch.dict(_clipboard.os.environ, environment, clear=True), \
                    mock.patch.object(_clipboard.shutil, "which", return_value=executable), \
                    mock.patch.object(_clipboard.subprocess, "run") as run:
                with self.assertRaises(_clipboard.ClipboardUnavailable):
                    _clipboard.copy_text("secret")
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
