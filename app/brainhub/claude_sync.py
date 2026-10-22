"""Claude Code parity across the two OSes (dual-boot design §7, plan Task 11, ruling D2).

    python -m brainhub claude-sync [--shared <dir>] [--claude-dir <dir>] [--dry-run] [--settings-only]
    python -m brainhub claude-sync init --shared <dir>

Stdlib only, and no dependency on the hub or its data: it imports nothing from ``brainhub`` but
``osdirs``. The desktop shell runs it at every login before the hub starts, and with
``--settings-only`` when this OS's ``settings.json`` changes; it can also be run by hand. It
prints one line (the whole summary with ``--dry-run``, which writes nothing at all) and exits 0,
except on a usage error (2) or a failed ``init`` (1).

**The shared store** (``--shared``; default ``$BRAINHUB_SHARED_DIR``, else
``/mnt/data/ClaudeShared`` on Linux and ``D:\\ClaudeShared`` on Windows; design §7.2) is set up
once, with the owner, by ``claude-sync init``. A sync never creates it: when the folder is missing,
has no ``memory/`` folder (an empty mount point, say) or can't be written, it changes nothing and
logs one line. Layout: ``memory/home/``, ``memory/projects/<name>/``, ``skills/<name>/``,
``settings.shared.json``, ``pairs.json`` (the owner's exceptions, see below), a git repo.

A sync, in order:

1. **Pairs.** The home folder's key pairs with ``memory/home``. A project key pairs with
   ``memory/projects/<name>`` when it is the key of a folder named <name> in the projects root
   (``~/dev`` on Linux, ``D:\\`` on Windows), or of the folder that ``pairs.json``
   (``{"<name>": {"linux": "<folder>", "windows": "<folder>"}}``) names for <name> on this OS. A
   key is the folder's path with every character that isn't an ASCII letter or digit turned into
   ``-``, case kept (ruling D2). On Windows, keys that differ only in the drive letter's case
   (``d--X``, ``D--X``) are one key. Other keys (scratch folders, say) are left alone, and so are
   the hub's own filing worktrees (``…-data-worktrees-e-<entry id>``, ruling D11), always. A folder
   that ``pairs.json`` names pairs only under the name it gives there, never also under its own
   folder name; a root folder named like an entry that names another folder doesn't pair.
2. **Point** (ruling D2: option 2, since a linked memory folder fails on Linux). For each pair
   with notes on either side, this OS's real memory folder (``<claude>/projects/<key>/memory``)
   is merged into the shared one after a backup to ``<state>/claude-sync-backups/<key>-<stamp>/``:
   a file the shared copy lacks is copied; a file whose content differs is kept as well, as
   ``<stem>.<os><suffix>``; ``MEMORY.md`` is merged line by line, without duplicates (its links to
   a file kept under the second name point there). Only files new or changed since this key's last
   merge count, so a backup is made once per change, and a note removed from the store isn't
   brought back; likewise only the ``MEMORY.md`` lines new since this key's last merged index are
   added, so a line the owner deleted from the store stays deleted. A key with no record (a first
   merge, or a lost or damaged ``claude-sync-last.json``) merges only the notes whose content the
   store has never held: not now under any name, nor in its git history. The local folder stays
   where it is. Then ``autoMemoryDirectory`` = the shared folder goes into the pair's
   ``settings.local.json`` (home: ``<home>/.claude/settings.local.json``, the home folder's own
   project settings, wherever ``<claude>`` is; a project: ``<project>/.claude/settings.local.json``,
   which is also added to the repo's ``info/exclude`` when git doesn't ignore it yet). Every other
   key there stays as it is. Never pointed (their notes still merge): a project folder holding a
   BrainHub root (the live copy is never a workspace, design §5.1), one on the shared drive (the
   store's parent) outside this OS's projects root, and a file whose ``autoMemoryDirectory`` has
   the other OS's path shape (that OS's sessions read it).
3. **Header.** Every shared ``MEMORY.md`` starts with exactly one ``HEADER`` line.
4. **Skills.** A skill folder in ``<claude>/skills`` that isn't a link and isn't managed by
   claude.ai sync (``synced``) or by a plugin (a name in ``<claude>/plugins``) is copied to the
   store's ``skills/<name>``, moved to the backups and replaced by a link to the store's copy (a
   symlink on POSIX, a junction on Windows: neither needs admin). A store skill missing here is
   linked in. A local skill that differs from the store's skill of the same name is left alone,
   and reported.
5. **Settings**, per key, both ways, between ``<claude>/settings.json`` and the store's
   ``settings.shared.json`` = ``{"per_os_keys": [...], "settings": {key: {"value", "changed_at",
   "deleted", "os"}}}``. Every key is shared except the per-OS ones: ``DEFAULT_PER_OS_KEYS``, the
   owner's ``per_os_keys`` (BrainHub Settings) and any key whose value holds an absolute path on
   either side. A per-OS key is never sent and never received. First, each local change since the
   last sync (the snapshot in ``<state>/claude-sync-last.json``; on a first sync, every value that
   differs from the store's) is recorded, with the file's modification time as its time, unless the
   store holds a later change of that key. Then the store is applied: values and removals. A
   changed ``settings.json`` is backed up to ``<state>/claude-sync-backups/settings/`` and replaced
   atomically.
6. **Backup.** If the store is a git repo, everything is committed ("claude-sync <os> <time>") and
   pushed to ``origin`` when one is set, never prompting. A failed push is logged and retried at the
   next sync.

Memory notes and skills are never deleted (settings removals do propagate, design §7.4). Problems
go to ``<state>/claude-sync.log``, and a summary to ``<state>/claude-sync.json``, which BrainHub
Settings shows. One sync runs at a time per OS (``<state>/claude-sync.lock``).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import osdirs

HEADER = ("Shared by Windows and Kubuntu. Label any note that is only true on one OS: "
          "(Windows only) / (Linux only).")
# Never shared (design §7.4). autoMemoryDirectory is claude-sync's own per-project key; at user
# level it would put every project into one folder.
DEFAULT_PER_OS_KEYS = ("hooks", "statusLine", "permissions", "env", "defaultShell", "apiKeyHelper",
                       "autoMemoryDirectory")
MANAGED_SKILLS = ("synced",)  # claude.ai sync; plugin-managed names come from <claude>/plugins
INDEX = "MEMORY.md"
STORE_SETTINGS = "settings.shared.json"
PAIRS_FILE = "pairs.json"
SUMMARY_FILE = "claude-sync.json"
LOG_FILE = "claude-sync.log"
LAST_FILE = "claude-sync-last.json"
LOCK_FILE = "claude-sync.lock"
BACKUPS = "claude-sync-backups"
STORE_DIRS = ("memory/home", "memory/projects", "skills")
GITATTRIBUTES = "* -text\n"
GIT_TIMEOUT_S = 60
PUSH_TIMEOUT_S = 30
ERROR_TAIL = 300
LOG_MAX_BYTES = 256 * 1024
MAX_PER_OS_KEYS = 100
REPLACE_TRIES = 10  # Windows: a reader holding the file open makes os.replace fail for a moment
REPLACE_PAUSE_S = 0.2

_WIN = sys.platform == "win32"
_NO_WINDOW = 0x08000000 if _WIN else 0  # CREATE_NO_WINDOW (Ruling 13)
_GIT_REPO_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR")
_URL_CREDENTIALS = re.compile(r"(://)[^/@\s]+@")
_KEY_CHARS = re.compile(r"[^A-Za-z0-9]")
_DRIVE_KEY = re.compile(r"[A-Za-z]--")
# A path bound to one machine inside a string, at the start or after a space, quote, =, (, comma,
# [, {, < or ; (a Windows PATH-style list): /x or //x (POSIX, UNC), C:\ or C:/ (a drive), \\x (UNC),
# ~/ or ~\ (a home folder: ~/dev/X on Linux is D:\X on Windows), $VAR/, ${VAR}/ or %VAR%\ (a folder
# from the environment, like $HOME/bin or %USERPROFILE%\x). Also /x right after a colon (the later
# parts of /usr/bin:/bin or .:/opt/x, never the // of https://), and file:/ URLs. Web URLs and
# relative paths don't match.
_ABS_PATH = re.compile(
    r"(?:^|(?<=[\s\"'=(,\[{<;]))(?://?[^\s/]|[A-Za-z]:[\\/]|\\\\[^\s\\]|~[\\/]"
    r"|\$\{?[A-Za-z_][A-Za-z0-9_]*\}?[\\/]|%[A-Za-z_][A-Za-z0-9_()]*%[\\/])"
    r"|(?<=:)/(?!/)"
    r"|(?i:(?<![A-Za-z0-9])file:/)")
# A Windows path's shape (P7): a drive letter, or a \\server UNC path. A leading / is Linux's.
_WINDOWS_SHAPE = re.compile(r"[A-Za-z]:[\\/]|\\\\")
_SETTING_KEY = re.compile(r"[A-Za-z0-9_.:-]{1,100}")
# The hub's own filing runs (claude -p in <root>/data/worktrees/<entry id>) left keys like
# -mnt-data-BrainHub-data-worktrees-e-20261020-101500-ab12 or D--BrainHub-data-worktrees-e-…
# (ruling D11). They are hub-internal: never paired, merged or pointed.
_HUB_INTERNAL_KEY = re.compile(r"-data-worktrees-e-[0-9]{8}-[0-9]{6}-[0-9a-f]{4}", re.IGNORECASE)


def is_hub_internal(key_or_path) -> bool:
    """A key (or a folder) of the hub's own filing worktrees: under a BrainHub data/worktrees/."""
    text = str(key_or_path)
    if _HUB_INTERNAL_KEY.search(encode_key(text)):
        return True
    parts = [p.lower() for p in re.split(r"[\\/]+", text)]
    return any(a == "data" and b == "worktrees" for a, b in zip(parts, parts[1:]))
_MISSING = object()


class StoreUnavailable(Exception):
    """The shared store can't be used now (missing, not set up, read-only or unreadable)."""


class Busy(Exception):
    """Another claude-sync holds this OS's lock."""


class BadFile(Exception):
    """A JSON file that exists but can't be read or isn't a JSON object: never overwritten."""


# -- folders and keys ----------------------------------------------------------------------------

def default_shared_dir() -> Path:
    value = os.environ.get("BRAINHUB_SHARED_DIR")
    if value:
        return Path(value)
    return Path("D:\\ClaudeShared") if _WIN else Path("/mnt/data/ClaudeShared")


def default_claude_dir() -> Path:
    """Claude Code's own folder: $CLAUDE_CONFIG_DIR (as Claude Code reads it), else ~/.claude."""
    value = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(value) if value else Path.home() / ".claude"


def default_projects_root() -> Path:
    """Where the owner's projects live (design §4): D:\\<name> on Windows, ~/dev/<name> on Linux."""
    return Path("D:\\") if _WIN else Path.home() / "dev"


def encode_key(path) -> str:
    """Claude Code's key for a folder (ruling D2): every character but an ASCII letter or digit
    becomes "-", case kept. /home/tudor/dev/X -> -home-tudor-dev-X; D:\\X -> D--X."""
    return _KEY_CHARS.sub("-", str(path))


def fold_key(key: str, os_name: str) -> str:
    """Windows records one folder under keys that differ in the drive letter's case (d--X, D--X):
    they compare as one. Other keys, and every key on Linux, compare as they are."""
    if os_name == "windows" and _DRIVE_KEY.match(key):
        return key[0].upper() + key[1:]
    return key


def holds_abs_path(value) -> bool:
    """Does a settings value hold a path bound to one machine anywhere (strings, dict keys, lists,
    at any depth)? See _ABS_PATH for the forms."""
    if isinstance(value, str):
        return bool(_ABS_PATH.search(value))
    if isinstance(value, dict):
        return any(holds_abs_path(k) or holds_abs_path(v) for k, v in value.items())
    if isinstance(value, list):
        return any(holds_abs_path(v) for v in value)
    return False


def other_os_path(value, os_name: str) -> bool:
    """Is `value` a path in the other OS's shape (P7)? On Linux a drive letter or a \\\\server path;
    on Windows a leading /."""
    if not isinstance(value, str):
        return False
    if os_name == "windows":
        return value.startswith("/")
    return bool(_WINDOWS_SHAPE.match(value))


def _under(path, folder) -> bool:
    """Is `path` `folder` or inside it (case-folded on Windows, no links resolved)?"""
    a, b = (os.path.normcase(os.path.abspath(p)) for p in (path, folder))
    return Path(a).is_relative_to(b)


def is_link(path) -> bool:
    """A symlink, or (Windows) a junction."""
    return os.path.islink(path) or os.path.isjunction(path)


def make_link(target: Path, link: Path) -> None:
    """A folder link at `link` to `target`: a symlink on POSIX, a junction on Windows (no admin or
    Developer Mode needed, unlike a symlink)."""
    if _WIN:
        _junction(target, link)
    else:
        os.symlink(target, link, target_is_directory=True)


def _junction(target: Path, link: Path) -> None:
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
        return
    except (ImportError, AttributeError, OSError):
        pass
    comspec = os.environ.get("ComSpec") or "cmd.exe"
    r = subprocess.run([comspec, "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=30, creationflags=_NO_WINDOW)
    if r.returncode != 0:
        raise OSError(f"mklink /J failed: {(r.stdout + r.stderr).strip()[-ERROR_TAIL:]}")


def _abs(path) -> Path:
    return Path(os.path.abspath(path))


def _dirs(path: Path) -> list[Path]:
    """The sub-folders of `path` (links to folders included), sorted; none if it can't be read."""
    out = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_dir():
                        out.append(Path(entry.path))
                except OSError:
                    pass
    except OSError:
        return []
    return sorted(out)


def _entries(path: Path) -> list[Path]:
    try:
        return sorted(Path(e.path) for e in os.scandir(path))
    except OSError:
        return []


def note_files(folder: Path) -> list[str]:
    """The notes in a memory folder: every file not starting with "." (nor in a folder that does),
    as sorted relative paths with / separators. Links aren't followed."""
    out = []
    for top, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        rel = Path(top).relative_to(folder)
        out += [(rel / f).as_posix() for f in files if not f.startswith(".")]
    return sorted(out)


def has_notes(folder: Path) -> bool:
    return not is_link(folder) and bool(note_files(folder))


def _all_files(folder: Path) -> list[str]:
    out = []
    for top, _dirs_, files in os.walk(folder):
        rel = Path(top).relative_to(folder)
        out += [(rel / f).as_posix() for f in files]
    return sorted(out)


def trees_equal(a: Path, b: Path) -> bool:
    files = _all_files(a)
    if files != _all_files(b):
        return False
    return all((a / f).read_bytes() == (b / f).read_bytes() for f in files)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _valid_name(name) -> bool:
    return isinstance(name, str) and name not in ("", ".", "..") and not re.search(r"[\\/:\x00]", name)


# -- files ---------------------------------------------------------------------------------------

def _now_iso(now) -> str:
    return now().replace(microsecond=0).isoformat()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).replace(microsecond=0).isoformat()


def read_json_obj(path: Path) -> dict | None:
    """None when the file doesn't exist; BadFile when it can't be read or isn't a JSON object."""
    try:
        text = Path(path).read_text("utf-8-sig")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        raise BadFile(f"can't read {path}: {e}") from None
    try:
        obj = json.loads(text)
    except ValueError as e:
        raise BadFile(f"{path} is not valid JSON ({e})") from None
    if not isinstance(obj, dict):
        raise BadFile(f"{path} is not a JSON object")
    return obj


def _replace(src, dst) -> None:
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(REPLACE_PAUSE_S)


def _write_atomic(path: Path, fill, *, stat_from: Path | None = None) -> None:
    """Whole or not at all: `fill(f)` writes a temp file beside `path` (a dot-file named for this
    process, so a leftover is never taken for a note or a skill), which is flushed to disk and then
    renamed over `path`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.claude-sync-{os.getpid()}.tmp")
    try:
        with open(tmp, "wb") as f:
            fill(f)
            f.flush()
            os.fsync(f.fileno())
        if stat_from is not None:
            try:
                shutil.copystat(stat_from, tmp)
            except OSError:
                pass  # times and mode bits are a nicety; the content is what counts
        _replace(tmp, path)
    finally:
        if os.path.lexists(tmp):  # only our own temp file, after a failed write
            os.unlink(tmp)


def write_text_atomic(path: Path, text: str, *, errors: str = "strict") -> None:
    """UTF-8, LF line endings (written as given), atomically. `errors="surrogateescape"` writes
    back the bytes of a file that was read that way."""
    data = text.encode("utf-8", errors)
    _write_atomic(path, lambda f: f.write(data))


def copy_atomic(src: Path, dst: Path) -> None:
    """Copy a file atomically (a crash never leaves half a note), with its times where possible."""
    def fill(f):
        with open(src, "rb") as source:
            shutil.copyfileobj(source, f)
    _write_atomic(dst, fill, stat_from=src)


def write_json_atomic(path: Path, obj, *, sort_keys: bool = False, ascii: bool = False) -> None:
    """`ascii` escapes everything else, so text that came from a file that isn't UTF-8 (kept as
    surrogates, see _read_lines; a note's file name on Linux, too) can still be written."""
    write_text_atomic(path, json.dumps(obj, indent=2, ensure_ascii=ascii, sort_keys=sort_keys) + "\n")


def _read_lines(path: Path) -> list[str]:
    """A MEMORY.md's lines. Bytes that aren't UTF-8 (a file saved in another encoding) come through
    as surrogates, so the merge still works and writing back with surrogateescape keeps them."""
    return path.read_text("utf-8-sig", errors="surrogateescape").splitlines()


def _join(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


def _write_lines(path: Path, lines: list[str]) -> None:
    """A MEMORY.md, LF, with any bytes _read_lines kept as surrogates written back as they were."""
    write_text_atomic(path, _join(lines), errors="surrogateescape")


def _index_texts(lines: list[str]) -> list[str]:
    """The lines of a MEMORY.md that count (neither blank nor the header), stripped, once each."""
    return list(dict.fromkeys(t for t in (line.strip() for line in lines) if t and t != HEADER))


def _err(e: BaseException) -> str:
    return (str(e) or type(e).__name__)[-ERROR_TAIL:]


# -- the lock ------------------------------------------------------------------------------------

def take_lock(state: Path):
    """A non-blocking exclusive lock on <state>/claude-sync.lock: the open fd, or None when another
    claude-sync holds it. The OS drops it when the holder dies, so it never goes stale. Raises
    OSError when the file can't be opened."""
    path = Path(state) / LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if _WIN:
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # the first byte
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def release_lock(fd) -> None:
    try:
        if _WIN:
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        os.close(fd)


# -- the store -----------------------------------------------------------------------------------

def unavailable_reason(shared: Path, *, probe: bool = True) -> str | None:
    """Why the store can't be used now, or None. It must exist with its memory/ folder (an empty
    mount point never counts), and, with `probe`, take a file (created and removed again)."""
    shared = Path(shared)
    if not os.path.isdir(shared):
        return f"shared store not found at {shared}"
    if not os.path.isdir(shared / "memory"):
        return (f"{shared} is not a claude-sync store (no memory folder); set it up once with "
                f"`python -m brainhub claude-sync init --shared {shared}`")
    if probe:
        try:
            fd, name = tempfile.mkstemp(prefix=".claude-sync-probe-", dir=shared)
            os.close(fd)
            os.unlink(name)
        except OSError as e:
            return f"shared store at {shared} can't be written ({e.strerror or e})"
    return None


def read_store(shared: Path) -> dict:
    """settings.shared.json as {"per_os_keys": [...], "settings": {...}}; empty when absent.
    BadFile when it can't be read or has an unexpected shape (it is then never overwritten)."""
    path = Path(shared) / STORE_SETTINGS
    obj = read_json_obj(path) or {}
    per_os, settings = obj.get("per_os_keys", []), obj.get("settings", {})
    if not isinstance(per_os, list) or not all(isinstance(k, str) for k in per_os):
        raise BadFile(f"{path}: per_os_keys is not a list of names")
    if not isinstance(settings, dict):
        raise BadFile(f"{path}: settings is not an object")
    for key, entry in settings.items():
        if (not isinstance(entry, dict) or not isinstance(entry.get("changed_at"), str)
                or not isinstance(entry.get("deleted"), bool) or "value" not in entry):
            raise BadFile(f"{path}: the entry for {key!r} is not {{value, changed_at, deleted}}")
    return {**obj, "per_os_keys": per_os, "settings": settings}


def write_store(shared: Path, store: dict) -> None:
    """Sorted keys and LF, so the store's git history shows real changes only. No absolute paths:
    a value holding one is never recorded."""
    write_json_atomic(Path(shared) / STORE_SETTINGS, store, sort_keys=True)


def clean_per_os_keys(keys) -> list[str]:
    """The owner's extra per-OS keys: setting names, without blanks, duplicates or the defaults
    (which always apply). ValueError when it isn't a list of setting names."""
    if not isinstance(keys, list):
        raise ValueError("keys must be a list of setting names")
    out: list[str] = []
    for key in keys:
        if not isinstance(key, str):
            raise ValueError("keys must be a list of setting names")
        key = key.strip()
        if not key:
            continue
        if not _SETTING_KEY.fullmatch(key):
            raise ValueError(f"not a setting name: {key!r}")
        if key not in DEFAULT_PER_OS_KEYS and key not in out:
            out.append(key)
    if len(out) > MAX_PER_OS_KEYS:
        raise ValueError(f"at most {MAX_PER_OS_KEYS} per-OS keys")
    return out


def set_per_os_keys(shared: Path, keys, state: Path) -> list[str]:
    """Store the owner's extra per-OS keys in settings.shared.json, and drop the store's entries for
    them: from now on they are never shared. Raises ValueError (bad keys), StoreUnavailable or
    Busy."""
    custom = clean_per_os_keys(keys)
    reason = unavailable_reason(shared)
    if reason:
        raise StoreUnavailable(reason)
    try:
        lock = take_lock(state)
    except OSError as e:
        raise StoreUnavailable(f"can't take the claude-sync lock: {e}") from None
    if lock is None:
        raise Busy("claude-sync is running; try again in a moment")
    try:
        try:
            store = read_store(shared)
        except BadFile as e:
            raise StoreUnavailable(str(e)) from None
        store["per_os_keys"] = custom
        for key in custom:
            store["settings"].pop(key, None)
        try:
            write_store(shared, store)
        except OSError as e:
            raise StoreUnavailable(f"can't write {Path(shared) / STORE_SETTINGS}: {e}") from None
    finally:
        release_lock(lock)
    return custom


def view(state: Path, shared: Path) -> dict:
    """What BrainHub Settings shows: the last summary, whether the store is there, and the per-OS
    keys (the defaults, and the owner's from the store; None while it can't be read)."""
    try:
        summary = read_json_obj(Path(state) / SUMMARY_FILE)
    except BadFile:
        summary = None
    reason = unavailable_reason(shared, probe=False)
    custom = None
    if reason is None:
        try:
            custom = read_store(shared)["per_os_keys"]
        except BadFile as e:
            reason = str(e)
    return {"summary": summary, "shared": str(shared), "available": custom is not None, "reason": reason,
            "per_os_keys": {"default": list(DEFAULT_PER_OS_KEYS), "custom": custom}}


# -- git -----------------------------------------------------------------------------------------

def _child_env() -> dict:
    """Never a prompt (terminal, credential manager, ssh askpass, ssh's own password or host-key
    questions), and no inherited variable that points git at another repo. An ssh command the owner
    set (GIT_SSH_COMMAND, GIT_SSH) is kept."""
    env = dict(os.environ)
    for name in _GIT_REPO_VARS:
        env.pop(name, None)
    env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", SSH_ASKPASS_REQUIRE="never")
    if not (env.get("GIT_SSH_COMMAND") or env.get("GIT_SSH")):
        env["GIT_SSH_COMMAND"] = "ssh -oBatchMode=yes"
    return env


def _masked(text: str) -> str:
    return _URL_CREDENTIALS.sub(r"\1***@", text).strip()[-ERROR_TAIL:]


class Git:
    """git on one repo, with safe.directory (files the other OS made may have another owner) and
    this store's rules on every call: no line-ending conversion, no mode bits, this OS's case rule
    (core.ignorecase, as for the brain in ruling D7: the store's .git/config may come from the
    other OS, and with Windows' true Linux git wouldn't see a note.md next to Note.md), no
    background gc on the shared drive, BrainHub's identity."""

    def __init__(self, repo: Path):
        self.repo = Path(repo)
        safe = str(self.repo.resolve()).replace("\\", "/")
        self.base = ["git", "-c", f"safe.directory={safe}", "-c", "core.autocrlf=false",
                     "-c", "core.filemode=false", "-c", f"core.ignorecase={'true' if _WIN else 'false'}",
                     "-c", "gc.auto=0", "-c", "maintenance.auto=false",
                     "-c", "user.name=BrainHub", "-c", "user.email=brainhub@localhost"]

    def __call__(self, *args: str, timeout: float = GIT_TIMEOUT_S, input: str | None = None,
                 detach: bool = False) -> subprocess.CompletedProcess:
        """`input` goes to git's stdin (otherwise it reads nothing). `detach` (the push) starts git
        in a session of its own on POSIX: with no controlling terminal, nothing it runs (ssh, a
        credential helper) can open /dev/tty and wait on a prompt, and a timeout kills the session."""
        cmd = [*self.base, *args]
        group = detach and not _WIN
        try:
            with subprocess.Popen(cmd, cwd=self.repo, text=True, encoding="utf-8", errors="replace",
                                  stdin=subprocess.DEVNULL if input is None else subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_child_env(),
                                  creationflags=_NO_WINDOW, start_new_session=group) as p:
                try:
                    out, err = p.communicate(input, timeout=timeout)
                except subprocess.TimeoutExpired:
                    if group:
                        try:
                            os.killpg(p.pid, signal.SIGKILL)
                        except OSError:
                            pass
                    p.kill()
                    p.wait()
                    return subprocess.CompletedProcess(cmd, 124, "", f"git {args[0]} timed out after {timeout:g} s")
                return subprocess.CompletedProcess(cmd, p.returncode, out, err)
        except OSError as e:
            return subprocess.CompletedProcess(cmd, 127, "", f"can't run git: {e}")

    def push(self) -> tuple[bool, str]:
        r = self("-c", "credential.interactive=never", "push", "--quiet", "origin", "HEAD:refs/heads/main",
                 timeout=PUSH_TIMEOUT_S, detach=True)
        return r.returncode == 0, _masked(r.stderr)


def init_store(shared: Path) -> Path:
    """Set the store up (only ever run by hand, with the owner): the folders, `* -text`, a git repo
    with core.autocrlf=false and core.filemode=false, and a first commit. Safe to run again. The
    folder's parent must exist (a missing drive is never papered over). Raises OSError."""
    shared = _abs(shared)
    if not shared.parent.is_dir():
        raise OSError(f"{shared.parent} doesn't exist (is the drive mounted?); nothing created")
    shared.mkdir(exist_ok=True)
    for sub in STORE_DIRS:
        folder = shared / sub
        folder.mkdir(parents=True, exist_ok=True)
        keep = folder / ".gitkeep"  # git keeps no empty folder; dot-files are never notes or skills
        if not keep.exists():
            keep.write_bytes(b"")
    attributes = shared / ".gitattributes"
    if not attributes.exists():
        with open(attributes, "w", encoding="utf-8", newline="\n") as f:
            f.write(GITATTRIBUTES)
    git = Git(shared)

    def check(r: subprocess.CompletedProcess, what: str) -> None:
        if r.returncode != 0:
            raise OSError(f"git {what} failed: {_masked(r.stderr)}")

    if not (shared / ".git").exists():
        check(git("init", "-q", "-b", "main"), "init")
    for key, value in (("core.autocrlf", "false"), ("core.filemode", "false"),
                       ("user.name", "BrainHub"), ("user.email", "brainhub@localhost")):
        check(git("config", key, value), "config")
    status = git("status", "--porcelain", "--untracked-files=all")
    check(status, "status")
    if status.stdout.strip():
        check(git("add", "-A"), "add")
        check(git("commit", "-q", "-m", "claude-sync init"), "commit")
    return shared


# -- a sync --------------------------------------------------------------------------------------

@dataclass
class Pair:
    name: str              # "home", or the project's name
    shared: Path           # its folder in the store
    settings_local: Path   # where autoMemoryDirectory goes
    project: Path | None   # the project's folder (None for home)
    keys: list[str] = field(default_factory=list)  # this OS's keys for it


class Sync:
    def __init__(self, shared: Path, claude_dir: Path, state: Path, *, home: Path, projects_root: Path,
                 os_name: str, dry_run: bool, now):
        self.shared, self.claude, self.state = shared, claude_dir, state
        self.home, self.root = home, projects_root
        self.os, self.dry, self.now = os_name, dry_run, now
        self.started = _now_iso(now)
        self.stamp = now().strftime("%Y%m%d-%H%M%S")
        self.lines: list[str] = []
        self.problems: dict[str, list[str]] = {}
        try:
            self.last = read_json_obj(state / LAST_FILE) or {}
        except BadFile as e:
            # Unknown, not empty: no key has a record now, so a merge takes only the notes the store
            # has never held (merge_folder), never every stale local copy as <stem>.<os>.md.
            self.last = {}
            self.problem("memory", f"{e}; {self._keep_damaged_state()}only notes the store has never "
                                   "held are merged, and settings compare as on a first sync")
        merged = self.last.get("merged")
        self.merged: dict[str, dict] = dict(merged) if isinstance(merged, dict) else {}
        snapshot = self.last.get("settings")
        self.snapshot: dict | None = snapshot if isinstance(snapshot, dict) else None
        self.push_pending = bool(self.last.get("push_pending"))

    # -- reporting --------------------------------------------------------------------------------
    def log(self, text: str) -> None:
        self.lines.append(text)

    def problem(self, part: str, text: str) -> None:
        self.problems.setdefault(part, []).append(text)
        self.log(f"{part}: {text}")

    def _part(self, name: str, fn) -> dict:
        try:
            return fn()
        except Exception as e:  # one failing part never stops the others
            self.problem(name, _err(e))
            return {"error": _err(e)}

    # -- backups ------------------------------------------------------------------------------------
    def _backup_path(self, name: str) -> Path:
        base = self.state / BACKUPS / f"{name}-{self.stamp}"
        target, n = base, 1
        while os.path.lexists(target):
            n += 1
            target = base.with_name(f"{base.name}-{n}")
        return target

    def backup_file(self, path: Path, label: str) -> Path:
        target = self._backup_path(f"settings/{label}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        return target

    def _keep_damaged_state(self) -> str:
        """Copy a claude-sync-last.json that can't be read to the backups (this run replaces it)."""
        if self.dry:
            return ""
        try:
            target = self._backup_path(LAST_FILE)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.state / LAST_FILE, target)
        except OSError as e:
            return f"couldn't back it up ({_err(e)}); "
        return f"backed up to {target}; "

    # -- 1. pairs -------------------------------------------------------------------------------------
    def find_pairs(self) -> tuple[list[Pair], int]:
        exceptions: dict[str, Path] = {}
        try:
            listed = read_json_obj(self.shared / PAIRS_FILE) or {}
        except BadFile as e:
            self.problem("memory", f"{e}; its exceptions are ignored")
            listed = {}
        for name, folders in listed.items():
            folder = folders.get(self.os) if isinstance(folders, dict) else None
            if not _valid_name(name) or not (folder is None or isinstance(folder, str)):
                self.problem("memory", f"{PAIRS_FILE}: ignored the entry {name!r}")
            elif folder and folder.strip():
                path = _abs(os.path.expanduser(folder.strip()))
                if is_hub_internal(path):
                    self.problem("memory", f"{PAIRS_FILE}: ignored {name!r}: a BrainHub filing worktree")
                else:
                    exceptions[name] = path
        def fold(key: str) -> str:
            return fold_key(key, self.os)

        candidates: dict[str, tuple[str, Path]] = {}
        shared_real = os.path.normcase(str(self.shared))
        for child in _dirs(self.root):
            # A root folder named like an exception that names another folder isn't that project.
            if (not child.name.startswith(".") and os.path.normcase(str(child)) != shared_real
                    and child.name not in exceptions):
                candidates[fold(encode_key(child))] = (child.name, child)
        for name, folder in exceptions.items():  # an exception names that folder's project
            candidates[fold(encode_key(folder))] = (name, folder)

        # The home folder's own project settings, which a session started there reads (ruling D2):
        # <home>/.claude, not Claude Code's config folder (they differ under CLAUDE_CONFIG_DIR).
        home = Pair("home", self.shared / "memory" / "home", self.home / ".claude" / "settings.local.json", None)
        projects: dict[str, Pair] = {}

        def project(name: str, folder: Path) -> Pair:
            if name not in projects:
                projects[name] = Pair(name, self.shared / "memory" / "projects" / name,
                                      folder / ".claude" / "settings.local.json", folder)
            return projects[name]

        home_key, unpaired = fold(encode_key(self.home)), 0
        for key_dir in _dirs(self.claude / "projects"):
            key = fold(key_dir.name)
            if is_hub_internal(key_dir.name):
                continue  # the hub's own filing runs (ruling D11): never shared
            if key == home_key:
                home.keys.append(key_dir.name)
            elif key in candidates:
                project(*candidates[key]).keys.append(key_dir.name)
            else:
                unpaired += 1
        # Notes from the other OS: a store folder pairs with the root folder of its name, unless
        # pairs.json gives that folder another name (it would then be pointed at both, in turn, at
        # every sync) or names another folder for it.
        claimed = {os.path.normcase(str(f)) for f in exceptions.values()}
        claimed |= {os.path.normcase(str(p.project)) for p in projects.values()}
        for folder in _dirs(self.shared / "memory" / "projects"):
            name = folder.name
            if name in projects or not _valid_name(name) or name.startswith("."):
                continue
            local = exceptions.get(name)
            if local is None:
                local = self.root / name
                if os.path.normcase(str(local)) in claimed:
                    continue
            if local.is_dir():
                project(name, local)
        return [home, *(projects[n] for n in sorted(projects))], unpaired

    # -- 2. point, merge ----------------------------------------------------------------------------
    def sync_memory(self) -> dict:
        out = {"pairs": [], "pointed": [], "merged": [], "headers": 0, "unpaired": 0}
        pairs, out["unpaired"] = self.find_pairs()
        for pair in pairs:
            try:
                self._sync_pair(pair, out)
            except Exception as e:
                self.problem("memory", f"{pair.name}: {_err(e)}")
        out["headers"] = self.ensure_headers()
        return out

    def _sync_pair(self, pair: Pair, out: dict) -> None:
        folders = [(key, self.claude / "projects" / key / "memory") for key in pair.keys]
        if not (has_notes(pair.shared) or any(has_notes(f) for _, f in folders)):
            return  # no notes on either side: nothing to share yet
        for key, folder in folders:
            if is_link(folder):
                self.log(f"memory: {folder} is a link; left as it is")
            elif has_notes(folder):
                merged = self.merge_folder(key, folder, pair.shared)
                if merged:
                    out["merged"].append(merged)
        out["pairs"].append(pair.name)
        if self.point(pair):
            out["pointed"].append(pair.name)

    def _record(self, key: str) -> dict | None:
        """This key's last merge, {"files": {rel: sha256}, "index": [its MEMORY.md's lines] or
        None}; None when there is none (a first merge, a lost state file) or it has another shape."""
        rec = self.merged.get(key)
        files = rec.get("files") if isinstance(rec, dict) else None
        if not isinstance(files, dict):
            return None
        index = rec.get("index")
        return {"files": files, "index": index if isinstance(index, list) else None}

    def _known_to_store(self, local: Path, shared: Path, files: list[str], hashes: dict) -> dict:
        """For a key with no record: the local notes whose content the store already holds, as
        rel -> its name in the pair's folder now (maybe another one), or None when it is only in the
        store's git history (changed or removed there since: this copy is stale)."""
        here: dict[str, str] = {}
        for rel in note_files(shared):
            here.setdefault(_sha(shared / rel), rel)
        known: dict[str, str | None] = {rel: here[hashes[rel]] for rel in files if hashes[rel] in here}
        rest = [rel for rel in files if rel not in known]
        if rest and (self.shared / ".git").exists():
            git = Git(self.shared)
            ids = git("hash-object", "--no-filters", "--", *(str(local / rel) for rel in rest))
            names = ids.stdout.split() if ids.returncode == 0 else []
            if len(names) == len(rest):
                found = git("cat-file", "--batch-check", input="".join(f"{n}\n" for n in names))
                lines = found.stdout.splitlines() if found.returncode == 0 else []
                if len(lines) == len(rest):
                    known.update({rel: None for rel, line in zip(rest, lines) if not line.endswith(" missing")})
        return known

    def merge_folder(self, key: str, local: Path, shared: Path) -> dict | None:
        """Merge one local memory folder into its shared folder (never deleting or overwriting
        anything there); None when nothing in it is new. With a record of this key's last merge,
        new means new or changed since then. Without one (a first merge, or a lost state file) it
        means content the store has never held, so stale copies of notes changed or removed in the
        store since are never merged back, as <stem>.<os>.md or at all."""
        files = note_files(local)
        hashes = {rel: _sha(local / rel) for rel in files}
        record = self._record(key)
        if record is not None:
            known: dict[str, str | None] = {}
            todo = {rel for rel in files if record["files"].get(rel) != hashes[rel]}
        else:
            known = self._known_to_store(local, shared, files, hashes)
            todo = {rel for rel in files if rel not in known}
        index = record["index"] if record is not None else None
        if not todo:
            if record is None and not self.dry:  # from now on, compare with this
                renamed = {rel: where for rel, where in known.items() if where and rel not in (where, INDEX)}
                if INDEX in files:
                    index = _index_texts(self._local_index(local / INDEX, renamed))
                self.merged[key] = {"files": hashes, "index": index or []}
            return None
        result = {"key": key, "into": shared.relative_to(self.shared).as_posix(), "copied": [],
                  "kept_both": [], "index": False, "backup": None}
        if not self.dry:  # the backup first: the merge only adds to the store, but it is the safety net
            backup = self._backup_path(key)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(local, backup, symlinks=True)
            result["backup"] = str(backup)
        renamed: dict[str, str] = {}  # rel -> the name its local version has in the store
        for rel in files:
            if rel == INDEX:
                continue
            src, dst = local / rel, shared / rel
            data = src.read_bytes()
            if os.path.lexists(dst) and dst.read_bytes() == data:
                continue
            if known.get(rel):  # the store holds this content under another name
                renamed[rel] = known[rel]
                continue
            if not os.path.lexists(dst):
                if rel in todo:  # an unchanged note the store no longer has was removed there
                    self._copy(src, dst)
                    result["copied"].append(rel)
                continue
            alt, there = self._alt_name(dst, data)
            alt_rel = alt.relative_to(shared).as_posix()
            if there:
                renamed[rel] = alt_rel
            elif rel in todo:
                self._copy(src, alt)
                result["kept_both"].append(alt_rel)
                renamed[rel] = alt_rel
        if INDEX in todo:
            result["index"], index = self.merge_index(local / INDEX, shared / INDEX, renamed, index)
        elif index is None and INDEX in files:
            index = _index_texts(self._local_index(local / INDEX, renamed))
        if not self.dry:
            self.merged[key] = {"files": hashes, "index": index or []}
        self.log(f"memory: merged {key} into {result['into']}: {len(result['copied'])} copied, "
                 f"{len(result['kept_both'])} kept under a second name"
                 + (f" ({', '.join(result['kept_both'])})" if result["kept_both"] else "")
                 + (f", backup {result['backup']}" if result["backup"] else ""))
        return result

    def _copy(self, src: Path, dst: Path) -> None:
        if not self.dry:
            copy_atomic(src, dst)

    def _alt_name(self, dst: Path, data: bytes) -> tuple[Path, bool]:
        """The name for the local version of a clashing note: <stem>.<os><suffix>, then
        <stem>.<os>-2<suffix>, ...; True when that file already holds this content."""
        n = 1
        while True:
            tag = self.os if n == 1 else f"{self.os}-{n}"
            alt = dst.with_name(f"{dst.stem}.{tag}{dst.suffix}")
            if not os.path.lexists(alt):
                return alt, False
            if alt.is_file() and alt.read_bytes() == data:
                return alt, True
            n += 1

    @staticmethod
    def _local_index(local_md: Path, renamed: dict[str, str]) -> list[str]:
        """A local MEMORY.md's lines, right-stripped, with links to a note kept under a second name
        pointing at that name."""
        lines = [line.rstrip() for line in _read_lines(local_md)]
        for rel, alt in renamed.items():
            lines = [line.replace(f"]({rel})", f"]({alt})") for line in lines]
        return lines

    def merge_index(self, local_md: Path, shared_md: Path, renamed: dict[str, str],
                    since: list[str] | None) -> tuple[bool, list[str]]:
        """Add the local MEMORY.md's lines that are new since its last merge (`since`: its lines
        then; None: all of them) and that the shared one lacks, at its end (blank lines and the
        header aside). A line the owner deleted from the store is in `since`, so an old copy of it
        here never brings it back. Returns (whether the shared index changed, the local index's
        lines now, for the record)."""
        lines = self._local_index(local_md, renamed)
        now = _index_texts(lines)
        have = _read_lines(shared_md) if os.path.lexists(shared_md) else None
        if have is None and since is None:
            if not self.dry:
                _write_lines(shared_md, lines)
            return True, now
        seen = {line.strip() for line in have or [] if line.strip()} | set(since or ())
        add = []
        for line in lines:
            text = line.strip()
            if text and text != HEADER and text not in seen:
                seen.add(text)
                add.append(line)
        if not add:
            return False, now
        have = have or []
        while have and not have[-1].strip():
            have.pop()
        if not self.dry:
            _write_lines(shared_md, have + add)
        return True, now

    def _not_a_workspace(self, folder: Path) -> str | None:
        """Why a project folder is never pointed (its notes still merge), or None: it holds a
        BrainHub root (the live copy is never a workspace, design §5.1; a legacy root has only
        brain/map.json), or it is on the shared drive (the store's parent) outside this OS's
        projects root, where sessions on both OSes would read its one settings.local.json. On
        Windows the projects root (D:\\) is on that drive itself, so there only the first applies."""
        for marker in ("data/root.json", "brain/map.json"):
            if os.path.lexists(folder / marker):
                return f"holds a BrainHub root ({marker})"
        drive = self.shared.parent
        if _under(folder, drive) and not _under(folder, self.root):
            return f"is on the shared drive ({drive})"
        return None

    def point(self, pair: Pair) -> bool:
        """Write autoMemoryDirectory = the pair's shared folder into its settings.local.json, keeping
        every other key. True when the file changed (or would, in a dry run). Never for a project
        folder that isn't a workspace (_not_a_workspace), and never over a value of the other OS's
        path shape: that OS's sessions use it."""
        if pair.project is not None:
            why = self._not_a_workspace(pair.project)
            if why:
                self.log(f"memory: {pair.name}: {pair.project} {why}; its notes are merged, but it isn't pointed")
                return False
        target, path = str(pair.shared), pair.settings_local
        try:
            obj = read_json_obj(path)
        except BadFile as e:
            self.problem("memory", f"{e}; left alone, so {pair.name} keeps its memory on this OS")
            return False
        obj = obj if obj is not None else {}
        current = obj.get("autoMemoryDirectory")
        if current != target and other_os_path(current, self.os):
            self.log(f"memory: {pair.name}: {path} names {current!r}, a path of the other OS, whose "
                     "sessions read it; left as it is")
            return False
        changed = current != target
        if changed:
            if "autoMemoryDirectory" in obj:
                self.log(f"memory: {pair.name}: autoMemoryDirectory was {obj['autoMemoryDirectory']!r}")
            if not self.dry:
                pair.shared.mkdir(parents=True, exist_ok=True)
                if path.exists():
                    self.backup_file(path, f"{pair.name}.settings.local.json")
                write_json_atomic(path, {**obj, "autoMemoryDirectory": target})
            self.log(f"memory: {pair.name} now uses {target}")
        if pair.project is not None and not self.dry:
            self.exclude_from_git(pair.project)
        return changed

    def exclude_from_git(self, project: Path) -> None:
        """Keep .claude/settings.local.json out of the project's git status (an untracked file there
        would make a checkout look dirty to the update step): add it to the repo's info/exclude,
        which is never committed, when git doesn't ignore it already. The file is the one `git
        rev-parse --git-path info/exclude` names, so a worktree (whose .git is a file) uses its main
        repo's, which every worktree reads. Quietly nothing for a folder that isn't a repo's top."""
        if not os.path.lexists(project / ".git"):
            return
        rel, git = ".claude/settings.local.json", Git(project)
        r = git("check-ignore", "-q", "--", rel)
        if r.returncode != 1:  # 0: ignored already; anything else: git couldn't tell, so leave it
            return
        where = git("rev-parse", "--git-path", "info/exclude")
        if where.returncode != 0 or not where.stdout.strip():
            return
        exclude, line = project / where.stdout.strip(), "/" + rel
        try:
            text = exclude.read_text("utf-8") if exclude.exists() else ""
            if line in text.splitlines():
                return
            exclude.parent.mkdir(exist_ok=True)
            with open(exclude, "a", encoding="utf-8", newline="\n") as f:
                f.write(("\n" if text and not text.endswith("\n") else "") + line + "\n")
            self.log(f"memory: {line} added to {exclude}")
        except OSError as e:
            self.problem("memory", f"can't add {line} to {exclude}: {e}")

    # -- 3. header ----------------------------------------------------------------------------------
    def ensure_headers(self) -> int:
        """Exactly one HEADER line, at the top of every shared MEMORY.md. Returns how many changed."""
        changed = 0
        files = [self.shared / "memory" / "home" / INDEX]
        files += [d / INDEX for d in _dirs(self.shared / "memory" / "projects")]
        for md in files:
            if not md.is_file():
                continue
            try:
                lines = _read_lines(md)
                fixed = [HEADER, *(line for line in lines if line.strip() != HEADER)]
                if fixed != lines:
                    if not self.dry:
                        _write_lines(md, fixed)
                    changed += 1
            except (OSError, UnicodeError) as e:
                self.problem("memory", f"{md}: {_err(e)}")
        return changed

    # -- 4. skills ----------------------------------------------------------------------------------
    def sync_skills(self) -> dict:
        out = {"moved": [], "linked": [], "conflicts": []}
        local_root, store_root = self.claude / "skills", self.shared / "skills"
        managed = set(MANAGED_SKILLS) | {p.name for p in _entries(self.claude / "plugins")}
        for entry in _entries(local_root):
            name = entry.name
            if name.startswith(".") or name in managed or is_link(entry) or not entry.is_dir():
                continue
            try:
                self._adopt_skill(entry, store_root / name, out)
            except Exception as e:
                self.problem("skills", f"{name}: {_err(e)}")
        for skill in _dirs(store_root):
            name, link = skill.name, local_root / skill.name
            if name.startswith(".") or name in managed or os.path.lexists(link):
                continue
            try:
                if not self.dry:
                    local_root.mkdir(parents=True, exist_ok=True)
                    make_link(skill, link)
                out["linked"].append(name)
                self.log(f"skills: linked {name}")
            except Exception as e:
                self.problem("skills", f"{name}: can't link it in: {_err(e)}")
        return out

    def _adopt_skill(self, entry: Path, dst: Path, out: dict) -> None:
        """Move a local skill into the store: copy it there (unless the store's copy is identical),
        move the original to the backups, and link the store's copy in its place."""
        name = entry.name
        fresh = not os.path.lexists(dst)
        if not fresh and not (dst.is_dir() and trees_equal(entry, dst)):
            out["conflicts"].append(name)
            self.problem("skills", f"{name} differs from the shared skill of that name; this OS's copy is "
                                   "left as it is")
            return
        if not self.dry:
            if fresh:
                tmp = dst.with_name(f".{name}.claude-sync-{os.getpid()}.tmp")
                try:
                    shutil.copytree(entry, tmp, symlinks=True)
                    os.rename(tmp, dst)
                except BaseException:
                    shutil.rmtree(tmp, ignore_errors=True)  # our own partial copy; the original is intact
                    raise
            backup = self._backup_path(f"skills/{name}")
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(entry), str(backup))
            try:
                make_link(dst, entry)
            except BaseException:
                shutil.move(str(backup), str(entry))  # put the skill back where it was
                raise
        out["moved" if fresh else "linked"].append(name)
        self.log(f"skills: {'moved' if fresh else 'linked'} {name} (the original is in the backups)")

    # -- 5. settings --------------------------------------------------------------------------------
    def sync_settings(self) -> dict:
        out = {"ran_at": self.started, "recorded": [], "applied": [], "written": False}
        path = self.claude / "settings.json"
        try:
            local = read_json_obj(path)
            store = read_store(self.shared)
        except BadFile as e:
            self.problem("settings", f"{e}; settings not synced")
            return out
        exists, local = local is not None, local or {}
        per_os = set(DEFAULT_PER_OS_KEYS) | set(store["per_os_keys"])
        entries: dict = store["settings"]
        store_changed = False
        for key in [k for k, e in entries.items() if k in per_os or holds_abs_path(e["value"])]:
            del entries[key]  # never kept: a per-OS key (added by the owner since) or a path
            store_changed = True

        # Record the local changes since the last sync, timed by the file's modification time.
        when = _iso(path.stat().st_mtime) if exists else self.started
        snap = self.snapshot
        for key in sorted(set(local) | set(snap or {})):
            mine = local.get(key, _MISSING)
            if key in per_os or (mine is not _MISSING and holds_abs_path(mine)):
                continue
            if mine is _MISSING and snap is not None and holds_abs_path(snap.get(key)):
                continue  # a key that held a path here was never shared, so neither is its removal
            entry = entries.get(key)
            if snap is not None:
                if mine == snap.get(key, _MISSING) and not (entry is None and mine is not _MISSING):
                    continue  # unchanged here (re-recorded only if the store lost it)
            elif mine is _MISSING or (entry is not None and not entry["deleted"] and entry["value"] == mine):
                continue  # first sync: a missing key isn't a removal; an equal value isn't a change
            if entry is not None and entry["changed_at"] > when:
                continue  # a later change made on the other OS wins
            entries[key] = {"value": None if mine is _MISSING else mine, "changed_at": when,
                            "deleted": mine is _MISSING, "os": self.os}
            store_changed = True
            out["recorded"].append(key)

        # Apply the store: values and removals, never to a per-OS key.
        new = dict(local)
        for key, entry in sorted(entries.items()):
            mine = new.get(key, _MISSING)
            if key in per_os or (mine is not _MISSING and holds_abs_path(mine)):
                continue
            if entry["deleted"]:
                if mine is not _MISSING:
                    del new[key]
                    out["applied"].append(key)
            elif mine != entry["value"]:
                new[key] = entry["value"]
                out["applied"].append(key)
        if new != local:
            if not self.dry:
                if exists:
                    self.backup_file(path, "settings.json")
                write_json_atomic(path, new)
            out["written"] = True
        if store_changed and not self.dry:
            write_store(self.shared, store)
        if out["recorded"] or out["applied"]:
            self.log(f"settings: recorded {', '.join(out['recorded']) or 'nothing'}; "
                     f"applied {', '.join(out['applied']) or 'nothing'}")
        if not self.dry:
            self.snapshot = new
        return out

    # -- 6. git backup ------------------------------------------------------------------------------
    def git_backup(self) -> dict:
        out = {"repo": False, "committed": None, "pushed": None, "error": None}
        if not (self.shared / ".git").exists():
            self.log("git: the store isn't a git repo (claude-sync init makes one); nothing committed")
            return out
        out["repo"] = True
        if self.dry:
            return out
        git = Git(self.shared)
        status = git("status", "--porcelain", "--untracked-files=all")
        if status.returncode != 0:
            self.problem("git", f"status failed: {_masked(status.stderr)}")
            return out
        if status.stdout.strip():
            for args in (("add", "-A"), ("commit", "-q", "-m", f"claude-sync {self.os} {self.started}")):
                r = git(*args)
                if r.returncode != 0:
                    self.problem("git", f"{args[0]} failed: {_masked(r.stderr or r.stdout)}")
                    return out
            out["committed"] = git("rev-parse", "--short", "HEAD").stdout.strip() or None
        origin = git("config", "--get", "remote.origin.url")
        if origin.returncode != 0 or not origin.stdout.strip():
            self.push_pending = False  # nowhere to push to (the owner adds origin)
            return out
        if out["committed"] or self.push_pending:
            ok, error = git.push()
            out["pushed"], self.push_pending = ok, not ok
            if not ok:  # quiet: the log and the summary; retried at the next sync
                out["error"] = error or "push failed"
                self.log(f"git: push failed: {out['error']}")
        return out

    # -- the whole sync -------------------------------------------------------------------------------
    def run(self, settings_only: bool = False) -> dict:
        previous = {}
        if settings_only:
            try:
                previous = read_json_obj(self.state / SUMMARY_FILE) or {}
            except BadFile:
                previous = {}
        summary = {**previous, "ran_at": self.started, "os": self.os, "shared": str(self.shared),
                   "available": True, "reason": None, "dry_run": self.dry}
        if not self.claude.is_dir():
            self.problem("memory", f"no Claude Code folder at {self.claude}; nothing synced")
        else:
            if not settings_only:
                summary["memory"] = self._part("memory", self.sync_memory)
                summary["skills"] = self._part("skills", self.sync_skills)
            summary["settings"] = self._part("settings", self.sync_settings)
        summary["git"] = self._part("git", self.git_backup)
        kept = previous.get("problems") if isinstance(previous.get("problems"), dict) else {}
        summary["problems"] = {**{k: v for k, v in kept.items() if k in ("memory", "skills")}, **self.problems}
        if not self.dry:
            self._save(summary)
        return summary

    def _save(self, summary: dict) -> None:
        last = {**self.last, "merged": self.merged, "push_pending": self.push_pending}
        if self.snapshot is not None:
            last["settings"] = self.snapshot
        write_json_atomic(self.state / LAST_FILE, last, ascii=True)
        write_json_atomic(self.state / SUMMARY_FILE, summary, ascii=True)
        write_log(self.state, self.started, self.os, [*self.lines, summary_line(summary)])


def write_log(state: Path, started: str, os_name: str, lines: list[str]) -> None:
    path = Path(state) / LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size > LOG_MAX_BYTES:
            os.replace(path, path.with_name(LOG_FILE + ".1"))
    except OSError:
        pass
    with open(path, "a", encoding="utf-8", errors="backslashreplace", newline="\n") as f:
        for line in lines:
            f.write(f"{started} {os_name} {line}\n")


def summary_line(summary: dict) -> str:
    """One line for the shell's log and a hand run."""
    if summary.get("skipped"):
        return f"claude-sync: skipped ({summary['skipped']})"
    if not summary.get("available"):
        return f"claude-sync: nothing changed ({summary.get('reason')})"
    parts = []
    mem = summary.get("memory")
    if isinstance(mem, dict) and "pairs" in mem:
        parts.append(f"memory shared for {len(mem['pairs'])} (newly pointed {len(mem['pointed'])}, "
                     f"merged {len(mem['merged'])})")
    sk = summary.get("skills")
    if isinstance(sk, dict) and "moved" in sk:
        parts.append(f"skills moved {len(sk['moved'])}, linked {len(sk['linked'])}")
    st = summary.get("settings")
    if isinstance(st, dict) and "recorded" in st:
        parts.append(f"settings recorded {len(st['recorded'])}, applied {len(st['applied'])}")
    git = summary.get("git") or {}
    if git.get("committed"):
        parts.append(f"committed {git['committed']}")
    if git.get("pushed") is False:
        parts.append("push failed")
    problems = sum(len(v) for v in (summary.get("problems") or {}).values())
    if problems:
        parts.append(f"{problems} problem{'s' if problems != 1 else ''} (see claude-sync.log)")
    return "claude-sync: " + ("; ".join(parts) or "nothing to do")


def run(shared=None, claude_dir=None, state=None, *, home=None, projects_root=None, os_name=None,
        dry_run: bool = False, settings_only: bool = False, now=datetime.now) -> dict:
    """One sync (see the module docstring). Returns the summary; never raises for a problem it can
    report. A store that can't be used changes nothing."""
    shared = _abs(shared or default_shared_dir())
    claude = _abs(claude_dir or default_claude_dir())
    state = _abs(state or osdirs.state_dir())
    os_name = os_name or osdirs.os_name()
    reason = unavailable_reason(shared, probe=not dry_run)
    if reason:
        summary = {"ran_at": _now_iso(now), "os": os_name, "shared": str(shared), "available": False,
                   "reason": reason, "dry_run": dry_run}
        if not dry_run:
            write_json_atomic(state / SUMMARY_FILE, summary)
            write_log(state, summary["ran_at"], os_name, [summary_line(summary)])
        return summary
    sync = Sync(shared, claude, state, home=_abs(home or Path.home()),
                projects_root=_abs(projects_root or default_projects_root()), os_name=os_name,
                dry_run=dry_run, now=now)
    if dry_run:
        return sync.run(settings_only)
    lock = take_lock(state)
    if lock is None:
        return {"skipped": "claude-sync already running"}
    try:
        return sync.run(settings_only)
    finally:
        release_lock(lock)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["init"]:
        ap = argparse.ArgumentParser(
            prog="brainhub claude-sync init",
            description="Set up the shared Claude store once (folders, `* -text`, a git repo). Add the "
                        "private backup repo afterwards with `git -C <dir> remote add origin <url>`.")
        ap.add_argument("--shared", required=True, type=Path, help="the store's folder (its parent must exist)")
        args = ap.parse_args(argv[1:])
        try:
            path = init_store(args.shared)
        except OSError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"claude-sync: store ready at {path}")
        return 0
    ap = argparse.ArgumentParser(
        prog="brainhub claude-sync",
        description="Share Claude Code memory, skills and portable settings with the other OS through "
                    "the shared store (set it up once with `claude-sync init --shared <dir>`).")
    ap.add_argument("--shared", type=Path, default=None,
                    help="the shared store (default $BRAINHUB_SHARED_DIR, else /mnt/data/ClaudeShared "
                         "or D:\\ClaudeShared)")
    ap.add_argument("--claude-dir", type=Path, default=None,
                    help="Claude Code's folder (default $CLAUDE_CONFIG_DIR, else ~/.claude)")
    ap.add_argument("--projects-root", type=Path, default=None,
                    help="where the projects live (default ~/dev on Linux, D:\\ on Windows)")
    ap.add_argument("--home", type=Path, default=None,
                    help="the home folder (default ~): its key is the home key, and its "
                         ".claude/settings.local.json gets the home memory folder")
    ap.add_argument("--dry-run", action="store_true", help="print what would change; write nothing")
    ap.add_argument("--settings-only", action="store_true",
                    help="only the settings sync and the commit (the shell's settings.json watch)")
    args = ap.parse_args(argv)
    try:
        summary = run(args.shared, args.claude_dir, home=args.home, projects_root=args.projects_root,
                      dry_run=args.dry_run, settings_only=args.settings_only)
    except Exception as e:  # never in the way of the shell's start
        print(f"claude-sync: failed: {_err(e)}", file=sys.stderr)
        return 0
    print(json.dumps(summary, indent=2, ensure_ascii=False) if args.dry_run else summary_line(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
