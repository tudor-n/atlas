"""Per-OS folders and identity (dual-boot design §4, Plan decisions P10 and P11).

The shared root on D: holds only OS-neutral data. What belongs to one OS (integration state,
the status line passthrough, later the holding inbox and logs) lives in that OS's state folder;
the desktop shell's config lives in its config folder. The env overrides exist for tests and are
honoured by the Rust shell too. Nothing here creates a folder: callers create what they write.

Stdlib only.
"""
from __future__ import annotations

import functools
import os
import sys
import time
from pathlib import Path

LINUX_BOOT_ID = Path("/proc/sys/kernel/random/boot_id")


def os_name() -> str:
    """Return "windows" or "linux". Any other POSIX system follows the Linux rules."""
    return "windows" if sys.platform == "win32" else "linux"


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


def _localappdata() -> Path:
    return _env_path("LOCALAPPDATA") or Path.home() / "AppData" / "Local"


def _xdg(name: str, default: str) -> Path:
    """`${name:-~/default}`: an unset or empty variable falls back to the default."""
    return _env_path(name) or Path.home() / default


def state_dir() -> Path:
    """Per-OS hub state: $BRAINHUB_STATE_DIR, else ${XDG_DATA_HOME:-~/.local/share}/brainhub
    on Linux and %LOCALAPPDATA%\\BrainHub on Windows."""
    override = _env_path("BRAINHUB_STATE_DIR")
    if override:
        return override
    if sys.platform == "win32":
        return _localappdata() / "BrainHub"
    return _xdg("XDG_DATA_HOME", ".local/share") / "brainhub"


def config_dir() -> Path:
    """The desktop shell's config: $BRAINHUB_CONFIG_DIR, else ${XDG_CONFIG_HOME:-~/.config}/brainhub
    on Linux and %LOCALAPPDATA%\\BrainHub on Windows."""
    override = _env_path("BRAINHUB_CONFIG_DIR")
    if override:
        return override
    if sys.platform == "win32":
        return _localappdata() / "BrainHub"
    return _xdg("XDG_CONFIG_HOME", ".config") / "brainhub"


def _linux_boot_id() -> str:
    value = LINUX_BOOT_ID.read_text("ascii").strip()
    if not value:
        raise OSError(f"{LINUX_BOOT_ID} is empty")
    return value


def _boot_minute(now: float, uptime_ms: int) -> str:
    """The boot time (now minus uptime) in epoch minutes, rounded so the jitter between two
    readings never matters."""
    return f"win-{round((now - uptime_ms / 1000) / 60)}"


def _windows_boot_id() -> str:
    import ctypes
    tick = ctypes.windll.kernel32.GetTickCount64  # ms since boot, sleep and hibernation included
    tick.restype = ctypes.c_ulonglong
    return _boot_minute(time.time(), tick())


@functools.cache
def boot_id() -> str:
    """Identifies this boot of this OS: Linux's kernel boot id, or the Windows boot time to the
    minute. A row or lock from the other OS always has a different id. It never raises: when it
    can't be read it returns "unknown-<pid>", which callers treat as a fresh boot. Read once per
    process (a process never outlives its boot), so the Windows rounding can't flip between calls."""
    try:
        return _windows_boot_id() if sys.platform == "win32" else _linux_boot_id()
    except Exception:
        return f"unknown-{os.getpid()}"
