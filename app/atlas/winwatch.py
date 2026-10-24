"""Fast Startup watch (dual-boot design §6.5 item 4).

Windows feature updates sometimes turn hibernation back on, and with it Fast Startup. Either
leaves D: hibernated (NTFS dirty) on shutdown, so Linux can't use it safely. Hibernation is the
switch that matters: with it off Fast Startup can't run, even when `HiberbootEnabled` still says 1
(`powercfg /h off` leaves that alone, so a warning keyed on it would never go away). At start on
Windows the hub reads the hibernation switch from the registry (read-only) and, if it is on, shows
a quiet warning with the fix. On Linux there is nothing to check. Stdlib only.
"""
from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

POWER_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Power"
# HibernateEnabled, or the system's default when it isn't set.
VALUES = ("HibernateEnabled", "HibernateEnabledDefault")
WARNING = ("Hibernation (and with it Fast Startup) is on again; run `powercfg /h off` as admin so "
           "Linux can use D: safely.")


def _read_power_values() -> dict[str, int]:
    """The power switches that exist under POWER_KEY, opened read-only."""
    import winreg
    out = {}
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, POWER_KEY, 0, winreg.KEY_READ) as key:
        for name in VALUES:
            try:
                value, kind = winreg.QueryValueEx(key, name)
            except FileNotFoundError:
                continue  # not set (_hibernation_on reads that as the default, or off)
            if kind == winreg.REG_DWORD:
                out[name] = int(value)
    return out


def _hibernation_on(values: dict[str, int]) -> bool:
    """HibernateEnabled decides; when it isn't set, HibernateEnabledDefault does."""
    value = values.get("HibernateEnabled", values.get("HibernateEnabledDefault"))
    return value == 1


def fast_startup_warnings(read=None) -> list[str]:
    """[WARNING] when hibernation (which Fast Startup needs) is on, else []. Always [] off Windows,
    and on any error reading the registry (a warning that can't be checked is no warning)."""
    if sys.platform != "win32":
        return []
    try:
        values = (read or _read_power_values)()
    except Exception:
        log.warning("could not read the Fast Startup and hibernation settings", exc_info=True)
        return []
    return [WARNING] if _hibernation_on(values) else []
