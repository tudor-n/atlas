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
    """`${name:-~/default}`: an unset or empty variable falls back to the default, and so does a
    relative one (the XDG spec says to ignore those)."""
    value = _env_path(name)
    return value if value is not None and value.is_absolute() else Path.home() / default


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


WINDOWS_BOOT_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters"


def _boot_minute(now: float, uptime_ms: int) -> str:
    """The boot time (now minus uptime) in epoch minutes. Only a fallback: a boot time near a
    minute edge can round differently in two processes, as the wall clock drifts against the
    tick count."""
    return f"win-{round((now - uptime_ms / 1000) / 60)}"


def _windows_boot_counter() -> int:
    """Windows' own boot counter (a DWORD bumped at every boot), read-only from the registry."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, WINDOWS_BOOT_KEY) as key:
        value, kind = winreg.QueryValueEx(key, "BootId")
    if kind != winreg.REG_DWORD:
        raise OSError("BootId is not a DWORD")
    return int(value)


def _windows_boot_id() -> str:
    try:
        return f"win-boot-{_windows_boot_counter()}"
    except OSError:
        pass
    import ctypes
    tick = ctypes.windll.kernel32.GetTickCount64  # ms since boot, sleep and hibernation included
    tick.restype = ctypes.c_ulonglong
    return _boot_minute(time.time(), tick())


@functools.cache
def boot_id() -> str:
    """Identifies this boot of this OS: Linux's kernel boot id, or Windows' registry boot counter
    (the boot time to the minute if that can't be read). A row or lock from the other OS always
    has a different id. It never raises: when it can't be read it returns "unknown-<pid>", which
    callers treat as a fresh boot. Read once per process: a process never outlives its boot."""
    try:
        return _windows_boot_id() if sys.platform == "win32" else _linux_boot_id()
    except Exception:
        return f"unknown-{os.getpid()}"
