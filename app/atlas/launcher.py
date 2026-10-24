"""The launcher entry's front door (Linux): it keeps the desktop shell on the live copy's master,
then starts it.

    python3 -I -S launcher.py --exe <target dir>/release/atlas-desktop [shell args, e.g. --open]

`desktop-entry install --live <live copy>` points the KDE launcher here. The shell updates the hub's
runtime itself before every hub start (`update_step.py`), but it can't rebuild itself ("widget
update available"). This does, before the shell starts:

1. **Running already:** when an `atlas-desktop` of this user runs, hand over at once: exec the
   shell with the same arguments, which passes `--open` to the running one and exits. Nothing is
   updated or built while a shell runs (its hub runs from the current runtime slot, which an
   update step must not rebuild under it).
2. **Update:** in runtime mode (`desktop.json` names `live` and `state`), run the update step as
   the shell does (the current slot's python, `-I -S`, `--live`, `--state`, `ATLAS_STATE_DIR`),
   bounded at 90 s. It pulls master only when CI passed, and refreshes the slots. The shell runs it
   again when it starts; that second run finds nothing to do.
3. **Build:** when the live copy's HEAD isn't the commit the shell was last built from
   (`<state>/launcher.json`), or the binary is missing, run
   `cargo build --release --locked` in `<live>/app/desktop/src-tauri` with the binary's target
   folder, bounded at 30 minutes, with a desktop notification while it runs. Cargo rebuilds only
   what changed, so a commit that didn't touch the shell costs a few seconds. A failed build leaves
   the old binary, and says so.
4. **Start:** exec the shell with the arguments given after `--exe`.

Only one launcher works at a time (`<state>/launcher.lock`): a second click waits, then finds the
shell running and hands over. Everything is logged to `<state>/launcher.log`. Any failure in
steps 2 and 3 only skips them: the shell always starts, if its binary exists.

Stdlib only, run as a file: never import anything from `atlas`.
"""
import argparse
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime

SHELL_NAME = "atlas-desktop"  # the binary, and its /proc/<pid>/comm
UPDATE_LIMIT_S = 90  # the shell's own limit for the update step (supervisor.rs)
BUILD_LIMIT_S = 30 * 60
TERM_GRACE_S = 3
LOCK_WAIT_S = BUILD_LIMIT_S + 120
LOG_MAX = 1 << 20  # the log is cut back to its last half beyond this
_GIT_REPO_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR")


# -- folders and files (the rules of osdirs.py and config.rs) -----------------------------------

def _xdg(name, default):
    value = os.environ.get(name)
    if value and os.path.isabs(value):
        return value
    return os.path.join(os.path.expanduser("~"), default)


def config_path():
    """`desktop.json`: in $ATLAS_CONFIG_DIR, else ${XDG_CONFIG_HOME:-~/.config}/atlas."""
    override = os.environ.get("ATLAS_CONFIG_DIR")
    folder = override if override else os.path.join(_xdg("XDG_CONFIG_HOME", ".config"), "atlas")
    return os.path.join(folder, "desktop.json")


def default_state_dir():
    override = os.environ.get("ATLAS_STATE_DIR")
    return override if override else os.path.join(_xdg("XDG_DATA_HOME", ".local/share"), "atlas")


def read_json(path):
    try:
        with open(path, "rb") as f:
            value = json.loads(f.read().decode("utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def write_json(path, data):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def runtime_mode(cfg):
    """(live, state) when `desktop.json` is in runtime mode (it names root, live and state)."""
    if not cfg:
        return None
    root, live, state = cfg.get("root"), cfg.get("live"), cfg.get("state")
    if all(isinstance(v, str) and v for v in (root, live, state)):
        return live, state
    return None


def current_slot(state):
    """(venv python, app folder) of the current runtime slot, as supervisor.rs's `current_slot`."""
    runtime = os.path.join(state, "runtime")
    cur = read_json(os.path.join(runtime, "current.json")) or {}
    slot, commit = cur.get("slot"), cur.get("commit")
    if slot not in ("a", "b") or not isinstance(commit, str):
        return None
    folder = os.path.join(runtime, f"slot-{slot}")
    record = read_json(os.path.join(folder, "slot.json")) or {}
    app, python = os.path.join(folder, "app"), os.path.join(folder, "venv", "bin", "python")
    if record.get("commit") == commit and os.path.isdir(app) and os.path.exists(python):
        return python, app
    return None


# -- the log and the notifications ---------------------------------------------------------------

class Log:
    def __init__(self, path):
        self.path = path

    def trim(self):
        try:
            if os.path.getsize(self.path) > LOG_MAX:
                with open(self.path, "rb") as f:
                    f.seek(-(LOG_MAX // 2), os.SEEK_END)
                    tail = f.read()
                with open(self.path, "wb") as f:
                    f.write(tail[tail.find(b"\n") + 1:])
        except OSError:
            pass

    def __call__(self, msg):
        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}\n"
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            sys.stderr.write(line)

    def stream(self):
        """The log opened for a child's stdout and stderr (None: discard)."""
        try:
            return open(self.path, "ab")
        except OSError:
            return None


def notify(summary, body, log, *, urgent=False):
    """A desktop notification (`notify-send`), best effort."""
    exe = shutil.which("notify-send")
    if exe is None:
        return
    cmd = [exe, "--app-name=ATLAS", "--icon=dev.atlas.desktop", f"--urgency={'critical' if urgent else 'normal'}",
           summary, body]
    try:
        subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.SubprocessError) as e:
        log(f"notify-send failed: {e}")


# -- processes -----------------------------------------------------------------------------------

def shell_running(proc="/proc", uid=None):
    """Whether a process of this user named `atlas-desktop` runs (other than us)."""
    uid = os.getuid() if uid is None else uid
    me = os.getpid()
    try:
        pids = [int(p) for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return False
    for pid in pids:
        if pid == me:
            continue
        try:
            with open(os.path.join(proc, str(pid), "comm"), "rb") as f:
                if f.read().strip() != SHELL_NAME.encode():
                    continue
            if os.stat(os.path.join(proc, str(pid))).st_uid == uid:
                return True
        except OSError:
            continue
    return False


def child_env(extra=None):
    env = {k: v for k, v in os.environ.items() if k not in _GIT_REPO_VARS}
    env.update(GIT_TERMINAL_PROMPT="0", GH_PROMPT_DISABLED="1", GH_NO_UPDATE_NOTIFIER="1")
    env.update(extra or {})
    return env


def run_bounded(cmd, *, cwd, env, limit, log, what):
    """Run `cmd` in a session of its own, its output appended to the log; at `limit` seconds,
    SIGTERM to its group, SIGKILL after TERM_GRACE_S. Returns the exit code, or None on a timeout
    or when it couldn't start."""
    out = log.stream()
    log(f"{what}: {' '.join(cmd)} (in {cwd})")
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out or subprocess.DEVNULL,
                                stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as e:
        log(f"{what}: could not start: {e}")
        return None
    finally:
        if out is not None:
            out.close()
    try:
        code = proc.wait(timeout=limit)
        log(f"{what}: exit {code}")
        return code
    except subprocess.TimeoutExpired:
        log(f"{what}: still running after {limit} s, stopping it")
        for sig, wait in ((signal.SIGTERM, TERM_GRACE_S), (signal.SIGKILL, 5)):
            try:
                os.killpg(proc.pid, sig)
            except OSError:
                pass
            try:
                proc.wait(timeout=wait)
                break
            except subprocess.TimeoutExpired:
                continue
        return None


def git_head(live):
    try:
        out = subprocess.run(["git", "-C", live, "rev-parse", "HEAD"], env=child_env(), stdin=subprocess.DEVNULL,
                             capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.decode("ascii", "replace").strip()
    return sha if out.returncode == 0 and len(sha) == 40 else None


def find_cargo():
    exe = shutil.which("cargo")
    if exe is None:
        local = os.path.join(os.path.expanduser("~"), ".cargo", "bin", "cargo")
        if os.path.isfile(local) and os.access(local, os.X_OK):
            exe = local
    return exe


# -- the steps -----------------------------------------------------------------------------------

def update(live, state, log):
    slot = current_slot(state)
    if slot is None:
        log("update: no runtime slot yet; the shell says what to do")
        return
    python, app = slot
    # A slot from before the rename can't be current here: its desktop.json is still under the
    # old config folder until the shell's first start moves it, so runtime_mode() found nothing.
    script = os.path.join(app, "atlas", "update_step.py")
    run_bounded([python, "-I", "-S", script, "--live", live, "--state", state], cwd=state,
                env=child_env({"ATLAS_STATE_DIR": state}),
                limit=UPDATE_LIMIT_S, log=log, what="update step")


def target_dir_of(exe):
    """The cargo target folder `exe` was built into: `<target>/release/atlas-desktop`."""
    release = os.path.dirname(exe)
    if os.path.basename(exe) != SHELL_NAME or os.path.basename(release) != "release":
        return None
    return os.path.dirname(release)


def build(live, state, exe, log):
    """Rebuild the shell when the live HEAD moved since the last build (or the binary is gone).
    True when the binary is now built from HEAD."""
    stamp_path = os.path.join(state, "launcher.json")
    head = git_head(live)
    if head is None:
        log(f"build: no git HEAD in {live}; not building")
        return False
    stamp = read_json(stamp_path) or {}
    if stamp.get("built") == head and stamp.get("exe") == exe and os.path.isfile(exe):
        return True
    src = os.path.join(live, "app", "desktop", "src-tauri")
    target, cargo = target_dir_of(exe), find_cargo()
    if not os.path.isfile(os.path.join(src, "Cargo.toml")) or target is None or cargo is None:
        log(f"build: not building (source {src}, target {target}, cargo {cargo})")
        return False
    first = not os.path.isfile(exe)
    notify("Updating ATLAS", "Building the latest version from master. ATLAS opens by itself when it's done"
           + (" (the first build takes about 5 minutes)." if first else " (usually about a minute)."), log)
    started = time.monotonic()
    code = run_bounded([cargo, "build", "--release", "--locked"], cwd=src,
                       env=child_env({"CARGO_TARGET_DIR": target}), limit=BUILD_LIMIT_S, log=log, what="build")
    if code != 0:
        notify("ATLAS couldn't update itself", ("It opens the version you had." if os.path.isfile(exe) else
               "There is no version to open yet.") + f" Details: {log.path}", log, urgent=True)
        return False
    write_json(stamp_path, {"built": head, "exe": exe, "at": datetime.now().isoformat(timespec="seconds"),
                            "seconds": round(time.monotonic() - started)})
    return True


def take_lock(state, log):
    try:
        fd = os.open(os.path.join(state, "launcher.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as e:
        log(f"lock: {e}; going on without it")
        return None
    deadline = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if time.monotonic() > deadline:
                log("lock: another launcher kept it too long; going on")
                return fd
            time.sleep(0.5)


def prepare(exe, log_for):
    """Steps 1 to 3. Never raises: any problem only skips the rest."""
    try:
        if shell_running():
            return
        mode = runtime_mode(read_json(config_path()))
        if mode is None:
            return
        live, state = mode
        if not os.path.isdir(live) or not os.path.isdir(state):
            return
        log = log_for(state)
        log.trim()
        fd = take_lock(state, log)
        try:
            if shell_running():  # another launcher started it while we waited
                return
            update(live, state, log)
            build(live, state, exe, log)
        finally:
            if fd is not None:
                os.close(fd)
    except Exception as e:  # never stop the shell from starting
        try:
            Log(os.path.join(default_state_dir(), "launcher.log"))(f"launcher: {type(e).__name__}: {e}")
        except Exception:
            pass


def main(argv, exec_fn=os.execv):
    ap = argparse.ArgumentParser(prog="launcher.py")
    ap.add_argument("--exe", required=True, help="the desktop shell's binary (<target>/release/atlas-desktop)")
    args, rest = ap.parse_known_args(argv)
    if sys.platform == "win32":
        print("launcher: not on Windows yet", file=sys.stderr)
        return 2
    exe = os.path.abspath(args.exe)
    prepare(exe, lambda state: Log(os.path.join(state, "launcher.log")))
    if not (os.path.isfile(exe) and os.access(exe, os.X_OK)):
        notify("ATLAS can't start", f"{exe} isn't built yet. See app/desktop/README.md (Build).",
               Log(os.path.join(default_state_dir(), "launcher.log")), urgent=True)
        return 1
    exec_fn(exe, [exe] + rest)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
