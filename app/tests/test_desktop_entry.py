"""desktop-entry (phase 4.5, design §6): the launcher's .desktop file and icon. Temp folders only:
every test passes a tmp_path data home (and conftest points XDG_DATA_HOME at a temp folder too)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from atlas import desktop_entry as de

APP_DIR = Path(__file__).resolve().parents[1]

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="the launcher entry is Linux only for now")


def fake_shell(folder: Path, name: str = "atlas-desktop") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / name
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)
    return exe


def cli(*args, cwd=APP_DIR):
    return subprocess.run([sys.executable, "-m", "atlas", "desktop-entry", *map(str, args)], cwd=cwd,
                          capture_output=True, text=True, timeout=60)


def unquote_exec(value: str) -> list[str]:
    """The Exec value's arguments, decoded as the Desktop Entry spec says: first the string
    escapes (`\\\\` → `\\`), then quoting, then `%%` → `%`."""
    value = value.replace("\\\\", "\\")
    args, cur, i, quoted, has = [], "", 0, False, False
    while i < len(value):
        c = value[i]
        if quoted:
            if c == "\\" and i + 1 < len(value) and value[i + 1] in '"`$\\':
                cur += value[i + 1]
                i += 1
            elif c == '"':
                quoted = False
            else:
                cur += c
        elif c == '"':
            quoted, has = True, True
        elif c == " ":
            if cur or has:
                args.append(cur)
            cur, has = "", False
        else:
            cur += c
        i += 1
    if cur or has:
        args.append(cur)
    return [a.replace("%%", "%") for a in args]


@posix_only
def test_install_writes_the_entry_and_the_icon(tmp_path):
    exe = fake_shell(tmp_path / "target" / "release")
    home = tmp_path / "data"
    result = de.install(exe, home)
    desktop = home / "applications" / "dev.atlas.desktop.desktop"
    icon = home / "icons" / "hicolor" / "256x256" / "apps" / "dev.atlas.desktop.png"
    assert result == {"desktop_file": str(desktop), "icon": str(icon), "exec": str(exe), "changed": True,
                      "removed_legacy": []}
    lines = desktop.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "[Desktop Entry]"
    for line in ("Type=Application", "Name=ATLAS", f'Exec="{exe}" --open', "Icon=dev.atlas.desktop",
                 "StartupWMClass=atlas-desktop", "Categories=Office;Utility;", "StartupNotify=false",
                 "Terminal=false"):
        assert line in lines
    # A second launch exits at once and would never complete a startup notification.
    assert "StartupNotify=true" not in lines
    assert any(line.startswith("Comment=") for line in lines)
    assert b"\r" not in desktop.read_bytes()
    assert icon.read_bytes() == de.ICON_SOURCE.read_bytes()
    assert de.ICON_SOURCE.name == "128x128@2x.png" and de.ICON_SOURCE.is_file()


@posix_only
def test_a_path_with_spaces_and_specials_round_trips(tmp_path):
    exe = fake_shell(tmp_path / "my builds" / "a $b `c` \"d\" 50% \\e")
    de.install(exe, tmp_path / "data")
    text = (tmp_path / "data" / "applications" / de.DESKTOP_NAME).read_text(encoding="utf-8")
    value = next(line for line in text.splitlines() if line.startswith("Exec="))[len("Exec="):]
    assert unquote_exec(value) == [str(exe), "--open"]
    assert "%%" in value and "\\$" in value and '\\"' in value
    # Spaces alone just need the quotes.
    assert de.exec_arg("/home/t/my builds/atlas-desktop") == '"/home/t/my builds/atlas-desktop"'


@posix_only
def test_a_control_character_is_refused():
    with pytest.raises(ValueError):
        de.exec_arg("/tmp/a\nb")


@posix_only
def test_install_is_idempotent(tmp_path):
    exe = fake_shell(tmp_path / "bin")
    home = tmp_path / "data"
    assert de.install(exe, home)["changed"] is True
    desktop, icon = de.paths(home)
    before = (desktop.stat().st_mtime_ns, icon.stat().st_mtime_ns)
    assert de.install(exe, home)["changed"] is False
    assert (desktop.stat().st_mtime_ns, icon.stat().st_mtime_ns) == before
    assert sorted(p.name for p in desktop.parent.iterdir()) == [de.DESKTOP_NAME]
    # A moved binary rewrites the entry.
    other = fake_shell(tmp_path / "bin2")
    assert de.install(other, home)["changed"] is True
    assert f'Exec="{other}" --open' in desktop.read_text(encoding="utf-8")


@posix_only
def test_uninstall_removes_only_its_two_files(tmp_path):
    exe = fake_shell(tmp_path / "bin")
    home = tmp_path / "data"
    de.install(exe, home)
    desktop, icon = de.paths(home)
    other_app = desktop.parent / "org.kde.dolphin.desktop"
    other_icon = icon.parent / "other.png"
    other_app.write_text("[Desktop Entry]\n", encoding="utf-8")
    other_icon.write_bytes(b"png")
    assert de.uninstall(home) == {"removed": [str(desktop), str(icon)]}
    assert not desktop.exists() and not icon.exists()
    assert other_app.exists() and other_icon.exists()
    assert de.uninstall(home) == {"removed": []}  # again: nothing to do, no error


@posix_only
def test_missing_or_not_executable_exec_is_refused(tmp_path):
    home = tmp_path / "data"
    with pytest.raises(ValueError):
        de.install(tmp_path / "nope", home)
    plain = tmp_path / "plain"
    plain.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        de.install(plain, home)
    with pytest.raises(ValueError):
        de.install(tmp_path, home)  # a folder
    assert not home.exists()


@posix_only
def test_status(tmp_path):
    exe = fake_shell(tmp_path / "bin")
    home = tmp_path / "data"
    st = de.status(home, exe)
    assert st["desktop_file_exists"] is False and st["icon_exists"] is False
    assert st["exec"] is None and st["current"] is False
    de.install(exe, home)
    st = de.status(home, exe)
    assert st["desktop_file_exists"] and st["icon_exists"] and st["current"] is True
    assert st["exec"] == f'"{exe}" --open'
    assert de.status(home, fake_shell(tmp_path / "bin2"))["current"] is False
    assert "current" not in de.status(home)


@posix_only
def test_cli(tmp_path):
    exe = fake_shell(tmp_path / "shell dir")
    home = tmp_path / "data home"
    r = cli("install", "--exec", exe, "--data-home", home)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["desktop_file"] == str(home / "applications" / de.DESKTOP_NAME)
    r = cli("status", "--exec", exe, "--data-home", home)
    assert r.returncode == 0 and json.loads(r.stdout)["current"] is True
    r = cli("install", "--exec", tmp_path / "missing", "--data-home", home)
    assert r.returncode == 1 and "not a file" in r.stderr
    r = cli("install", "--data-home", home)
    assert r.returncode == 2 and "--exec" in r.stderr
    r = cli("uninstall", "--data-home", home)
    assert r.returncode == 0 and len(json.loads(r.stdout)["removed"]) == 2
    assert list((home / "applications").iterdir()) == []


@posix_only
def test_default_data_home_follows_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert de.default_data_home() == tmp_path / "xdg"
    # Without --data-home the CLI uses it (conftest's temp XDG_DATA_HOME, never the real one).
    exe = fake_shell(tmp_path / "bin")
    r = subprocess.run([sys.executable, "-m", "atlas", "desktop-entry", "install", "--exec", str(exe)],
                       cwd=APP_DIR, capture_output=True, text=True, timeout=60, env=dict(os.environ))
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "xdg" / "applications" / de.DESKTOP_NAME).is_file()
    monkeypatch.setenv("XDG_DATA_HOME", "relative/path")
    assert de.default_data_home() == Path.home() / ".local" / "share"


def test_windows_says_not_yet(tmp_path, monkeypatch, capsys):
    """Runs on both OSes: on Windows for real, here with the check forced."""
    monkeypatch.setattr(de, "_on_windows", lambda: True)
    exe = fake_shell(tmp_path / "bin")
    assert de.main(["install", "--exec", str(exe), "--data-home", str(tmp_path / "data")]) == 2
    assert "not on Windows yet" in capsys.readouterr().out
    assert not (tmp_path / "data").exists()
