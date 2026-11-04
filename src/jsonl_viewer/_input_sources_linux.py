"""Control an existing IBus or Fcitx5 desktop using bounded local helpers."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import time

from ._input_sources import InputSourceAdapter, InputSourceState


_HELPER_TIMEOUT_SECONDS = 0.25
_MAX_HELPER_OUTPUT = 65_536
_ENGINE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_:().+@-]{0,255}\Z")


def _run_helper(executable: str, *arguments: str) -> str:
    """Bound both helper duration and buffered output before UTF-8 decoding."""
    command = [executable, *arguments]
    deadline = time.monotonic() + _HELPER_TIMEOUT_SECONDS
    process = subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert process.stdout is not None
    output = bytearray()
    try:
        descriptor = process.stdout.fileno()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([descriptor], [], [], remaining)[0]:
                raise subprocess.TimeoutExpired(command, _HELPER_TIMEOUT_SECONDS)
            block = os.read(descriptor, min(8192, _MAX_HELPER_OUTPUT + 1 - len(output)))
            if not block:
                break
            output.extend(block)
            if len(output) > _MAX_HELPER_OUTPUT:
                raise OSError("input-method helper returned too much output")
        status = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        if status:
            raise subprocess.CalledProcessError(status, command)
        return output.decode("utf-8", errors="strict").strip()
    finally:
        try:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=_HELPER_TIMEOUT_SECONDS)
        finally:
            process.stdout.close()


def _english_ibus_engine(name: str) -> bool:
    return name.startswith("xkb:") and name.rsplit(":", 1)[-1] == "eng"


class IBusInputSourceAdapter:
    def __init__(self, executable: str) -> None:
        self._executable = executable

    def current_source(self) -> InputSourceState | None:
        name = _run_helper(self._executable, "engine")
        if _ENGINE_NAME.fullmatch(name) is None:
            return None
        return InputSourceState(("ibus", name), _english_ibus_engine(name))

    def _engines(self) -> tuple[str, ...]:
        output = _run_helper(self._executable, "list-engine", "--name-only")
        return tuple(name for name in output.splitlines() if _ENGINE_NAME.fullmatch(name))

    def english_source(self) -> InputSourceState | None:
        current = self.current_source()
        if current is not None and current.english:
            return current
        engines = self._engines()
        if "xkb:us::eng" in engines:
            return InputSourceState(("ibus", "xkb:us::eng"), True)
        return next((InputSourceState(("ibus", name), True)
                     for name in sorted(engines) if _english_ibus_engine(name)), None)

    def select_source(self, source: InputSourceState) -> None:
        if (len(source.identity) != 2 or source.identity[0] != "ibus"
                or source.identity[1] not in self._engines()):
            raise OSError("saved IBus input source is unavailable")
        _run_helper(self._executable, "engine", source.identity[1])


def _fcitx_english_keyboard(name: str) -> bool:
    return name in {"keyboard-us", "keyboard-gb"}


@dataclass(frozen=True, slots=True)
class _FcitxGroup:
    members: tuple[str, ...]
    english_keyboards: frozenset[str]


def _fcitx_groups() -> dict[str, _FcitxGroup]:
    """Read the bounded framework profile without changing its defaults."""
    config_root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    if not config_root.is_absolute():
        return {}
    try:
        with (config_root / "fcitx5" / "profile").open("r", encoding="utf-8") as stream:
            text = stream.read(_MAX_HELPER_OUTPUT + 1)
        if len(text) > _MAX_HELPER_OUTPUT:
            return {}
        parser = configparser.RawConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read_string(text)
        groups = {}
        for section in parser.sections():
            if re.fullmatch(r"Groups/[0-9]+", section) is None:
                continue
            name = parser.get(section, "Name", fallback="")
            if not name or any(ord(character) < 32 for character in name):
                continue
            members = []
            english_keyboards = set()
            default_layout = parser.get(section, "Default Layout", fallback="")
            offset = 0
            while parser.has_section(f"{section}/Items/{offset}"):
                member_section = f"{section}/Items/{offset}"
                member = parser.get(member_section, "Name", fallback="")
                if _ENGINE_NAME.fullmatch(member) is None:
                    members = []
                    break
                members.append(member)
                layout = parser.get(member_section, "Layout", fallback="") or default_layout
                if _fcitx_english_keyboard(member) and layout in {"us", "gb"}:
                    english_keyboards.add(member)
                offset += 1
            if name in groups:
                return {}
            if members:
                groups[name] = _FcitxGroup(tuple(members), frozenset(english_keyboards))
        return groups
    except (OSError, UnicodeError, configparser.Error):
        return {}


class Fcitx5InputSourceAdapter:
    def __init__(self, executable: str) -> None:
        self._executable = executable
        self._groups = _fcitx_groups()

    def _run(self, *arguments: str) -> str:
        # --check avoids activating an inactive daemon through D-Bus.
        return _run_helper(self._executable, "--check", *arguments)

    def current_source(self) -> InputSourceState | None:
        active = self._run()
        if active not in {"1", "2"}:
            return None
        group, name = self._run("-q"), self._run("-n")
        configured = self._groups.get(group)
        if (not group or any(ord(character) < 32 for character in group)
                or _ENGINE_NAME.fullmatch(name) is None or configured is None
                or name not in configured.members
                or (active == "1" and name != configured.members[0])):
            return None
        return InputSourceState(
            ("fcitx5", group, name, active), name in configured.english_keyboards,
        )

    def english_source(self) -> InputSourceState | None:
        current = self.current_source()
        if current is None or current.english:
            return current
        group = current.identity[1]
        configured = self._groups[group]
        if configured.members[0] not in configured.english_keyboards:
            return None
        # Deactivate to the group's English keyboard while retaining the IME
        # remembered by the framework. Reopening can then restore it exactly.
        return InputSourceState(("fcitx5", group, configured.members[0], "1"), True)

    def select_source(self, source: InputSourceState) -> None:
        identity = source.identity
        if len(identity) != 4 or identity[0] != "fcitx5":
            raise ValueError("invalid Fcitx5 input-source identity")
        _kind, group, name, active = identity
        configured = self._groups.get(group)
        members = configured.members if configured is not None else ()
        if (active not in {"1", "2"} or name not in members
                or (active == "1" and name != members[0])):
            raise OSError("saved Fcitx5 input source is unavailable")
        current = self.current_source()
        if current is None:
            raise OSError("Fcitx5 has no active desktop input context")
        if current.identity[1] != group:
            self._run("-g", group)
            current = self.current_source()
            if current is None or current.identity[1] != group:
                raise OSError("Fcitx5 group selection was not accepted")
        if active == "1":
            self._run("-c")
        else:
            if current.identity[2] != name:
                self._run("-s", name)
                current = self.current_source()
            if current is None or current.identity[3] != "2":
                self._run("-o")


def linux_input_source_adapter() -> InputSourceAdapter | None:
    """Prefer an explicit framework, otherwise require a unique installed one."""
    configured = set()
    for key in ("GTK_IM_MODULE", "QT_IM_MODULE", "XMODIFIERS"):
        value = os.environ.get(key, "").lower()
        if value in {"ibus", "@im=ibus"}:
            configured.add("ibus")
        if value in {"fcitx", "fcitx5", "@im=fcitx", "@im=fcitx5"}:
            configured.add("fcitx5")
    if len(configured) > 1:
        return None
    executables = {"ibus": shutil.which("ibus"), "fcitx5": shutil.which("fcitx5-remote")}
    candidates = configured or {name for name, executable in executables.items() if executable}
    if len(candidates) != 1:
        return None
    framework = next(iter(candidates))
    executable = executables[framework]
    if executable is None:
        return None
    if framework == "ibus":
        return IBusInputSourceAdapter(executable)
    return Fcitx5InputSourceAdapter(executable)
