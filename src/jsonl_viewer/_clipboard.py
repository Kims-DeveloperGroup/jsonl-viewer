"""Bounded, explicit writes to an existing local desktop clipboard."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


class ClipboardUnavailable(NotImplementedError):
    """No acknowledged local clipboard transport is available."""


def copy_text(text: str) -> bool:
    """Send UTF-8 on stdin only; successful helper exit acknowledges acceptance."""
    if any(os.environ.get(name) for name in (
        "SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY", "MOSH_CONNECTION", "MOSH_KEY",
    )):
        raise ClipboardUnavailable("Clipboard unavailable.")
    command = None
    if sys.platform == "darwin":
        executable = shutil.which("pbcopy")
        if executable:
            command = [executable]
    elif sys.platform.startswith("linux"):
        executable = shutil.which("wl-copy") if os.environ.get("WAYLAND_DISPLAY") else None
        if executable:
            command = [executable]
        elif os.environ.get("DISPLAY") and (executable := shutil.which("xclip")):
            command = [executable, "-selection", "clipboard"]
    if command is None:
        raise ClipboardUnavailable("Clipboard unavailable.")
    try:
        result = subprocess.run(command, input=text.encode("utf-8"), stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, shell=False, timeout=2, check=False)
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return False
    return result.returncode == 0
