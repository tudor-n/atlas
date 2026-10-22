"""Fast Startup watch (dual-boot design §6.5 item 4).

Windows feature updates sometimes turn Fast Startup or hibernation back on. Either one leaves D:
hibernated on shutdown, so Linux can't use it safely. At start on Windows the hub reads the two
switches from the registry (read-only) and, if one is on, shows a quiet warning with the fix.
On Linux there is nothing to check. Stdlib only.
"""
from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

POWER_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Power"
VALUES = ("HiberbootEnabled", "HibernateEnabled")
WARNING = ("Fast Startup or hibernation is on again; run `powercfg /h off` as admin so Linux can "
           "use D: safely.")


def _read_power_values() -> dict[str, int]:
    """The power switches that exist under POWER_KEY, opened read-only."""
    import winreg
    out = {}
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, POWER_KEY, 0, winreg.KEY_READ) as key:
        for name in VALUES:
            try:
                value, kind = winreg.QueryValueEx(key, name)
            except FileNotFoundError:
                continue  # not set: off
            if kind == winreg.REG_DWORD:
                out[name] = int(value)
    return out


def fast_startup_warnings(read=None) -> list[str]:
    """[WARNING] when Fast Startup or hibernation is on, else []. Always [] off Windows, and on
    any error reading the registry (a warning that can't be checked is no warning)."""
    if sys.platform != "win32":
        return []
    try:
        values = (read or _read_power_values)()
    except Exception:
        log.warning("could not read the Fast Startup and hibernation settings", exc_info=True)
        return []
    return [WARNING] if any(values.get(name) == 1 for name in VALUES) else []
