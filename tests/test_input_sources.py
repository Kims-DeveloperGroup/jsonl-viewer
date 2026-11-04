"""Deterministic desktop-source lifecycle and platform-boundary regressions."""

from __future__ import annotations

import io
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

from jsonl_viewer._input_sources import InputSourceLifecycle, InputSourceState
from jsonl_viewer import _input_sources_linux as linux_sources
from jsonl_viewer import _input_sources_macos as macos_sources
from jsonl_viewer import _terminal


class _MemoryAdapter:
    def __init__(self, initial, english=None):
        self.current = initial
        self.english = english or InputSourceState(("en-US",), True)
        self.selections = []
        self.reads = 0
        self.english_reads = 0
        self.selection_failure = None
        self.read_failure = None

    def current_source(self):
        self.reads += 1
        if self.read_failure is not None:
            failure, self.read_failure = self.read_failure, None
            raise failure
        return self.current

    def english_source(self):
        self.english_reads += 1
        return self.english

    def select_source(self, source):
        self.selections.append(source)
        self.current = source
        if self.selection_failure is not None:
            failure, self.selection_failure = self.selection_failure, None
            raise failure


class InputSourceLifecycleTests(unittest.TestCase):
    def test_command_text_and_close_restore_one_exact_entry_source(self):
        entry = InputSourceState(("ko",), False)
        manual = InputSourceState(("ja",), False)
        adapter = _MemoryAdapter(entry)
        factory = mock.Mock(return_value=adapter)
        owner = InputSourceLifecycle(factory)
        input_stream, output_stream = io.StringIO(), io.StringIO()
        factory.assert_not_called()
        owner.start(input_stream, output_stream, live=True)
        factory.assert_called_once_with(input_stream, output_stream)
        self.assertEqual(adapter.selections, [])
        owner.set_input_mode("command")
        self.assertEqual(adapter.current, adapter.english)
        adapter.current = manual
        reads = adapter.reads
        owner.set_input_mode("command")
        self.assertEqual(adapter.current, manual)
        self.assertEqual(adapter.reads, reads)
        owner.set_input_mode("text")
        self.assertEqual(adapter.current, entry)
        adapter.current = manual
        owner.set_input_mode("command")
        self.assertEqual(adapter.current, adapter.english)
        owner.set_input_mode("text")
        self.assertEqual(adapter.current, entry)
        owner.stop()
        before = list(adapter.selections)
        owner.stop()
        self.assertEqual(adapter.selections, before)
        self.assertEqual(adapter.selections, [adapter.english, entry, adapter.english, entry])
        self.assertEqual(adapter.english_reads, 1)

    def test_english_noop_and_external_changes_use_exact_entry_identity(self):
        entry = InputSourceState(("en-GB",), True)
        adapter = _MemoryAdapter(entry)
        owner = InputSourceLifecycle(lambda *_: adapter)
        owner.start(io.StringIO(), io.StringIO(), live=True)
        owner.set_input_mode("command")
        self.assertEqual(adapter.selections, [])
        adapter.current = InputSourceState(("ko",), False)
        owner.set_input_mode("text")
        self.assertEqual(adapter.current, entry)
        owner.set_input_mode("command")
        self.assertEqual(adapter.selections, [entry])
        adapter.current = InputSourceState(("ja",), False)
        owner.stop()
        self.assertEqual(adapter.current, entry)
        self.assertEqual(adapter.selections, [entry, entry])

    def test_uncertain_selection_keeps_restoration_responsibility(self):
        entry = InputSourceState(("ko",), False)
        adapter = _MemoryAdapter(entry)
        owner = InputSourceLifecycle(lambda *_: adapter)
        owner.start(io.StringIO(), io.StringIO(), live=True)
        adapter.selection_failure = TimeoutError("selection outcome is uncertain")
        owner.set_input_mode("command")
        self.assertEqual(adapter.current, adapter.english)
        adapter.read_failure = OSError("post-selection source observation failed")
        owner.set_input_mode("text")
        self.assertEqual(adapter.current, entry)
        owner.set_input_mode("command")
        adapter.selection_failure = TimeoutError("cleanup selection failed")
        adapter.read_failure = OSError("cleanup source observation failed")
        owner.stop()
        self.assertEqual(adapter.current, entry)
        self.assertEqual(adapter.selections, [adapter.english, entry, adapter.english, entry])

    def test_unsupported_and_failed_discovery_never_select_a_source(self):
        missing = mock.Mock()
        missing.current_source.return_value = None
        factories = (mock.Mock(return_value=None), mock.Mock(return_value=missing),
                     mock.Mock(side_effect=OSError("desktop unavailable")))
        for factory in factories:
            with self.subTest(factory=factory):
                owner = InputSourceLifecycle(factory)
                owner.start(io.StringIO(), io.StringIO(), live=True)
                owner.set_input_mode("command")
                owner.set_input_mode("text")
                owner.stop()
        missing.select_source.assert_not_called()
        factory = mock.Mock(side_effect=AssertionError("fallback must not discover desktop"))
        owner = InputSourceLifecycle(factory)
        owner.start(io.StringIO(), io.StringIO(), live=False)
        owner.set_input_mode("command")
        owner.set_input_mode("text")
        owner.stop()
        factory.assert_not_called()


def _cached_macos_source(initial):
    native = SimpleNamespace(actual=initial, cached=initial, events=[],
                             pointer=mock.Mock(), tis=mock.Mock(), cf=mock.Mock())
    native.pointer.in_dll.side_effect = lambda _library, name: SimpleNamespace(
        value=101 if name == "kCFRunLoopDefaultMode" else 1)
    with mock.patch.object(macos_sources.ctypes, "CDLL", side_effect=[native.tis, native.cf]), \
            mock.patch.object(macos_sources.ctypes, "c_void_p", native.pointer):
        native.adapter = macos_sources.MacOSInputSourceAdapter()

    def refresh(mode, seconds, return_after):
        native.events.append(("refresh", mode, seconds, return_after))
        native.cached = native.actual
        return 3

    def current():
        native.events.append(("read", native.cached))
        return native.cached

    def select(source):
        native.events.append(("select", source))
        native.actual = source

    native.cf.CFRunLoopRunInMode.side_effect = refresh
    native.tis.TISCopyCurrentKeyboardInputSource.side_effect = current
    native.cf.CFRelease.side_effect = lambda source: native.events.append(("release", source))
    native.adapter._state = lambda source: source
    native.adapter.english_source = mock.Mock(return_value=InputSourceState(("ABC",), True))
    native.adapter.select_source = mock.Mock(side_effect=select)
    return native


class MacOSInputSourceTests(unittest.TestCase):
    def test_external_source_changes_refresh_before_noop_and_restore_decisions(self):
        english, korean = InputSourceState(("ABC",), True), InputSourceState(("KO",), False)
        for entry, external in ((english, korean), (korean, english)):
            with self.subTest(entry=entry):
                native = _cached_macos_source(entry)
                owner = InputSourceLifecycle(lambda *_: native.adapter)
                owner.start(io.StringIO(), io.StringIO(), live=True)
                native.actual = external
                self.assertEqual(native.cached, entry)
                owner.set_input_mode("command")
                self.assertEqual(native.actual, english)
                if entry.english:
                    native.adapter.select_source.assert_called_once_with(english)
                    native.actual = korean
                    count = len(native.events)
                    owner.set_input_mode("command")
                    self.assertEqual(len(native.events), count)
                    self.assertEqual(native.actual, korean)
                else:
                    native.adapter.select_source.assert_not_called()
                owner.set_input_mode("text")
                self.assertEqual(native.actual, entry)
                owner.stop()
                for offset, event in enumerate(native.events):
                    if event[0] == "read":
                        self.assertEqual(native.events[offset - 1], ("refresh", 101, 0.0, False))
                        self.assertEqual(native.events[offset + 1], ("release", event[1]))
                self.assertNotIn(mock.call(101), native.cf.CFRelease.call_args_list)

    def test_refresh_abi_failure_and_source_conversion_release_are_bounded(self):
        entry = InputSourceState(("KO",), False)
        native = _cached_macos_source(entry)
        refresh = native.cf.CFRunLoopRunInMode
        self.assertEqual(refresh.argtypes, [native.pointer, macos_sources.ctypes.c_double,
                                           macos_sources.ctypes.c_bool])
        self.assertIs(refresh.restype, macos_sources.ctypes.c_int32)
        refresh.side_effect = OSError("refresh failed")
        with self.assertRaisesRegex(OSError, "refresh failed"):
            native.adapter.current_source()
        native.tis.TISCopyCurrentKeyboardInputSource.assert_not_called()
        native.cf.CFRelease.assert_not_called()
        refresh.side_effect = lambda *_: None
        native.adapter._state = mock.Mock(side_effect=ValueError("source conversion failed"))
        with self.assertRaisesRegex(ValueError, "source conversion failed"):
            native.adapter.current_source()
        native.cf.CFRelease.assert_called_once_with(entry)

    def test_native_language_and_ascii_capability_jointly_qualify_english(self):
        native = _cached_macos_source(InputSourceState(("KO",), False))
        adapter = native.adapter
        adapter._state = macos_sources.MacOSInputSourceAdapter._state.__get__(adapter)
        values = {"InputSourceID": "source-id", "InputSourceLanguages": []}
        flags = {"InputSourceIsEnabled": True, "InputSourceIsSelectCapable": True,
                 "InputSourceIsASCIICapable": True}
        adapter._property = lambda _source, name: values[name]
        adapter._boolean = lambda _source, name: flags[name]
        adapter._string = lambda value: value
        native.cf.CFArrayGetCount.side_effect = len
        native.cf.CFArrayGetValueAtIndex.side_effect = lambda values, index: values[index]
        for language, ascii_capable, english in (("ko", True, False), ("en-US", True, True),
                                                 ("en_GB", True, True), ("en-US", False, False)):
            with self.subTest(language=language, ascii_capable=ascii_capable):
                values["InputSourceLanguages"] = [language]
                flags["InputSourceIsASCIICapable"] = ascii_capable
                self.assertEqual(adapter._state(1), InputSourceState(("source-id",), english))
        flags["InputSourceIsEnabled"] = False
        self.assertIsNone(adapter._state(1))

    def test_english_lookup_and_selection_release_owned_native_references(self):
        entry, english = InputSourceState(("KO",), False), InputSourceState(("ABC",), True)
        native = _cached_macos_source(entry)
        adapter = native.adapter
        adapter.english_source = macos_sources.MacOSInputSourceAdapter.english_source.__get__(adapter)
        native.cf.CFStringCreateWithCString.return_value = 201
        native.tis.TISCopyInputSourceForLanguage.return_value = english
        self.assertEqual(adapter.english_source(), english)
        self.assertEqual(native.cf.CFRelease.call_args_list, [mock.call(entry), mock.call(english), mock.call(201)])
        native.cf.CFRelease.reset_mock()
        adapter.select_source = macos_sources.MacOSInputSourceAdapter.select_source.__get__(adapter)
        native.tis.TISCreateInputSourceList.return_value = 301
        native.cf.CFArrayGetCount.return_value = 1
        native.cf.CFArrayGetValueAtIndex.return_value = english
        native.tis.TISSelectInputSource.return_value = 0
        adapter.select_source(english)
        native.tis.TISSelectInputSource.assert_called_once_with(english)
        native.cf.CFRelease.assert_called_once_with(301)
        native.cf.CFRelease.reset_mock()
        native.cf.CFArrayGetCount.return_value = 0
        with self.assertRaisesRegex(OSError, "no longer enabled"):
            adapter.select_source(english)
        native.cf.CFRelease.assert_called_once_with(301)


class LinuxInputSourceTests(unittest.TestCase):
    def test_ibus_prefers_enabled_english_and_rejects_missing_restore_engine(self):
        engines = "hangul\nxkb:kr::kor\nxkb:gb::eng\nxkb:us::eng\nbad engine\n"
        answers = {("engine",): "hangul", ("list-engine", "--name-only"): engines}
        with mock.patch.object(linux_sources, "_run_helper", side_effect=lambda _exe, *args: answers.get(args, "")) as helper:
            adapter = linux_sources.IBusInputSourceAdapter("/tool/ibus")
            self.assertEqual(adapter.current_source(), InputSourceState(("ibus", "hangul"), False))
            english = adapter.english_source()
            self.assertEqual(english, InputSourceState(("ibus", "xkb:us::eng"), True))
            adapter.select_source(english)
            helper.assert_any_call("/tool/ibus", "engine", "xkb:us::eng")
            with self.assertRaisesRegex(OSError, "unavailable"):
                adapter.select_source(InputSourceState(("ibus", "missing"), False))
            self.assertNotIn(mock.call("/tool/ibus", "engine", "missing"), helper.call_args_list)

    def test_fcitx_roundtrip_preserves_group_engine_and_activation_state(self):
        profile = ("[Groups/0]\nName=Default\nDefault Layout=us\n"
                   "[Groups/0/Items/0]\nName=keyboard-us\n[Groups/0/Items/1]\nName=hangul\n")
        current = {"active": "2", "group": "Default", "name": "hangul"}

        def reply(executable, *arguments):
            self.assertEqual((executable, arguments[0]), ("/tool/fcitx5-remote", "--check"))
            action = arguments[1:]
            if action == ("-c",):
                current.update(active="1", name="keyboard-us")
            elif action == ("-s", "hangul"):
                current.update(active="2", name="hangul")
            elif action == ("-o",):
                current["active"] = "2"
            return {(): current["active"], ("-q",): current["group"],
                    ("-n",): current["name"]}.get(action, "")

        with mock.patch.dict(linux_sources.os.environ, {"XDG_CONFIG_HOME": "/virtual/ime"}), \
                mock.patch.object(linux_sources.Path, "open", mock.mock_open(read_data=profile)), \
                mock.patch.object(linux_sources, "_run_helper", side_effect=reply) as helper:
            adapter = linux_sources.Fcitx5InputSourceAdapter("/tool/fcitx5-remote")
            entry, english = adapter.current_source(), adapter.english_source()
            self.assertEqual(entry, InputSourceState(("fcitx5", "Default", "hangul", "2"), False))
            self.assertEqual(english, InputSourceState(("fcitx5", "Default", "keyboard-us", "1"), True))
            adapter.select_source(english)
            self.assertEqual(adapter.current_source(), english)
            adapter.select_source(entry)
            self.assertEqual(adapter.current_source(), entry)
            adapter.select_source(english)
            adapter.select_source(InputSourceState((*english.identity[:3], "2"), True))
            helper.assert_any_call("/tool/fcitx5-remote", "--check", "-o")
            current["active"] = "0"
            helper.reset_mock()
            self.assertIsNone(adapter.current_source())
            self.assertEqual(helper.call_args_list, [mock.call("/tool/fcitx5-remote", "--check")])

    def test_framework_selection_rejects_ambiguity_and_missing_explicit_tools(self):
        cases = (({}, {"ibus": "/tool/ibus", "fcitx5-remote": "/tool/fcitx5"}),
                 ({"GTK_IM_MODULE": "ibus"}, {"ibus": None, "fcitx5-remote": "/tool/fcitx5"}),
                 ({}, {"ibus": None, "fcitx5-remote": None}))
        for environment, installed in cases:
            with self.subTest(environment=environment, installed=installed), \
                    mock.patch.dict(linux_sources.os.environ, environment, clear=True), \
                    mock.patch.object(linux_sources.shutil, "which", side_effect=installed.get), \
                    mock.patch.object(linux_sources, "_run_helper") as helper:
                self.assertIsNone(linux_sources.linux_input_source_adapter())
                helper.assert_not_called()
        with mock.patch.dict(linux_sources.os.environ, {"GTK_IM_MODULE": "ibus", "QT_IM_MODULE": "fcitx"}, clear=True), \
                mock.patch.object(linux_sources.shutil, "which") as which:
            self.assertIsNone(linux_sources.linux_input_source_adapter())
            which.assert_not_called()
        with mock.patch.dict(linux_sources.os.environ, {"GTK_IM_MODULE": "ibus"}, clear=True), \
                mock.patch.object(linux_sources.shutil, "which", side_effect=lambda name: "/tool/ibus" if name == "ibus" else None), \
                mock.patch.object(linux_sources, "_run_helper") as helper:
            self.assertIsInstance(linux_sources.linux_input_source_adapter(), linux_sources.IBusInputSourceAdapter)
            helper.assert_not_called()

    def test_helper_output_timeout_and_failures_reap_and_close_the_process(self):
        real_popen = subprocess.Popen
        processes = []

        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        cases = (("print('hangul')", None),
                 ("import os; os.write(1, b'x' * 65537)", OSError),
                 ("import time; time.sleep(1)", subprocess.TimeoutExpired),
                 ("import os; os.write(1, b'\\xff')", UnicodeDecodeError),
                 ("raise SystemExit(3)", subprocess.CalledProcessError))
        with mock.patch.object(linux_sources.subprocess, "Popen", side_effect=spawn):
            for program, failure in cases:
                with self.subTest(program=program):
                    if failure is None:
                        self.assertEqual(linux_sources._run_helper(sys.executable, "-c", program), "hangul")
                    else:
                        with self.assertRaises(failure):
                            linux_sources._run_helper(sys.executable, "-c", program)
                    self.assertIsNotNone(processes[-1].poll())
                    self.assertTrue(processes[-1].stdout.closed)


class DesktopEligibilityTests(unittest.TestCase):
    def test_only_local_foreground_same_tty_desktops_are_eligible(self):
        input_stream, output_stream = mock.Mock(), mock.Mock()
        for stream in (input_stream, output_stream):
            stream.isatty.return_value = True
            stream.fileno.return_value = 17
        with mock.patch.object(_terminal.os, "isatty", return_value=True), \
                mock.patch.object(_terminal.os, "ttyname", return_value="/dev/tty-local"), \
                mock.patch.object(_terminal.os, "tcgetpgrp", return_value=101) as foreground, \
                mock.patch.object(_terminal.os, "getpgrp", return_value=101):
            for platform, environment in (("darwin", {"TERM_PROGRAM": "iTerm.app"}),
                                          ("linux", {"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/virtual/bus"})):
                with self.subTest(platform=platform), mock.patch.object(_terminal.sys, "platform", platform), \
                        mock.patch.dict(_terminal.os.environ, environment, clear=True):
                    self.assertTrue(_terminal._local_desktop_terminal(input_stream, output_stream))
                    for name in ("SSH_CONNECTION", "MOSH_CONNECTION", "TMUX", "STY", "WSL_INTEROP"):
                        with mock.patch.dict(_terminal.os.environ, {name: "present"}):
                            self.assertFalse(_terminal._local_desktop_terminal(input_stream, output_stream))
                    foreground.return_value = 102
                    self.assertFalse(_terminal._local_desktop_terminal(input_stream, output_stream))
                    foreground.return_value = 101
            with mock.patch.object(_terminal.sys, "platform", "darwin"), \
                    mock.patch.dict(_terminal.os.environ, {}, clear=True):
                self.assertFalse(_terminal._local_desktop_terminal(input_stream, output_stream))
        self.assertFalse(_terminal._local_desktop_terminal(io.StringIO(), io.StringIO()))


if __name__ == "__main__":
    unittest.main()
