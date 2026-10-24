"""BrainHub's update step (dual-boot design §5.2; plan decisions P2, P3, P4 and P9; ruling D9).

The desktop shell runs it before every hub start, as a file outside the package:

    python -I -S update_step.py --live <dir> --state <dir> [--timeout 20] [--budget 75]

Stdlib imports only: never import anything from ``brainhub``. It prints one JSON line,
``{"pulled", "slot", "commit", "pending"}``, writes the same plus ``checked`` (local time),
``shell_source`` and ``stderr`` (the tail of the last failed git, gh or uv call when something
is pending) to ``<state>/update.json``, and always exits 0, except on a usage error (2).

``--timeout`` caps each network call (fetch, gh). ``--budget`` caps the whole step: every
subprocess gets at most the time left, and a fast-forward or a venv sync that may not finish in
it is put off to the next start. Only one step runs per state folder at a time
(``runtime/update.lock``); a second one says "update already running" and touches nothing.
On POSIX, SIGTERM (the shell's own time limit) kills the running git, gh or uv with its process
tree (each runs in a session of its own, out of the signal's reach) and ends the step at once.

1. **Pull:** fast-forward the live copy to ``origin/master``, only when the live copy is a clean
   checkout of master, the fetch worked, ``origin/master`` is a fast-forward of HEAD, and CI
   finished green on both OSes for that exact commit (P9 as amended by D9). It never resets,
   stashes, checks out, cleans, or merges other than ``--ff-only``, and never overwrites an
   ignored file (the live copy's ``brain/`` and ``data/`` hold the owner's data).
2. **Refresh:** when the current runtime slot isn't the live HEAD, build the other slot (P2):
   its ``app/`` from ``git archive``, its venv with ``uv sync --frozen --no-install-project``
   (kept when the dependencies are unchanged). Only a HEAD on branch master that the local
   ``origin/master`` has (equal or behind) is built, so a slot only ever runs a commit CI could
   have checked; a dirty tree still builds (the archive is of the commit). Only when both parts
   worked, ``runtime/current.json`` flips to it. Any failure leaves the old slot current.
3. **Hook:** the current slot's hook client is copied to ``<state>/hook/hook_client.py`` (P4)
   whenever that copy differs, on every run.

Any skipped part is quiet: ``pending`` says why (Settings shows "update pending (reason)"), and
the hub starts on the slot it already has.

Layout under the state folder: ``runtime/current.json`` = ``{"slot": "a"|"b", "commit", "deps"}``
names ``runtime/slot-<slot>/`` (``app/`` + ``venv/``); each slot's ``slot.json`` records which
commit its ``app/`` holds and which ``deps`` hash its venv was last synced for. A slot is current
only while its ``slot.json`` names the same commit as ``current.json``.
"""
import argparse
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from datetime import datetime

# The CI gate (P9, D9): every check run of the commit has completed, none with another
# conclusion than these, and both of the required ones succeeded.
REQUIRED_CHECKS = ("test (ubuntu-latest)", "test (windows-latest)")
OK_CONCLUSIONS = ("success", "skipped", "neutral")
SLOTS = ("a", "b")
TIMEOUT_S = 20  # default for each network call (fetch, gh)
BUDGET_S = 75  # default for the whole step; the shell gives it 90 s
LOCAL_TIMEOUT_S = 60  # git calls that don't use the network (a cold NTFS index can be slow)
MERGE_MIN_S = 15  # never start a fast-forward that the budget could kill halfway
SYNC_MIN_S = 30  # nor a venv sync with less time left than this
KILL_WAIT_S = 5
STDERR_TAIL = 500
REPLACE_TRIES = 10  # Windows: a reader holding the file open makes os.replace fail for a moment
REPLACE_PAUSE_S = 0.2

_WIN = sys.platform == "win32"
_NO_WINDOW = 0x08000000 if _WIN else 0  # CREATE_NO_WINDOW (Ruling 13)
# Env vars that would point git at another repo than the one we name.
_GIT_REPO_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR")
_SLUG = re.compile(
    r"^(?:https?://(?:[^@/]+@)?|ssh://(?:[^@/]+@)?|[\w.-]+@)github\.com[:/]"
    r"([\w.-]+)/([\w.-]+?)(?:\.git)?/*$")
_URL_CREDENTIALS = re.compile(r"(://)[^/@\s]+@")


class Pending(Exception):
    """Why a part of the step was skipped or failed (shown in Settings)."""


# -- processes ---------------------------------------------------------------------------------

def child_env():
    """The environment for git, gh and uv: never a prompt (terminal, credential manager or ssh
    askpass window), and no inherited variable that redirects git to another repo."""
    env = dict(os.environ)
    for name in _GIT_REPO_VARS:
        env.pop(name, None)
    env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", SSH_ASKPASS_REQUIRE="never",
               GH_PROMPT_DISABLED="1", GH_NO_UPDATE_NOTIFIER="1")
    return env


def find_exe(name, path):
    """`name` on `path`; uv also in ~/.local/bin (`uv.exe` on Windows), where its installer puts
    it and which a shell started at login may not have on PATH (as P6 does for claude)."""
    exe = shutil.which(name, path=path)
    if exe is None and name == "uv":
        try:
            local = os.path.join(os.path.expanduser("~"), ".local", "bin", "uv.exe" if _WIN else "uv")
        except Exception:
            return None
        if os.path.isfile(local) and os.access(local, os.X_OK):
            exe = local
    return exe


def _kill_tree(proc):
    """Kill `proc` and everything it started: its process group (it leads its own session) on
    POSIX, its process tree on Windows."""
    try:
        if _WIN:
            taskkill = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")
            subprocess.run([taskkill, "/T", "/F", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=_NO_WINDOW, timeout=KILL_WAIT_S)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def stderr_tail(err):
    """The last STDERR_TAIL characters of a call's stderr, with any credentials in URLs masked."""
    text = _URL_CREDENTIALS.sub(r"\1***@", err.decode("utf-8", "replace")).strip()
    return text[-STDERR_TAIL:]


class Runner:
    """Runs git, gh and uv under one deadline, and keeps the stderr tail of the last call that
    failed with something on stderr."""

    def __init__(self, env, timeout, budget):
        self.env = env
        self.timeout = timeout  # per network call
        self.deadline = time.monotonic() + budget
        self.stderr = None
        self.proc = None  # the call running now, for the SIGTERM handler (on_sigterm)

    def left(self):
        return self.deadline - time.monotonic()

    def run(self, cmd, *, cwd, what, cap, env=None):
        """Run `cmd` with stdin closed and output captured, for at most min(`cap`, the time left).
        Returns (exit code, stdout bytes). On timeout the whole process tree is killed and
        Pending("<what> timed out") is raised."""
        env = self.env if env is None else env
        limit = min(cap, self.left())
        if limit <= 0:
            raise Pending("out of time")
        exe = find_exe(cmd[0], env.get("PATH"))
        if exe is None:
            raise Pending(f"{cmd[0]} not found")
        # POSIX: its own session (so its own process group), so a timeout kills git's helpers
        # and uv's children too, and ssh has no terminal to prompt on.
        try:
            proc = subprocess.Popen([exe, *cmd[1:]], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    creationflags=_NO_WINDOW, start_new_session=not _WIN)
        except OSError as e:
            raise Pending(f"{cmd[0]} could not start ({type(e).__name__})") from None
        self.proc = proc
        try:
            out, err = proc.communicate(timeout=limit)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            try:
                proc.communicate(timeout=KILL_WAIT_S)
            except Exception:
                pass
            raise Pending(f"{what} timed out") from None
        finally:
            self.proc = None
        if proc.returncode != 0 and err.strip():
            self.stderr = stderr_tail(err)
        return proc.returncode, out


# No background gc or maintenance on the live copy: fetch and merge may start one that detaches
# itself, outlives the budget's kill, and keeps writing to the shared drive while the hub starts.
NO_BACKGROUND = ("-c", "gc.auto=0", "-c", "maintenance.auto=false")
# This OS's case rule, whatever the shared .git/config says (D7): the live copy was cloned by
# Windows git (core.ignorecase=true), and on Linux a fast-forward with a case-only rename would
# otherwise leave the working tree, which the shell is built from, out of step.
CASE_RULE = ("-c", "core.ignorecase=" + ("true" if sys.platform == "win32" else "false"))


class Live:
    """git on the live copy. Every call names it as a safe directory (the other OS's files may
    have another owner, design §5.4) and starts no background gc or maintenance."""

    def __init__(self, path, runner):
        self.path = os.path.abspath(path)
        self.runner = runner
        self.safe = "safe.directory=" + os.path.realpath(self.path).replace("\\", "/")

    def git(self, *args, what="git", network=False):
        cap = self.runner.timeout if network else LOCAL_TIMEOUT_S
        return self.runner.run(["git", "-c", self.safe, *NO_BACKGROUND, *CASE_RULE, *args], cwd=self.path,
                               what=what, cap=cap)

    def rev(self, ref):
        code, out = self.git("rev-parse", "--verify", "-q", ref)
        sha = out.decode("ascii", "replace").strip()
        return sha if code == 0 and sha else None

    def check(self):
        if not os.path.isdir(self.path):
            raise Pending("live copy not found")
        if not os.path.exists(os.path.join(self.path, ".git")) or self.rev("HEAD") is None:
            raise Pending("live copy is not a git repository")


def on_master(live):
    """Raise Pending unless the live copy's HEAD is the branch master."""
    code, out = live.git("symbolic-ref", "--short", "-q", "HEAD")
    branch = out.decode("utf-8", "replace").strip() if code == 0 else ""
    if branch != "master":
        raise Pending(f"on branch {branch}, not master" if branch else "detached HEAD, not master")


def fast_forward_of(live, head, remote):
    """Raise Pending unless `remote` is `head` or a fast-forward of it."""
    if head == remote:
        return
    code, _ = live.git("merge-base", "--is-ancestor", head, remote)
    if code == 1:
        raise Pending("diverged history")
    if code != 0:
        raise Pending("git merge-base failed")


def check_provenance(live, head):
    """The refresh's guard (D9): build only a HEAD on branch master that the local
    ``origin/master`` has (equal to it or behind it), never a commit made in the live copy, so a
    slot only runs a commit CI could have checked. It uses no network, so an offline start still
    builds what the other OS pulled. Same reasons, same words as the pull's checks."""
    on_master(live)
    remote = live.rev("refs/remotes/origin/master")
    if remote is None:
        raise Pending("no origin/master")
    fast_forward_of(live, head, remote)


def provenance_reason(live_path):
    """`runtime init`'s up-front check: None when the live HEAD may be built, else why not."""
    live = Live(live_path, Runner(child_env(), TIMEOUT_S, 2 * LOCAL_TIMEOUT_S))
    try:
        live.check()
        check_provenance(live, live.rev("HEAD"))
    except Pending as e:
        return str(e)
    return None


# -- part 1: pull ------------------------------------------------------------------------------

def repo_slug(url):
    """`owner/repo` from a GitHub remote URL (https or ssh form), else None."""
    m = _SLUG.match(url.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def ci_verdict(payload):
    """None when the check runs of a commit (GitHub's check-runs JSON, latest run per name) are
    green under P9 as amended by D9, else why not."""
    runs = payload.get("check_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list) or not all(isinstance(r, dict) for r in runs):
        return "CI status unreadable"
    total = payload.get("total_count")
    if type(total) is not int or total > len(runs):
        return "CI status unreadable (not every check run listed)"
    if any(r.get("status") == "completed" and r.get("conclusion") not in OK_CONCLUSIONS for r in runs):
        return "CI failed"
    if any(r.get("status") != "completed" for r in runs):
        return "CI pending"
    for name in REQUIRED_CHECKS:
        named = [r for r in runs if r.get("name") == name]
        if not named:
            return "CI pending"
        if not any(r.get("conclusion") == "success" for r in named):
            return "CI not green"
    return None


def ci_gate(live, sha):
    """None when CI is green for `sha`, else why not. Anything unreadable means "not green"."""
    # The configured URL, not `git remote get-url`: that one expands url.<base>.insteadOf, which
    # can turn the GitHub URL into a mirror or local path that names no GitHub repo.
    code, out = live.git("config", "--get", "remote.origin.url")
    slug = repo_slug(out.decode("utf-8", "replace")) if code == 0 else None
    if not slug:
        return "CI status unreadable (origin is not a GitHub repo)"
    # The query as fields of a GET (`?per_page=100&filter=latest`): an `&` on the command line
    # would split it if gh were a .cmd shim.
    try:
        code, out = live.runner.run(
            ["gh", "api", "--method", "GET", f"repos/{slug}/commits/{sha}/check-runs",
             "-f", "per_page=100", "-f", "filter=latest"],
            cwd=live.path, what="CI status", cap=live.runner.timeout)
    except Pending as e:
        return f"CI status unreadable ({e})"
    if code != 0:
        return f"CI status unreadable (gh exit {code})"
    try:
        payload = json.loads(out.decode("utf-8"))
    except ValueError:
        return "CI status unreadable"
    return ci_verdict(payload)


def pull(live):
    """Fast-forward the live copy when every check passes. Returns True when it moved; raises
    Pending with the reason when it didn't but should have. Up to date is not pending."""
    live.check()
    code, out = live.git("status", "--porcelain", "--untracked-files=normal")
    if code != 0:
        raise Pending("git status failed")
    if out.strip():
        raise Pending("dirty working tree")
    on_master(live)
    head = live.rev("HEAD")
    code, _ = live.git("fetch", "--quiet", "--no-tags", "origin", "master", what="fetch", network=True)
    if code != 0:
        raise Pending("fetch failed")
    remote = live.rev("refs/remotes/origin/master")
    if remote is None:
        raise Pending("fetch failed")
    if remote == head:
        return False
    fast_forward_of(live, head, remote)
    reason = ci_gate(live, remote)
    if reason:
        raise Pending(reason)
    if live.runner.left() < MERGE_MIN_S:
        raise Pending("fast-forward put off (not enough time)")
    # The exact sha CI checked. --no-autostash: a merge.autoStash setting must never make it
    # stash. --no-overwrite-ignore: if the commit tracks a file the live copy has as an ignored
    # one (brain/, data/), git refuses instead of replacing the owner's file.
    code, _ = live.git("merge", "--ff-only", "--no-autostash", "--no-overwrite-ignore", "--quiet", remote,
                       what="fast-forward")
    if code != 0 or live.rev("HEAD") != remote:
        raise Pending("fast-forward failed")
    return True


# -- part 2: refresh ---------------------------------------------------------------------------

def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _replace(src, dst):
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(REPLACE_PAUSE_S)


def write_bytes(path, data):
    """Atomic: a temp file beside it (named for this process), flushed to disk, then replaced."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        _replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_json(path, data):
    write_bytes(path, (json.dumps(data, indent=2) + "\n").encode("utf-8"))


def slot_dir(state, slot):
    return os.path.join(state, "runtime", f"slot-{slot}")


def venv_python(slot_path):
    venv = os.path.join(slot_path, "venv")
    return os.path.join(venv, "Scripts", "python.exe") if _WIN else os.path.join(venv, "bin", "python")


def current_slot(state):
    """The current slot's record, or None when there is no usable one: `current.json` must name
    a slot whose `slot.json` holds the same commit (a wiped or half-built slot never counts), with
    an `app/` and a venv python."""
    cur = read_json(os.path.join(state, "runtime", "current.json"))
    if not (cur and cur.get("slot") in SLOTS and isinstance(cur.get("commit"), str)):
        return None
    path = slot_dir(state, cur["slot"])
    record = read_json(os.path.join(path, "slot.json")) or {}
    if (record.get("commit") == cur["commit"] and os.path.isdir(os.path.join(path, "app"))
            and os.path.exists(venv_python(path))):
        return cur
    return None


def deps_hash(app_dir):
    """sha256 over app/pyproject.toml, app/uv.lock and app/.python-version (when present): what
    the venv was synced from."""
    h = hashlib.sha256()
    for name in ("pyproject.toml", "uv.lock", ".python-version"):
        try:
            with open(os.path.join(app_dir, name), "rb") as f:
                data = f.read()
        except FileNotFoundError:
            if name == ".python-version":
                continue
            raise Pending(f"no app/{name} in the live commit") from None
        h.update(name.encode("ascii") + b"\0" + str(len(data)).encode("ascii") + b"\0" + data)
    return h.hexdigest()


def extract_app(data, dest):
    """Unpack a `git archive <sha> app` tar into `dest`. Only plain files and folders under
    app/: an absolute, `..`, link, `:` (an NTFS stream or drive) or other member fails it before
    anything is written."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tf:
        members = tf.getmembers()
        for m in members:
            parts = m.name.replace("\\", "/").split("/")
            if (os.path.isabs(m.name) or m.name.startswith(("/", "\\")) or any(":" in p for p in parts)
                    or ".." in parts or parts[0] != "app" or not (m.isfile() or m.isdir())):
                raise Pending(f"unsafe archive member {m.name[:100]!r}")
        tf.extractall(dest, members=members, filter="data")


def _rmtree(path):
    def retry_writable(func, p, exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if os.path.lexists(path):
        shutil.rmtree(path, onexc=retry_writable)


def sync_venv(slot_path, runner):
    """`uv sync` the slot's venv. Its only time limit is the budget left."""
    venv_env = dict(runner.env, UV_PROJECT_ENVIRONMENT=os.path.join(slot_path, "venv"))
    venv_env.pop("VIRTUAL_ENV", None)  # the shell's or a dev venv, not this one
    code, _ = runner.run(["uv", "sync", "--frozen", "--no-install-project"],
                         cwd=os.path.join(slot_path, "app"), env=venv_env, what="venv sync",
                         cap=float("inf"))
    if code != 0:
        raise Pending(f"venv sync failed (uv exit {code})")
    if not os.path.exists(venv_python(slot_path)):
        raise Pending("venv sync failed (no python in the venv)")


def refresh(live, state):
    """Build the inactive slot from the live HEAD and flip to it, unless the current slot
    already holds that commit or the HEAD fails the provenance guard. The current slot is never
    touched."""
    live.check()
    head = live.rev("HEAD")
    cur = current_slot(state)
    if cur and cur["commit"] == head:
        return
    check_provenance(live, head)
    # The slot current.json names (even an unusable one) may be what runs this file: build the other.
    named = (read_json(os.path.join(state, "runtime", "current.json")) or {}).get("slot")
    slot = "b" if named == "a" else "a"
    path = slot_dir(state, slot)
    code, archive = live.git("archive", "--format=tar", head, "app", what="git archive")
    if code != 0:
        raise Pending("git archive failed")
    record = os.path.join(path, "slot.json")
    synced = (read_json(record) or {}).get("deps")
    write_json(record, {"commit": None, "deps": synced})  # its app/ is about to change
    _rmtree(os.path.join(path, "app"))
    extract_app(archive, path)
    deps = deps_hash(os.path.join(path, "app"))
    if deps != synced or not os.path.exists(venv_python(path)):
        if live.runner.left() < SYNC_MIN_S:
            raise Pending("venv sync put off (not enough time)")
        write_json(record, {"commit": None, "deps": None})
        sync_venv(path, live.runner)
    write_json(record, {"commit": head, "deps": deps})
    write_json(os.path.join(state, "runtime", "current.json"), {"slot": slot, "commit": head, "deps": deps})


HOOK_FILES = ("hook_client.py", "request_client.py")  # stdlib scripts run as files (P4; phase 3)


def install_hook(state, cur):
    """Copy the current slot's hook client and brainhub-request script to the stable
    `<state>/hook/` (P4), each when it differs. Run every time, so a copy that failed once is put
    right on the next start. A slot from before phase 3 has no request_client.py: skipped."""
    for name in HOOK_FILES:
        src = os.path.join(slot_dir(state, cur["slot"]), "app", "brainhub", name)
        dst = os.path.join(state, "hook", name)
        try:
            with open(src, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            if name == "hook_client.py":
                raise
            continue
        try:
            with open(dst, "rb") as f:
                if f.read() == data:
                    continue
        except OSError:
            pass
        write_bytes(dst, data)


SHELL_FILES = ("Cargo.toml", "Cargo.lock", "build.rs", "tauri.conf.json")
# `../dist` is the shell's starting pages (`frontendDist`, beside `src-tauri`; phase 4.5).
SHELL_FOLDERS = ("../dist", "capabilities", "src")


def _raise(err):
    raise err


def shell_source(live_path):
    """sha256 of the desktop shell's source in the live copy, which the shell compares with the
    hash its own build computed (its build.rs mirrors this). None when `src/` is missing or any
    file can't be read.

    Under `app/desktop/src-tauri`, the files are `Cargo.toml`, `Cargo.lock`, `build.rs` and
    `tauri.conf.json` (each one that exists) and every file under `src/`, `capabilities/` and
    `../dist/` (the starting pages), at any depth. Each file, in order of its `/`-separated path
    relative to `src-tauri` (compared as UTF-8 bytes; a starting page's path is
    `../dist/<name>`), adds `<path> NUL <byte length in decimal> NUL <bytes>` to one sha256; the result
    is its lowercase hex digest."""
    base = os.path.join(live_path, "app", "desktop", "src-tauri")
    try:
        if not os.path.isdir(os.path.join(base, "src")):
            return None
        files = [name for name in SHELL_FILES if os.path.isfile(os.path.join(base, name))]
        for top in SHELL_FOLDERS:
            if not os.path.isdir(os.path.join(base, top)):
                continue
            for folder, _, names in os.walk(os.path.join(base, top), onerror=_raise):
                files += [os.path.relpath(os.path.join(folder, n), base).replace(os.sep, "/") for n in names]
        h = hashlib.sha256()
        for name in sorted(rel.encode("utf-8", "surrogateescape") for rel in files):
            with open(os.path.join(base, *name.decode("utf-8", "surrogateescape").split("/")), "rb") as f:
                data = f.read()
            h.update(name + b"\0" + str(len(data)).encode("ascii") + b"\0" + data)
        return h.hexdigest()
    except (OSError, UnicodeError):
        return None


# -- the lock ----------------------------------------------------------------------------------

def take_lock(state):
    """A non-blocking exclusive lock on `<state>/runtime/update.lock`: the open fd, or None when
    another update step (or `runtime init`) holds it. The OS drops the lock when the holder
    dies, so it never goes stale. Raises Pending when the file can't be opened."""
    path = os.path.join(state, "runtime", "update.lock")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError:
        raise Pending("state folder not writable") from None
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


def release_lock(fd):
    try:
        if _WIN:
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        os.close(fd)


# -- the step ----------------------------------------------------------------------------------

def _result(state, pulled, pending):
    cur = current_slot(state)
    return {"pulled": pulled, "slot": cur["slot"] if cur else None,
            "commit": cur["commit"] if cur else None, "pending": pending}


def on_sigterm(runner):
    """POSIX: on SIGTERM, kill `runner`'s running call with its process tree and exit at once.

    The shell's time limit sends SIGTERM to the step's process group, but each git, gh or uv runs
    in a session of its own (see `Runner.run`), which the signal doesn't reach: without this they
    would outlive the step. The handler does nothing else: no lock to release (the OS drops it),
    no update.json (a half-built slot never counts as current)."""
    if _WIN:
        return
    import signal

    def handler(signum, frame):
        proc = runner.proc
        if proc is not None:
            _kill_tree(proc)
        os._exit(0)

    signal.signal(signal.SIGTERM, handler)


def run_step(live_path, state, *, timeout=TIMEOUT_S, budget=BUDGET_S, pull_first=True, on_runner=None):
    """Pull (unless `pull_first` is False, as for `runtime init`), refresh, copy the hook client,
    record. Never raises. A busy lock touches nothing. `on_runner` gets the step's Runner before
    anything runs (`main` installs the SIGTERM handler with it)."""
    state = os.path.abspath(state)
    runner = Runner(child_env(), timeout, budget)
    if on_runner is not None:
        on_runner(runner)
    live = Live(live_path, runner)
    try:
        lock = take_lock(state)
    except Pending as e:
        return _result(state, False, str(e))
    if lock is None:
        return _result(state, False, "update already running")
    try:
        return _step(live, state, pull_first)
    finally:
        release_lock(lock)


def _step(live, state, pull_first):
    reasons = []

    def note(reason):
        if reason not in reasons:
            reasons.append(reason)

    pulled = False
    if pull_first:
        try:
            pulled = pull(live)
        except Pending as e:
            note(str(e))
        except Exception as e:
            note(f"pull failed ({type(e).__name__})")
    try:
        refresh(live, state)
    except Pending as e:
        note(str(e))
    except Exception as e:
        note(f"refresh failed ({type(e).__name__})")
    cur = current_slot(state)
    if cur:
        try:
            install_hook(state, cur)
        except Exception:
            note("hook client copy failed")
    result = _result(state, pulled, "; ".join(reasons) or None)
    try:
        write_json(os.path.join(state, "update.json"),
                   {**result, "checked": datetime.now().isoformat(timespec="seconds"),
                    "shell_source": shell_source(live.path),
                    "stderr": live.runner.stderr if reasons else None})
    except Exception:
        pass
    return result


def main(argv):
    ap = argparse.ArgumentParser(prog="update_step.py", description="BrainHub update step")
    ap.add_argument("--live", required=True, help="the live copy (a checkout of master)")
    ap.add_argument("--state", required=True, help="this OS's BrainHub state folder")
    ap.add_argument("--timeout", type=float, default=TIMEOUT_S, help="seconds per network call")
    ap.add_argument("--budget", type=float, default=BUDGET_S, help="seconds for the whole step")
    args = ap.parse_args(argv)
    if not args.live or not args.state or not args.timeout > 0 or not args.budget > 0:
        ap.error("--live and --state must be folders, --timeout and --budget positive")
    try:
        result = run_step(args.live, args.state, timeout=args.timeout, budget=args.budget,
                          on_runner=on_sigterm)
    except BaseException:  # never stop the shell from starting the hub
        result = {"pulled": False, "slot": None, "commit": None, "pending": "update step failed"}
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
