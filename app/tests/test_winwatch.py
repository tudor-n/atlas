"""Fast Startup watch (dual-boot design §6.5 item 4): a quiet warning when Windows turned
hibernation (and with it Fast Startup) back on; nothing to check on Linux; an unreadable registry
is no warning."""
import sys
from types import SimpleNamespace

import pytest

from atlas import winwatch

win_only = pytest.mark.skipif(sys.platform != "win32", reason="reads the Windows registry")


@pytest.fixture
def on_windows(monkeypatch):
    monkeypatch.setattr(winwatch, "sys", SimpleNamespace(platform="win32"))


def test_hibernation_on_warns(on_windows):
    for values in ({"HibernateEnabled": 1}, {"HibernateEnabled": 1, "HibernateEnabledDefault": 0},
                   {"HibernateEnabledDefault": 1}):  # not set: the system default decides
        assert winwatch.fast_startup_warnings(lambda: values) == [winwatch.WARNING], values
    assert "powercfg /h off" in winwatch.WARNING
    assert "Fast Startup" in winwatch.WARNING and "Hibernation" in winwatch.WARNING


def test_hibernation_off_is_quiet(on_windows):
    # After `powercfg /h off` HibernateEnabled is 0, and Fast Startup can't run even when
    # HiberbootEnabled still says 1: the warning goes away.
    for values in ({"HibernateEnabled": 0}, {"HibernateEnabled": 0, "HibernateEnabledDefault": 1},
                   {"HibernateEnabledDefault": 0}, {}, {"HiberbootEnabled": 1, "HibernateEnabled": 0}):
        assert winwatch.fast_startup_warnings(lambda: values) == [], values
    assert "HiberbootEnabled" not in winwatch.VALUES


def test_registry_errors_give_no_warning(on_windows):
    def broken():
        raise OSError("access denied")
    assert winwatch.fast_startup_warnings(broken) == []


@pytest.mark.skipif(sys.platform == "win32", reason="the non-Windows side")
def test_nothing_to_check_off_windows():
    assert winwatch.fast_startup_warnings(lambda: {"HibernateEnabled": 1}) == []
    assert winwatch.fast_startup_warnings() == []


@win_only
def test_reads_the_real_power_values_read_only():
    values = winwatch._read_power_values()
    assert set(values) <= set(winwatch.VALUES) and all(type(v) is int for v in values.values())
    assert winwatch.fast_startup_warnings() in ([], [winwatch.WARNING])


async def test_warning_shown_in_state_and_widget(aiohttp_client, paths, monkeypatch):
    from atlas import server
    from tests.test_server import make_runner
    monkeypatch.setattr(server.winwatch, "fast_startup_warnings", lambda: [winwatch.WARNING])
    c = await aiohttp_client(server.create_app(paths, runner=make_runner(), start_workers=False))
    assert (await (await c.get("/api/state")).json())["warnings"] == [winwatch.WARNING]
    widget = await (await c.get("/api/widget")).json()
    assert widget["warnings"] == [winwatch.WARNING] and widget["drive"] is None
