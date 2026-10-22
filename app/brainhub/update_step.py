"""BrainHub's update step (dual-boot design §5.2; plan decisions P2, P3, P4 and P9).

The desktop shell runs it before every hub start, as a file outside the package:

    python -I -S update_step.py --live <dir> --state <dir> [--timeout 60]

Stdlib imports only: never import anything from ``brainhub``. It prints one JSON line,
``{"pulled", "slot", "commit", "pending"}``, writes the same plus ``checked`` (local time) and
``shell_source`` to ``<state>/update.json``, and always exits 0, except on a usage error (2).

1. **Pull:** fast-forward the live copy to ``origin/master``, only when the live copy is a clean
   checkout of master, the fetch worked within the timeout, ``origin/master`` is a fast-forward of
   HEAD, and CI passed on both OSes for that exact commit (P9). It never resets, stashes, checks
   out, cleans, or merges other than ``--ff-only``.
2. **Refresh:** when the current runtime slot isn't the live HEAD, build the other slot (P2):
   its ``app/`` from ``git archive``, its venv with ``uv sync --frozen --no-install-project``
   (kept when the dependencies are unchanged). Only when both worked, ``runtime/current.json``
   flips to it and the hook client is copied to ``<state>/hook/hook_client.py`` (P4). Any
   failure leaves the old slot current.

Any skipped part is quiet: ``pending`` says why (Settings shows "update pending (reason)"), and
the hub starts on the slot it already has.

Layout under the state folder: ``runtime/current.json`` = ``{"slot": "a"|"b", "commit", "deps"}``
names ``runtime/slot-<slot>/`` (``app/`` + ``venv/``); each slot's ``slot.json`` records which
commit its ``app/`` holds and which ``deps`` hash its venv was last synced for.
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

# The CI gate (P9): these check runs must have passed, and no check run may have failed.
REQUIRED_CHECKS = ("test (ubuntu-latest)", "test (windows-latest)")
FAILED_CONCLUSIONS = ("failure", "cancelled", "timed_out", "action_required")
SLOTS = ("a", "b")
LOCAL_TIMEOUT_S = 60  # git calls that don't use the network (a cold NTFS index can be slow)
KILL_WAIT_S = 5
REPLACE_TRIES = 10  # Windows: a reader holding the file open makes os.replace fail for a moment
REPLACE_PAUSE_S = 0.2

_WIN = sys.platform == "win32"
_NO_WINDOW = 0x08000000 if _WIN else 0  # CREATE_NO_WINDOW (Ruling 13)
# Env vars that would point git at another repo than the one we name.
_GIT_REPO_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR")
_SLUG = re.compile(
    r"^(?:https?://(?:[^@/]+@)?|ssh://(?:[^@/]+@)?|[\w.-]+@)github\.com[:/]"
    r"([\w.-]+)/([\w.-]+?)(?:\.git)?/*$")


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


def _kill_tree(proc):
    try:
        if _WIN:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL,
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


def run(cmd, *, cwd, env, timeout, what):
    """Run `cmd` with stdin closed and output captured. Returns (exit code, stdout bytes).
    On timeout the whole process tree is killed and Pending("<what> timed out") is raised."""
    exe = shutil.which(cmd[0], path=env.get("PATH"))
    if exe is None:
        raise Pending(f"{cmd[0]} not found")
    # POSIX: its own session, so a timeout kills git's helpers too, and ssh has no terminal to
    # prompt on.
    proc = subprocess.Popen([exe, *cmd[1:]], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=_NO_WINDOW, start_new_session=not _WIN)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.communicate(timeout=KILL_WAIT_S)
        except Exception:
            pass
        raise Pending(f"{what} timed out")
    return proc.returncode, out


class Live:
    """git on the live copy. Every call names it as a safe directory (the other OS's files may
    have another owner, design §5.4)."""

    def __init__(self, path, env, timeout):
        self.path = os.path.abspath(path)
        self.env = env
        self.timeout = timeout
        self.safe = "safe.directory=" + os.path.realpath(self.path).replace("\\", "/")

    def git(self, *args, what="git", timeout=LOCAL_TIMEOUT_S):
        return run(["git", "-c", self.safe, *args], cwd=self.path, env=self.env, timeout=timeout,
                   what=what)

    def rev(self, ref):
        code, out = self.git("rev-parse", "--verify", "-q", ref)
        sha = out.decode("ascii", "replace").strip()
        return sha if code == 0 and sha else None

    def check(self):
        if not os.path.isdir(self.path):
            raise Pending("live copy not found")
        if not os.path.exists(os.path.join(self.path, ".git")) or self.rev("HEAD") is None:
            raise Pending("live copy is not a git repository")


# -- part 1: pull ------------------------------------------------------------------------------

def repo_slug(url):
    """`owner/repo` from a GitHub remote URL (https or ssh form), else None."""
    m = _SLUG.match(url.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def ci_verdict(payload):
    """None when the check runs of a commit (GitHub's check-runs JSON) are green under P9, else
    why not."""
    runs = payload.get("check_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list) or not all(isinstance(r, dict) for r in runs):
        return "CI status unreadable"
    if any(r.get("conclusion") in FAILED_CONCLUSIONS for r in runs):
        return "CI failed"
    for name in REQUIRED_CHECKS:
        named = [r for r in runs if r.get("name") == name]
        if not any(r.get("status") == "completed" and r.get("conclusion") == "success" for r in named):
            done = named and all(r.get("status") == "completed" for r in named)
            return "CI not green" if done else "CI pending"
    return None


def ci_gate(live, sha):
    """None when CI is green for `sha`, else why not. Anything unreadable means "not green"."""
    # The configured URL, not `git remote get-url`: that one expands url.<base>.insteadOf, which
    # can turn the GitHub URL into a mirror or local path that names no GitHub repo.
    code, out = live.git("config", "--get", "remote.origin.url")
    slug = repo_slug(out.decode("utf-8", "replace")) if code == 0 else None
    if not slug:
        return "CI status unreadable (origin is not a GitHub repo)"
    try:
        code, out = run(["gh", "api", f"repos/{slug}/commits/{sha}/check-runs?per_page=100"],
                        cwd=live.path, env=live.env, timeout=live.timeout, what="CI status")
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
    code, out = live.git("status", "--porcelain")
    if code != 0:
        raise Pending("git status failed")
    if out.strip():
        raise Pending("dirty working tree")
    code, out = live.git("symbolic-ref", "--short", "-q", "HEAD")
    branch = out.decode("utf-8", "replace").strip() if code == 0 else ""
    if branch != "master":
        raise Pending(f"on branch {branch}, not master" if branch else "detached HEAD, not master")
    head = live.rev("HEAD")
    code, _ = live.git("fetch", "--quiet", "--no-tags", "origin", "master", what="fetch",
                       timeout=live.timeout)
    if code != 0:
        raise Pending("fetch failed")
    remote = live.rev("refs/remotes/origin/master")
    if remote is None:
        raise Pending("fetch failed")
    if remote == head:
        return False
    code, _ = live.git("merge-base", "--is-ancestor", head, remote)
    if code == 1:
        raise Pending("diverged history")
    if code != 0:
        raise Pending("git merge-base failed")
    reason = ci_gate(live, remote)
    if reason:
        raise Pending(reason)
    # --no-autostash: a merge.autoStash setting must never make it stash.
    code, _ = live.git("merge", "--ff-only", "--no-autostash", "--quiet", remote)
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
    """Atomic: a temp file beside it, flushed to disk, then replaced."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    _replace(tmp, path)


def write_json(path, data):
    write_bytes(path, (json.dumps(data, indent=2) + "\n").encode("utf-8"))


def slot_dir(state, slot):
    return os.path.join(state, "runtime", f"slot-{slot}")


def venv_python(slot_path):
    venv = os.path.join(slot_path, "venv")
    return os.path.join(venv, "Scripts", "python.exe") if _WIN else os.path.join(venv, "bin", "python")


def current_slot(state):
    """The current slot's record, or None when there is no usable one."""
    cur = read_json(os.path.join(state, "runtime", "current.json"))
    if (cur and cur.get("slot") in SLOTS and isinstance(cur.get("commit"), str)
            and os.path.isdir(os.path.join(slot_dir(state, cur["slot"]), "app"))
            and os.path.exists(venv_python(slot_dir(state, cur["slot"])))):
        return cur
    return None


def deps_hash(app_dir):
    """sha256 over app/pyproject.toml and app/uv.lock: what the venv was synced from."""
    h = hashlib.sha256()
    for name in ("pyproject.toml", "uv.lock"):
        try:
            with open(os.path.join(app_dir, name), "rb") as f:
                data = f.read()
        except FileNotFoundError:
            raise Pending(f"no app/{name} in the live commit") from None
        h.update(name.encode("ascii") + b"\0" + str(len(data)).encode("ascii") + b"\0" + data)
    return h.hexdigest()


def extract_app(data, dest):
    """Unpack a `git archive <sha> app` tar into `dest`. Only plain files and folders under
    app/: an absolute, `..`, link or other member fails it before anything is written."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tf:
        members = tf.getmembers()
        for m in members:
            parts = m.name.replace("\\", "/").split("/")
            if (os.path.isabs(m.name) or m.name.startswith(("/", "\\")) or ":" in parts[0]
                    or ".." in parts or parts[0] != "app" or not (m.isfile() or m.isdir())):
                raise Pending(f"unsafe archive member {m.name[:100]!r}")
        tf.extractall(dest, members=members, filter="data")


def _rmtree(path):
    def retry_writable(func, p, exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if os.path.lexists(path):
        shutil.rmtree(path, onexc=retry_writable)


def sync_venv(slot_path, env, timeout):
    venv_env = dict(env, UV_PROJECT_ENVIRONMENT=os.path.join(slot_path, "venv"))
    venv_env.pop("VIRTUAL_ENV", None)  # the shell's or a dev venv, not this one
    code, _ = run(["uv", "sync", "--frozen", "--no-install-project"],
                  cwd=os.path.join(slot_path, "app"), env=venv_env, timeout=timeout, what="venv sync")
    if code != 0:
        raise Pending(f"venv sync failed (uv exit {code})")
    if not os.path.exists(venv_python(slot_path)):
        raise Pending("venv sync failed (no python in the venv)")


def refresh(live, state):
    """Build the inactive slot from the live HEAD and flip to it, unless the current slot
    already holds that commit. The current slot is never touched."""
    live.check()
    head = live.rev("HEAD")
    cur = current_slot(state)
    if cur and cur["commit"] == head:
        return
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
        write_json(record, {"commit": None, "deps": None})
        sync_venv(path, live.env, live.timeout)
    write_json(record, {"commit": head, "deps": deps})
    write_json(os.path.join(state, "runtime", "current.json"), {"slot": slot, "commit": head, "deps": deps})


def install_hook(state, cur):
    """Copy the current slot's hook client to the stable `<state>/hook/hook_client.py` (P4),
    when it differs. Run every time, so a copy that failed once is put right on the next start."""
    src = os.path.join(slot_dir(state, cur["slot"]), "app", "brainhub", "hook_client.py")
    dst = os.path.join(state, "hook", "hook_client.py")
    with open(src, "rb") as f:
        data = f.read()
    try:
        with open(dst, "rb") as f:
            if f.read() == data:
                return
    except OSError:
        pass
    write_bytes(dst, data)


def shell_source(live_path):
    """sha256 of the shell's source in the live copy (`app/desktop/src-tauri`: Cargo.toml,
    Cargo.lock and every file under src/), compared by the shell with the hash it was built
    from. Each file in order of its `/`-separated path relative to src-tauri adds
    `<path> NUL <byte length> NUL <bytes>`. None when unreadable."""
    base = os.path.join(live_path, "app", "desktop", "src-tauri")
    try:
        if not os.path.isdir(os.path.join(base, "src")):
            return None
        files = ["Cargo.toml", "Cargo.lock"]
        for folder, _, names in os.walk(os.path.join(base, "src")):
            files += [os.path.relpath(os.path.join(folder, n), base).replace(os.sep, "/") for n in names]
        h = hashlib.sha256()
        for rel in sorted(files):
            with open(os.path.join(base, *rel.split("/")), "rb") as f:
                data = f.read()
            h.update(rel.encode("utf-8") + b"\0" + str(len(data)).encode("ascii") + b"\0" + data)
        return h.hexdigest()
    except OSError:
        return None


# -- the step ----------------------------------------------------------------------------------

def run_step(live_path, state, *, timeout=60, pull_first=True):
    """Pull (unless `pull_first` is False, as for `runtime init`), refresh, record. Never raises."""
    state = os.path.abspath(state)
    live = Live(live_path, child_env(), timeout)
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
    result = {"pulled": pulled, "slot": cur["slot"] if cur else None,
              "commit": cur["commit"] if cur else None, "pending": "; ".join(reasons) or None}
    try:
        write_json(os.path.join(state, "update.json"),
                   {**result, "checked": datetime.now().isoformat(timespec="seconds"),
                    "shell_source": shell_source(live.path)})
    except Exception:
        pass
    return result


def main(argv):
    ap = argparse.ArgumentParser(prog="update_step.py", description="BrainHub update step")
    ap.add_argument("--live", required=True, help="the live copy (a checkout of master)")
    ap.add_argument("--state", required=True, help="this OS's BrainHub state folder")
    ap.add_argument("--timeout", type=float, default=60, help="seconds per network call")
    args = ap.parse_args(argv)
    if not args.live or not args.state or args.timeout <= 0:
        ap.error("--live and --state must be folders, --timeout positive")
    try:
        result = run_step(args.live, args.state, timeout=args.timeout)
    except BaseException:  # never stop the shell from starting the hub
        result = {"pulled": False, "slot": None, "commit": None, "pending": "update step failed"}
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
