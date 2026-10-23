"""The update step (plan Task 9): real git (a bare "origin", a "live" clone), and fake gh and uv
on a temp PATH. PATH holds only the fakes and a git wrapper that logs each call and passes it on
to the real git, so the real gh can never run."""
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

from tests.fakes import fake_cli, kill_pid_file, pid_gone

APP_DIR = Path(__file__).resolve().parent.parent
STEP = APP_DIR / "brainhub" / "update_step.py"
REAL_GIT = shutil.which("git")
GITHUB_URL = "https://github.com/owner/repo.git"
WIN = sys.platform == "win32"
VENV_PY = ("venv", "Scripts", "python.exe") if WIN else ("venv", "bin", "python")


def _load():
    spec = importlib.util.spec_from_file_location("update_step_under_test", STEP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


us = _load()


def runs(*items):
    return {"total_count": len(items),
            "check_runs": [{"name": n, "status": s, "conclusion": c} for n, s, c in items]}


BOTH = (("test (ubuntu-latest)", "completed", "success"), ("test (windows-latest)", "completed", "success"))
GREEN = runs(*BOTH, ("changes", "completed", "success"), ("desktop (ubuntu-latest)", "completed", "skipped"),
             ("desktop (windows-latest)", "completed", "neutral"))
GH_ARGS = ["api", "--method", "GET", "repos/owner/repo/commits/{}/check-runs", "-f", "per_page=100",
           "-f", "filter=latest"]


def plus(*items):
    """GREEN with more check runs."""
    return runs(*[(r["name"], r["status"], r["conclusion"]) for r in GREEN["check_runs"]], *items)

GIT_WRAPPER = """
import json, os, subprocess, sys, time
args = sys.argv[1:]
with open({log!r}, "a", encoding="utf-8") as f:
    f.write(json.dumps(args) + "\\n")
if os.environ.get("FAKE_GIT_HANG") and "fetch" in args:
    with open({pid!r}, "w") as f:
        f.write(str(os.getpid()))
    time.sleep(60)
sys.exit(subprocess.call([{git!r}] + args))
"""

FAKE_GH = """
import json, sys
with open({log!r}, "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
try:
    with open({canned!r}, encoding="utf-8") as f:
        sys.stdout.write(f.read())
except OSError:
    sys.stderr.write("not logged in\\n")
    sys.exit(1)
"""

FAKE_UV = """
import json, os, sys
env = os.environ["UV_PROJECT_ENVIRONMENT"]
with open({log!r}, "a", encoding="utf-8") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd(), "env": env}}) + "\\n")
if os.environ.get("FAKE_UV_FAIL"):
    sys.exit(1)
marker = os.path.join(env, *{venv_py!r}[1:])
os.makedirs(os.path.dirname(marker), exist_ok=True)
open(marker, "w").close()
"""

BASE_FILES = {
    ".gitignore": "brain/\ndata/\n",
    "README.md": "outside app/\n",
    "app/pyproject.toml": "[project]\nname = 'x'\n",
    "app/uv.lock": "version = 1\n",
    "app/brainhub/__init__.py": "",
    "app/brainhub/hook_client.py": "# hook v1\n",
    "app/desktop/src-tauri/Cargo.toml": "[package]\n",
    "app/desktop/src-tauri/Cargo.lock": "# lock\n",
    "app/desktop/src-tauri/src/main.rs": "fn main() {}\n",
}


class World:
    """origin (bare) <- dev (where the tests commit) and live (the copy the step updates)."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.bin = tmp / "bin"
        self.logs = tmp / "logs"
        self.bin.mkdir()
        self.logs.mkdir()
        self.origin, self.dev, self.live = tmp / "origin.git", tmp / "dev", tmp / "live"
        self.state = Path(os.environ["BRAINHUB_STATE_DIR"])  # conftest: a temp folder
        self.canned = tmp / "gh.json"
        self.gitconfig = tmp / "gitconfig"
        self.gitconfig.write_text("", encoding="utf-8")
        fake_cli(self.bin, GIT_WRAPPER.format(log=str(self.logs / "git.log"), pid=str(self.logs / "hang.pid"),
                                              git=REAL_GIT), name="git")
        fake_cli(self.bin, FAKE_GH.format(log=str(self.logs / "gh.log"), canned=str(self.canned)), name="gh")
        fake_cli(self.bin, FAKE_UV.format(log=str(self.logs / "uv.log"), venv_py=VENV_PY), name="uv")
        self.git(tmp, "init", "-q", "--bare", "-b", "master", str(self.origin))
        self.git(tmp, "init", "-q", "-b", "master", str(self.dev))
        self.git(self.dev, "remote", "add", "origin", str(self.origin))
        self.base = self.commit(BASE_FILES, "base")
        self.git(tmp, "clone", "-q", str(self.origin), str(self.live))
        self.git(self.live, "remote", "set-url", "origin", GITHUB_URL)
        self.point_origin_at(self.origin)

    # -- plumbing --
    def env(self, **extra) -> dict:
        # A temp home: the step also looks for uv in ~/.local/bin, and the real one must never run.
        env = dict(os.environ, PATH=str(self.bin), GIT_CONFIG_GLOBAL=str(self.gitconfig),
                   GIT_CONFIG_NOSYSTEM="1", HOME=str(self.tmp / "home"), USERPROFILE=str(self.tmp / "home"))
        env.update(extra)
        return env

    def git(self, cwd, *args) -> str:
        r = subprocess.run([REAL_GIT, "-c", "user.name=t", "-c", "user.email=t@example.com",
                            "-c", "commit.gpgsign=false", *args],
                           cwd=cwd, env=self.env(), capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    def point_origin_at(self, path: Path):
        """The live copy's origin keeps its GitHub URL; git fetches it from `path`."""
        if getattr(self, "_via", None):
            self.git(self.live, "config", "--unset", f"url.{self._via}.insteadOf")
        self._via = path.as_posix()
        self.git(self.live, "config", f"url.{self._via}.insteadOf", GITHUB_URL)

    def commit(self, files: dict, msg: str = "change") -> str:
        for rel, text in files.items():
            p = self.dev / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8", newline="\n")
        self.git(self.dev, "add", "-A")
        self.git(self.dev, "commit", "-q", "-m", msg)
        self.git(self.dev, "push", "-q", "origin", "master")
        return self.git(self.dev, "rev-parse", "HEAD")

    def head(self, ref: str = "HEAD") -> str:
        return self.git(self.live, "rev-parse", ref)

    def gh(self, payload):
        self.canned.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")

    def step(self, live: Path | None = None, timeout: float = 20, budget: float | None = None, **extra):
        r = subprocess.run([sys.executable, "-I", "-S", str(STEP), "--live", str(live or self.live),
                            "--state", str(self.state), "--timeout", str(timeout),
                            *(["--budget", str(budget)] if budget is not None else [])],
                           env=self.env(**extra), capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr
        assert r.stderr == ""
        lines = r.stdout.splitlines()
        assert len(lines) == 1, r.stdout
        return json.loads(lines[0])

    def log(self, name: str) -> list:
        p = self.logs / f"{name}.log"
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def update(self) -> dict:
        return json.loads((self.state / "update.json").read_text(encoding="utf-8"))

    def current(self) -> dict:
        return json.loads((self.state / "runtime" / "current.json").read_text(encoding="utf-8"))

    def slot(self, name: str) -> Path:
        return self.state / "runtime" / f"slot-{name}"


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def gh_call(sha: str) -> list:
    return [a.format(sha) for a in GH_ARGS]


def _gone(pid: int, timeout: float = 5.0) -> bool:
    """True once `pid` no longer runs (POSIX: gone or a zombie; Windows: exited)."""
    if not WIN:
        return pid_gone(pid, timeout)
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    deadline = time.monotonic() + timeout
    while True:
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return True
        code = wintypes.DWORD()
        ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
        k32.CloseHandle(handle)
        if ok and code.value != 259:  # STILL_ACTIVE
            return True
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)


def _kill(pid: int) -> None:
    try:
        os.kill(pid, 9 if not WIN else 15)  # Windows: TerminateProcess either way
    except (OSError, ValueError):
        pass


# -- pull --------------------------------------------------------------------------------------

def test_fast_forward_when_ci_green(w):
    new = w.commit({"app/brainhub/x.py": "x = 1\n"})
    w.gh(GREEN)
    assert w.step() == {"pulled": True, "slot": "a", "commit": new, "pending": None}
    assert w.head() == new
    assert w.log("gh") == [gh_call(new)]  # GET ...check-runs?per_page=100&filter=latest
    assert (w.slot("a") / "app" / "brainhub" / "x.py").read_text(encoding="utf-8") == "x = 1\n"


def test_up_to_date_is_not_pending(w):
    assert w.step() == {"pulled": False, "slot": "a", "commit": w.base, "pending": None}
    assert w.log("gh") == []  # nothing to gate


@pytest.mark.parametrize("canned, reason", [
    (runs(BOTH[0], ("test (windows-latest)", "in_progress", None)), "CI pending"),
    (plus(("desktop (windows-latest)", "in_progress", None)), "CI pending"),
    (plus(("desktop (ubuntu-latest)", "queued", None)), "CI pending"),
    (runs(BOTH[0]), "CI pending"),
    (plus(("desktop (windows-latest)", "completed", "failure")), "CI failed"),
    (runs(BOTH[0], ("test (windows-latest)", "completed", "cancelled")), "CI failed"),
    (plus(("desktop (windows-latest)", "completed", "startup_failure")), "CI failed"),
    (plus(("changes", "completed", "stale")), "CI failed"),
    (runs(BOTH[0], ("test (windows-latest)", "completed", "neutral")), "CI not green"),
    (runs(BOTH[0], ("test (windows-latest)", "completed", "skipped")), "CI not green"),
    ({**GREEN, "total_count": 101}, "CI status unreadable (not every check run listed)"),
    ("not json", "CI status unreadable"),
    ({"message": "Not Found"}, "CI status unreadable"),
    (None, "CI status unreadable (gh exit 1)"),  # gh not logged in
    ("missing", "CI status unreadable (gh not found)"),
], ids=["pending", "desktop-running", "queued", "check-missing", "failed", "cancelled", "startup-failure",
        "stale", "neutral", "skipped", "truncated", "garbage", "not-found", "gh-error", "gh-missing"])
def test_ci_not_green_is_not_pulled(w, canned, reason):
    w.commit({"app/brainhub/x.py": "x = 1\n"})
    if canned == "missing":
        for p in w.bin.glob("gh*"):
            p.unlink()
    elif canned is not None:
        w.gh(canned)
    assert w.step() == {"pulled": False, "slot": "a", "commit": w.base, "pending": reason}
    assert w.head() == w.base


@pytest.mark.parametrize("dirt", ["untracked", "modified"])
def test_dirty_live_copy_is_left_alone(w, dirt):
    target = w.live / ("notes.txt" if dirt == "untracked" else "README.md")
    target.write_text("mine\n", encoding="utf-8")
    w.commit({"app/brainhub/x.py": "x = 1\n"})
    w.gh(GREEN)
    r = w.step()
    assert r == {"pulled": False, "slot": "a", "commit": w.base,  # a dirty tree still builds HEAD
                 "pending": "dirty working tree"}
    assert w.head() == w.base
    assert w.head("refs/remotes/origin/master") == w.base  # not even fetched
    assert target.read_text(encoding="utf-8") == "mine\n"


def test_ignored_file_is_never_overwritten(w):
    """The live copy's brain/ and data/ are ignored and hold the owner's data: a commit that
    tracks one of their files makes the fast-forward fail, never replace it."""
    w.step()
    hub = w.live / "data" / "hub.txt"
    hub.parent.mkdir()
    hub.write_bytes(b"the owner's\n")
    (w.dev / "data").mkdir()
    (w.dev / "data" / "hub.txt").write_bytes(b"from origin\n")
    w.git(w.dev, "add", "-f", "data/hub.txt")
    w.git(w.dev, "commit", "-q", "-m", "tracks data/hub.txt")
    w.git(w.dev, "push", "-q", "origin", "master")
    w.gh(GREEN)
    r = w.step()
    assert r == {"pulled": False, "slot": "a", "commit": w.base, "pending": "fast-forward failed"}
    assert hub.read_bytes() == b"the owner's\n"
    assert w.head() == w.base
    stderr = w.update()["stderr"]
    assert "data/hub.txt" in stderr and len(stderr) <= us.STDERR_TAIL


def test_other_branch_is_left_alone(w):
    w.git(w.live, "switch", "-q", "-c", "other")
    w.commit({"app/brainhub/x.py": "x = 1\n"})
    w.gh(GREEN)
    r = w.step()
    assert r == {"pulled": False, "slot": None, "commit": None,  # nor built (one note, not two)
                 "pending": "on branch other, not master"}
    assert w.git(w.live, "symbolic-ref", "--short", "HEAD") == "other"
    assert w.head("refs/heads/master") == w.base
    assert w.head("refs/remotes/origin/master") == w.base


def test_detached_head_is_left_alone(w):
    w.git(w.live, "switch", "-q", "--detach")
    w.commit({"app/brainhub/x.py": "x = 1\n"})
    w.gh(GREEN)
    assert w.step()["pending"] == "detached HEAD, not master"
    assert w.head() == w.base


@pytest.mark.parametrize("origin_moves", [True, False], ids=["diverged", "ahead"])
def test_diverged_history_is_left_alone(w, origin_moves):
    w.git(w.live, "commit", "-q", "--allow-empty", "-m", "local")
    local = w.head()
    if origin_moves:
        w.commit({"app/brainhub/x.py": "x = 1\n"})
    w.gh(GREEN)
    r = w.step()
    assert r == {"pulled": False, "slot": None, "commit": None, "pending": "diverged history"}
    assert w.head() == local
    assert w.log("gh") == []
    assert w.log("uv") == []  # a local commit is never built


def test_offline_fetch_failure_is_quiet(w):
    w.point_origin_at(w.tmp / "nowhere.git")
    w.commit({"app/brainhub/x.py": "x = 1\n"})
    w.gh(GREEN)
    r = w.step()  # asserts exit 0, no stderr, one line
    assert r == {"pulled": False, "slot": "a", "commit": w.base, "pending": "fetch failed"}
    assert "nowhere" in w.update()["stderr"]


def test_fetch_timeout_is_quiet_and_kills_git(w):
    w.commit({"app/brainhub/x.py": "x = 1\n"})
    t0 = time.monotonic()
    try:
        r = w.step(timeout=2, FAKE_GIT_HANG="1")
        assert r["pending"] == "fetch timed out"
        assert time.monotonic() - t0 < 30
        assert w.head() == w.base
        if not WIN:
            assert pid_gone(int((w.logs / "hang.pid").read_text()))
    finally:
        if not WIN:
            kill_pid_file(w.logs / "hang.pid")


def test_live_missing_is_quiet(w):
    w.step()  # a slot to keep
    assert w.step(live=w.tmp / "nowhere") == {"pulled": False, "slot": "a", "commit": w.base,
                                               "pending": "live copy not found"}
    (w.tmp / "plain").mkdir()
    assert w.step(live=w.tmp / "plain")["pending"] == "live copy is not a git repository"
    update = w.update()
    assert update["pending"] == "live copy is not a git repository"
    assert update["shell_source"] is None
    assert w.current()["commit"] == w.base


def test_live_missing_without_slot(w):
    assert w.step(live=w.tmp / "nowhere") == {"pulled": False, "slot": None, "commit": None,
                                               "pending": "live copy not found"}


_SHA = re.compile(r"[0-9a-f]{40}")
_EXACT_GIT = (
    ["rev-parse", "--verify", "-q", "HEAD"],
    ["rev-parse", "--verify", "-q", "refs/remotes/origin/master"],
    ["status", "--porcelain", "--untracked-files=normal"],
    ["symbolic-ref", "--short", "-q", "HEAD"],
    ["fetch", "--quiet", "--no-tags", "origin", "master"],
    ["config", "--get", "remote.origin.url"],
)


def git_allowed(rest: list) -> bool:
    """The only git calls the step may make (after `-c safe.directory=<live>`), argv pinned."""
    if rest in _EXACT_GIT:
        return True

    def shas(*xs):
        return all(_SHA.fullmatch(x) for x in xs)

    if len(rest) == 4 and rest[:2] == ["merge-base", "--is-ancestor"]:
        return shas(*rest[2:])
    if len(rest) == 6 and rest[:5] == ["merge", "--ff-only", "--no-autostash", "--no-overwrite-ignore", "--quiet"]:
        return shas(rest[5])
    if len(rest) == 4 and rest[:2] == ["archive", "--format=tar"] and rest[3] == "app":
        return shas(rest[2])
    return False


def test_git_allow_list_is_strict():
    sha = "a" * 40
    assert git_allowed(["merge", "--ff-only", "--no-autostash", "--no-overwrite-ignore", "--quiet", sha])
    for bad in (["merge", "--ff-only", "--no-autostash", "--no-ff", sha],
                ["merge", "--ff-only", "--no-autostash", "--quiet", sha],
                ["merge", "--ff-only", "--no-autostash", "--no-overwrite-ignore", "--quiet", "origin/master"],
                ["symbolic-ref", "HEAD", "refs/heads/x"],
                ["fetch", "--quiet", "--no-tags", "origin", "+master:master"],
                ["fetch", "--quiet", "--no-tags", "origin", "+refs/heads/master:refs/remotes/origin/master"],
                ["fetch", "--update-head-ok", "--quiet", "--no-tags", "origin", "master"],
                ["fetch", "--prune", "--quiet", "--no-tags", "origin", "master"],
                ["config", "--get", "remote.origin.url", "x"],
                ["config", "remote.origin.url", "x"],
                ["archive", "--format=tar", sha, "app", "--output=x"],
                ["reset", "--hard", sha], ["stash"], ["checkout", "master"], ["clean", "-fd"]):
        assert not git_allowed(bad), bad


def test_never_runs_destructive_git(w):
    w.gh(GREEN)
    w.commit({"app/brainhub/x.py": "x = 1\n"})
    assert w.step()["pulled"] is True  # fetch, gate, ff merge, archive
    (w.live / "notes.txt").write_text("mine\n", encoding="utf-8")
    w.commit({"app/brainhub/x.py": "x = 2\n"})
    w.step()  # dirty
    (w.live / "notes.txt").unlink()
    w.git(w.live, "commit", "-q", "--allow-empty", "-m", "local")
    w.step()  # diverged, and the local commit isn't built
    w.git(w.live, "switch", "-q", "-c", "other")
    w.step()  # other branch
    assert _cli(w, "runtime", "init", "--live", str(w.live)).returncode == 1  # refused up front
    calls = w.log("git")
    assert calls
    safe = "safe.directory=" + os.path.realpath(w.live).replace("\\", "/")
    seen = set()
    for argv in calls:
        assert argv[:8] == ["-c", safe, *us.NO_BACKGROUND, *us.CASE_RULE], argv
        assert git_allowed(argv[8:]), argv
        seen.add(argv[8])
    assert {"merge", "fetch", "archive", "merge-base", "config"} <= seen


# -- the gate's verdict ------------------------------------------------------------------------

def test_ci_verdict():
    assert us.ci_verdict(GREEN) is None
    assert us.ci_verdict(runs(*BOTH)) is None
    assert us.ci_verdict(runs(("test (windows-latest)", "completed", "success"))) == "CI pending"
    assert us.ci_verdict(runs()) == "CI pending"
    for conclusion in ("timed_out", "action_required", "startup_failure", "stale", None, "something-new"):
        assert us.ci_verdict(plus(("x", "completed", conclusion))) == "CI failed", conclusion
    for status in ("queued", "in_progress", "waiting", "requested", "pending", None):
        assert us.ci_verdict(plus(("x", status, None))) == "CI pending", status
    # A failure decides even while another run is still going.
    assert us.ci_verdict(plus(("x", "in_progress", None), ("y", "completed", "failure"))) == "CI failed"
    assert us.ci_verdict({**GREEN, "total_count": len(GREEN["check_runs"]) + 1}).startswith("CI status unreadable")
    assert us.ci_verdict({"check_runs": GREEN["check_runs"]}).startswith("CI status unreadable")
    assert us.ci_verdict({**GREEN, "total_count": True}).startswith("CI status unreadable")
    assert us.ci_verdict([]) == "CI status unreadable"
    assert us.ci_verdict({"check_runs": ["x"], "total_count": 1}) == "CI status unreadable"


# -- provenance: what the refresh may build ----------------------------------------------------

@pytest.mark.parametrize("how, reason", [
    ("other-branch", "on branch other, not master"),
    ("detached", "detached HEAD, not master"),
    ("ahead", "diverged history"),
], ids=["other-branch", "detached", "ahead"])
def test_refresh_builds_only_master_on_origin(w, how, reason):
    w.step()  # a: base
    if how == "other-branch":
        w.git(w.live, "switch", "-q", "-c", "other")
    elif how == "detached":
        w.git(w.live, "switch", "-q", "--detach")
    w.git(w.live, "commit", "-q", "--allow-empty", "-m", "local")
    r = w.step()
    assert r == {"pulled": False, "slot": "a", "commit": w.base, "pending": reason}  # one note
    assert len(w.log("uv")) == 1
    assert not w.slot("b").exists()


def test_refresh_builds_head_behind_origin_offline(w):
    new = w.commit({"app/brainhub/x.py": "x = 1\n"})
    w.git(w.live, "fetch", "-q", "origin")  # fetched earlier, not merged
    assert w.head("refs/remotes/origin/master") == new
    w.point_origin_at(w.tmp / "nowhere.git")
    assert w.step() == {"pulled": False, "slot": "a", "commit": w.base, "pending": "fetch failed"}


# -- budget, lock ------------------------------------------------------------------------------

def test_budget_bounds_the_whole_step(w):
    w.step()
    w.commit({"app/brainhub/x.py": "x = 1\n"})
    t0 = time.monotonic()
    try:
        r = w.step(timeout=60, budget=6, FAKE_GIT_HANG="1")
        assert time.monotonic() - t0 < 20  # well under the 60 s the fetch alone may take
        assert r["pending"].startswith("fetch timed out")
        assert r["slot"] == "a" and r["commit"] == w.base
        assert w.head() == w.base
        if not WIN:
            assert pid_gone(int((w.logs / "hang.pid").read_text()))
    finally:
        if not WIN:
            kill_pid_file(w.logs / "hang.pid")


def test_fast_forward_put_off_when_time_is_short(w):
    w.step()
    w.commit({"app/brainhub/x.py": "x = 1\n"})
    w.gh(GREEN)
    r = w.step(budget=us.MERGE_MIN_S - 5)
    assert r == {"pulled": False, "slot": "a", "commit": w.base,
                 "pending": "fast-forward put off (not enough time)"}
    assert w.head() == w.base


def test_venv_sync_put_off_when_time_is_short(w):
    w.step()
    new = w.commit({"app/uv.lock": "version = 2\n"})
    w.gh(GREEN)
    r = w.step(budget=us.SYNC_MIN_S - 5)
    assert r == {"pulled": True, "slot": "a", "commit": w.base, "pending": "venv sync put off (not enough time)"}
    assert len(w.log("uv")) == 1
    assert w.step() == {"pulled": False, "slot": "b", "commit": new, "pending": None}


HANG_TREE = """
import os, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
with open({pids!r}, "w") as f:
    f.write(f"{{os.getpid()}} {{child.pid}}")
time.sleep(60)
"""


def test_timeout_kills_the_whole_process_tree(tmp_path):
    folder = tmp_path / "bin"
    folder.mkdir()
    pids = tmp_path / "pids"
    fake_cli(folder, HANG_TREE.format(pids=str(pids)), name="uv")
    runner = us.Runner(dict(os.environ, PATH=str(folder)), timeout=60, budget=60)
    t0 = time.monotonic()
    try:
        with pytest.raises(us.Pending, match="^venv sync timed out$"):
            runner.run(["uv", "sync"], cwd=str(tmp_path), what="venv sync", cap=3)
        assert time.monotonic() - t0 < 3 + us.KILL_WAIT_S + 5
        parent, child = map(int, pids.read_text().split())
        assert _gone(parent) and _gone(child)
    finally:
        if pids.exists():
            for pid in map(int, pids.read_text().split()):
                _kill(pid)


@pytest.mark.skipif(WIN, reason="POSIX: the shell's time limit is SIGTERM to the step's process group")
def test_sigterm_kills_the_running_call_tree(w, tmp_path):
    """The shell's time limit sends SIGTERM to the step's process group (supervisor.rs
    `kill_tree`). uv runs in a session of its own, which that doesn't reach, so the step's handler
    kills uv's tree, then exits at once."""
    pids = tmp_path / "pids"
    fake_cli(w.bin, HANG_TREE.format(pids=str(pids)), name="uv")  # a slow venv sync
    step = subprocess.Popen([sys.executable, "-I", "-S", str(STEP), "--live", str(w.live), "--state", str(w.state)],
                            env=w.env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
    try:
        deadline = time.monotonic() + 30
        while len(pids.read_text().split() if pids.exists() else []) != 2:
            assert step.poll() is None, step.communicate()
            assert time.monotonic() < deadline, "uv never started"
            time.sleep(0.05)
        uv, grandchild = map(int, pids.read_text().split())
        assert os.getpgid(uv) != step.pid, "uv runs outside the step's process group"
        os.killpg(step.pid, signal.SIGTERM)
        out, err = step.communicate(timeout=10)
        assert (step.returncode, out, err) == (0, b"", b"")
        assert _gone(uv) and _gone(grandchild), "the step's grandchild outlived it"
        assert not (w.state / "runtime" / "current.json").exists(), "the half-built slot never counts"
    finally:
        if step.poll() is None:
            os.killpg(step.pid, signal.SIGKILL)
            step.communicate()
        if pids.exists():
            for pid in map(int, pids.read_text().split()):
                _kill(pid)


def test_out_of_budget_starts_nothing(tmp_path):
    runner = us.Runner(dict(os.environ, PATH=str(tmp_path)), timeout=20, budget=0.001)
    time.sleep(0.01)
    with pytest.raises(us.Pending, match="^out of time$"):
        runner.run(["git", "--version"], cwd=str(tmp_path), what="git", cap=60)


def test_busy_lock_touches_nothing(w):
    w.step()
    update = (w.state / "update.json").read_bytes()
    w.commit({"app/brainhub/x.py": "x = 1\n"})
    w.gh(GREEN)
    calls = len(w.log("git"))
    fd = us.take_lock(str(w.state))
    assert fd is not None
    try:
        assert w.step() == {"pulled": False, "slot": "a", "commit": w.base, "pending": "update already running"}
        assert len(w.log("git")) == calls
        r = _cli(w, "runtime", "init", "--live", str(w.live))
        assert r.returncode == 1 and "update already running" in r.stderr
    finally:
        us.release_lock(fd)
    assert (w.state / "update.json").read_bytes() == update
    assert w.head() == w.base
    assert w.step()["pulled"] is True


HOLDER = """
import importlib.util, sys, time
spec = importlib.util.spec_from_file_location("step", sys.argv[1])
step = importlib.util.module_from_spec(spec)
spec.loader.exec_module(step)
print("locked" if step.take_lock(sys.argv[2]) is not None else "busy", flush=True)
time.sleep(60)
"""


def test_lock_of_a_killed_step_is_released(w):
    w.step()
    holder = subprocess.Popen([sys.executable, "-I", "-c", HOLDER, str(STEP), str(w.state)],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        assert w.step()["pending"] == "update already running"
    finally:
        holder.kill()
        holder.wait()
        holder.stdout.close()
    deadline = time.monotonic() + 10  # Windows may drop it a moment after the process ends
    while (fd := us.take_lock(str(w.state))) is None:
        assert time.monotonic() < deadline
        time.sleep(0.1)
    us.release_lock(fd)
    assert w.step() == {"pulled": False, "slot": "a", "commit": w.base, "pending": None}


# -- refresh -----------------------------------------------------------------------------------

def test_refresh_builds_inactive_slot_and_flips(w):
    r = w.step()
    assert r == {"pulled": False, "slot": "a", "commit": w.base, "pending": None}
    a = w.slot("a")
    deps = us.deps_hash(str(a / "app"))
    assert w.current() == {"slot": "a", "commit": w.base, "deps": deps}
    assert json.loads((a / "slot.json").read_text(encoding="utf-8")) == {"commit": w.base, "deps": deps}
    assert sorted(p.name for p in (a / "app").iterdir()) == ["brainhub", "desktop", "pyproject.toml", "uv.lock"]
    assert not (a / "README.md").exists()  # only app/
    assert a.joinpath(*VENV_PY).exists()
    [uv] = w.log("uv")
    assert uv["argv"] == ["sync", "--frozen", "--no-install-project"]
    assert Path(uv["cwd"]).resolve() == (a / "app").resolve()
    assert Path(uv["env"]) == a / "venv"
    assert not list((w.state / "runtime").rglob("*.tmp"))

    new = w.commit({"app/brainhub/x.py": "x = 1\n"})
    w.gh(GREEN)
    assert w.step() == {"pulled": True, "slot": "b", "commit": new, "pending": None}
    assert w.current()["slot"] == "b"
    assert (w.slot("b") / "app" / "brainhub" / "x.py").exists()
    assert not (a / "app" / "brainhub" / "x.py").exists()  # the old slot is left as it was
    assert json.loads((a / "slot.json").read_text(encoding="utf-8"))["commit"] == w.base


def test_half_built_slot_is_never_current(w):
    w.step()
    record = w.slot("a") / "slot.json"
    record.write_text(json.dumps({"commit": None, "deps": None}), encoding="utf-8")
    assert us.current_slot(str(w.state)) is None
    # current.json names a, which may be what runs the step: b is built instead.
    assert w.step() == {"pulled": False, "slot": "b", "commit": w.base, "pending": None}
    (w.slot("b") / "slot.json").write_text(json.dumps({"commit": "0" * 40, "deps": None}), encoding="utf-8")
    assert us.current_slot(str(w.state)) is None


def test_refresh_replaces_stale_files_in_the_slot(w):
    w.step()
    w.gh(GREEN)
    w.commit({"app/brainhub/x.py": "x = 1\n"})
    w.step()  # b
    w.git(w.dev, "rm", "-q", "app/brainhub/x.py")
    w.commit({"app/brainhub/y.py": "y = 1\n"})
    w.step()  # a again
    app = w.slot("a") / "app" / "brainhub"
    assert (app / "y.py").exists() and not (app / "x.py").exists()


def test_failed_venv_sync_keeps_previous_slot(w):
    w.step()
    before = (w.state / "runtime" / "current.json").read_bytes()
    new = w.commit({"app/uv.lock": "version = 2\n"})
    w.gh(GREEN)
    r = w.step(FAKE_UV_FAIL="1")
    assert r == {"pulled": True, "slot": "a", "commit": w.base, "pending": "venv sync failed (uv exit 1)"}
    assert (w.state / "runtime" / "current.json").read_bytes() == before
    assert json.loads((w.slot("b") / "slot.json").read_text(encoding="utf-8")) == {"commit": None, "deps": None}
    assert w.update()["pending"] == r["pending"]
    # Back online: the next start finishes the job.
    assert w.step() == {"pulled": False, "slot": "b", "commit": new, "pending": None}


def test_uv_missing_keeps_previous_slot(w):
    w.step()
    w.commit({"app/brainhub/x.py": "x = 1\n"})
    w.gh(GREEN)
    for p in w.bin.glob("uv*"):
        p.unlink()
    r = w.step()
    assert r["pending"] == "uv not found" and r["slot"] == "a" and r["commit"] == w.base


def test_uv_found_in_local_bin(tmp_path, monkeypatch):
    home = tmp_path / "home"
    local = home / ".local" / "bin"
    local.mkdir(parents=True)
    exe = local / ("uv.exe" if WIN else "uv")
    exe.write_bytes(b"")
    exe.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    empty = tmp_path / "path"
    empty.mkdir()
    assert us.find_exe("uv", str(empty)) == str(exe)
    assert us.find_exe("gh", str(empty)) is None  # only uv has the fallback
    fake_cli(empty, "", name="uv")
    assert Path(us.find_exe("uv", str(empty))).parent == empty  # PATH first


def test_commit_without_lock_keeps_previous_slot(w):
    w.step()
    w.git(w.dev, "rm", "-q", "app/uv.lock")
    w.commit({"app/brainhub/x.py": "x = 1\n"})
    w.gh(GREEN)
    assert w.step() == {"pulled": True, "slot": "a", "commit": w.base,
                        "pending": "no app/uv.lock in the live commit"}
    assert len(w.log("uv")) == 1


def test_venv_reused_when_deps_unchanged(w):
    w.gh(GREEN)
    w.step()  # a: synced
    w.commit({"app/brainhub/x.py": "x = 1\n"})
    w.step()  # b: no venv yet, synced
    assert len(w.log("uv")) == 2
    c3 = w.commit({"app/brainhub/x.py": "x = 2\n"})
    assert w.step() == {"pulled": True, "slot": "a", "commit": c3, "pending": None}
    assert len(w.log("uv")) == 2  # a's venv kept
    assert (w.slot("a") / "app" / "brainhub" / "x.py").read_text(encoding="utf-8") == "x = 2\n"
    c4 = w.commit({"app/uv.lock": "version = 2\n"})
    assert w.step()["commit"] == c4
    assert len(w.log("uv")) == 3  # b's deps changed


def test_hook_client_copied_after_flip(w):
    hook = w.state / "hook" / "hook_client.py"
    w.step()
    assert hook.read_text(encoding="utf-8") == "# hook v1\n"
    w.gh(GREEN)
    w.commit({"app/brainhub/hook_client.py": "# hook v2\n"})
    w.step()
    assert hook.read_text(encoding="utf-8") == "# hook v2\n"
    # A failed flip leaves the copy alone.
    w.commit({"app/brainhub/hook_client.py": "# hook v3\n", "app/uv.lock": "version = 2\n"})
    assert w.step(FAKE_UV_FAIL="1")["pending"]
    assert hook.read_text(encoding="utf-8") == "# hook v2\n"
    # A damaged copy is put right on the next start, even with nothing to update.
    hook.write_text("junk", encoding="utf-8")
    w.step(FAKE_UV_FAIL="1")
    assert hook.read_text(encoding="utf-8") == "# hook v2\n"
    assert not list(hook.parent.glob("*.tmp"))


def test_request_client_copied_once_a_slot_has_it(w):
    """Phase 3: brainhub-request's script sits beside the hook client. A slot from before phase 3
    has none, and that is no error."""
    req = w.state / "hook" / "request_client.py"
    w.step()
    assert not req.exists() and (w.state / "hook" / "hook_client.py").exists()
    w.gh(GREEN)
    w.commit({"app/brainhub/request_client.py": "# request v1\n"})
    w.step()
    assert req.read_text(encoding="utf-8") == "# request v1\n"


def test_update_json_records_the_check(w):
    w.step()
    update = w.update()
    assert set(update) == {"pulled", "slot", "commit", "pending", "checked", "shell_source", "stderr"}
    assert len(update["checked"]) == 19  # local ISO, seconds
    assert len(update["shell_source"]) == 64
    assert update["stderr"] is None
    first = update["shell_source"]
    w.gh(GREEN)
    w.commit({"app/brainhub/hook_client.py": "# hook v2\n"})
    w.step()
    assert w.update()["shell_source"] == first
    w.commit({"app/desktop/src-tauri/src/tray.rs": "// tray\n"})
    w.step()
    assert w.update()["shell_source"] != first


def test_usage_error_exits_2(tmp_path):
    for extra in ([], ["--state", str(tmp_path), "--budget", "0"], ["--state", str(tmp_path), "--timeout", "nan"]):
        r = subprocess.run([sys.executable, "-I", "-S", str(STEP), "--live", str(tmp_path), *extra],
                           capture_output=True, text=True)
        assert r.returncode == 2, extra
        assert r.stdout == ""


# -- units -------------------------------------------------------------------------------------

@pytest.mark.parametrize("url, slug", [
    ("https://github.com/tudor-n/brain-hub.git", "tudor-n/brain-hub"),
    ("https://github.com/tudor-n/brain-hub", "tudor-n/brain-hub"),
    ("https://github.com/tudor-n/brain-hub/", "tudor-n/brain-hub"),
    ("https://user:tok@github.com/tudor-n/brain-hub.git\n", "tudor-n/brain-hub"),
    ("git@github.com:tudor-n/brain-hub.git", "tudor-n/brain-hub"),
    ("ssh://git@github.com/tudor-n/brain.hub.git", "tudor-n/brain.hub"),
    ("https://gitlab.com/tudor-n/brain-hub.git", None),
    ("/tmp/origin.git", None),
    ("file:///tmp/github.com/a/b.git", None),
    ("https://github.com/only-owner", None),
])
def test_repo_slug(url, slug):
    assert us.repo_slug(url) == slug


def test_stderr_tail():
    assert us.stderr_tail(b"x" * 1000 + b"\nfatal: the end\n").endswith("fatal: the end")
    assert len(us.stderr_tail(b"x" * 1000)) == us.STDERR_TAIL
    assert us.stderr_tail(b"fatal: unable to access 'https://me:tok3n@github.com/a/b.git/'") == \
        "fatal: unable to access 'https://***@github.com/a/b.git/'"
    assert us.stderr_tail(b"bad \xff byte") == "bad \ufffd byte"


def _tar(*members) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, kind in members:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                tf.addfile(info)
            elif kind == "link":
                info.type = tarfile.SYMTYPE
                info.linkname = "../../outside"
                tf.addfile(info)
            else:
                info.size = 2
                tf.addfile(info, io.BytesIO(b"hi"))
    return buf.getvalue()


@pytest.mark.parametrize("bad", ["../evil.txt", "app/../../evil.txt", "/abs/evil.txt", "C:/evil.txt",
                                 "other/evil.txt", ("app/link", "link"), "app/ok.txt:stream",
                                 "app/sub:x/evil.txt"])
def test_extract_rejects_unsafe_members(tmp_path, bad):
    name, kind = bad if isinstance(bad, tuple) else (bad, "file")
    dest = tmp_path / "slot"
    dest.mkdir()
    with pytest.raises(us.Pending, match="unsafe archive member"):
        us.extract_app(_tar(("app", "dir"), ("app/ok.txt", "file"), (name, kind)), str(dest))
    assert list(dest.iterdir()) == []
    assert not (tmp_path / "evil.txt").exists()


def test_extract_app(tmp_path):
    us.extract_app(_tar(("app", "dir"), ("app/brainhub", "dir"), ("app/brainhub/ok.txt", "file")), str(tmp_path))
    assert (tmp_path / "app" / "brainhub" / "ok.txt").read_bytes() == b"hi"


def test_deps_hash_follows_pyproject_lock_and_python_version(tmp_path):
    (tmp_path / "pyproject.toml").write_text("a", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("b", encoding="utf-8")
    first = us.deps_hash(str(tmp_path))
    (tmp_path / "uv.lock").write_text("c", encoding="utf-8")
    second = us.deps_hash(str(tmp_path))
    assert second != first
    (tmp_path / ".python-version").write_text("3.14\n", encoding="utf-8")
    third = us.deps_hash(str(tmp_path))
    assert third != second
    (tmp_path / ".python-version").write_text("3.15\n", encoding="utf-8")
    assert us.deps_hash(str(tmp_path)) not in (second, third)
    (tmp_path / ".python-version").unlink()
    assert us.deps_hash(str(tmp_path)) == second


SHELL_TREE = {
    "Cargo.toml": b"[package]\n",
    "Cargo.lock": b"# lock\n",
    "build.rs": b"fn main() {}\n",
    "tauri.conf.json": b"{}\n",
    "capabilities/widget.json": b"{\"identifier\": \"widget\"}\n",
    "src/main.rs": b"fn main() {}\n",
    "src/sub/tray.rs": b"// tray\n",
    "icons/icon.png": b"png",  # not hashed
    "README.md": b"not hashed\n",
}


SHELL_VECTOR = "b191da605ebed435390a547458d0f400d62249d6e58a43e51ebcfa89c114acb0"  # for build.rs too


def _shell_tree(root: Path, files: dict) -> Path:
    base = root / "app" / "desktop" / "src-tauri"
    for rel, data in files.items():
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_bytes(data)
    return base


def test_shell_source_algorithm(tmp_path):
    """The pinned algorithm; the shell's build.rs computes the same (this digest is a test vector
    for it)."""
    _shell_tree(tmp_path, SHELL_TREE)
    h = hashlib.sha256()
    for rel in ("Cargo.lock", "Cargo.toml", "build.rs", "capabilities/widget.json", "src/main.rs",
                "src/sub/tray.rs", "tauri.conf.json"):  # sorted as UTF-8 bytes
        data = SHELL_TREE[rel]
        h.update(rel.encode("utf-8") + b"\0" + str(len(data)).encode("ascii") + b"\0" + data)
    assert us.shell_source(str(tmp_path)) == h.hexdigest() == SHELL_VECTOR


SHELL_VECTOR_NOT_UTF8 = "a173d86857aa60db6a95b186b37800a8f0413f89f70053eb317a6bdcec3e4270"  # shell_source.rs too


@pytest.mark.skipif(sys.platform in ("win32", "darwin"), reason="needs a file name that isn't UTF-8 (Linux)")
def test_shell_source_name_not_utf8(tmp_path):
    """A name that isn't UTF-8 counts with its raw bytes (what os.fsencode gives); the shell's
    shell_source.rs hashes the same bytes on unix."""
    base = _shell_tree(tmp_path, SHELL_TREE)
    with open(os.path.join(os.fsencode(base), b"src", b"caf\xe9.rs"), "wb") as f:
        f.write(b"// not UTF-8\n")
    assert us.shell_source(str(tmp_path)) == SHELL_VECTOR_NOT_UTF8


@pytest.mark.parametrize("rel", ["build.rs", "tauri.conf.json", "capabilities/widget.json",
                                 "capabilities/new.json", "src/sub/tray.rs", "Cargo.lock"])
def test_shell_source_covers(tmp_path, rel):
    base = _shell_tree(tmp_path, SHELL_TREE)
    first = us.shell_source(str(tmp_path))
    (base / rel).parent.mkdir(parents=True, exist_ok=True)
    (base / rel).write_bytes(b"changed\n")
    assert us.shell_source(str(tmp_path)) != first


def test_shell_source_ignores_other_files(tmp_path):
    base = _shell_tree(tmp_path, SHELL_TREE)
    first = us.shell_source(str(tmp_path))
    (base / "icons" / "icon.png").write_bytes(b"other")
    (base / "README.md").unlink()
    assert us.shell_source(str(tmp_path)) == first
    (base / "build.rs").unlink()
    assert us.shell_source(str(tmp_path)) not in (first, None)
    shutil.rmtree(base / "src")
    assert us.shell_source(str(tmp_path)) is None


# -- runtime CLI -------------------------------------------------------------------------------

def _cli(w, *args, **extra):
    return subprocess.run([sys.executable, "-m", "brainhub", *args], cwd=APP_DIR, env=w.env(**extra),
                          capture_output=True, text=True, timeout=120)


def test_runtime_init_and_status(w):
    w.commit({"app/brainhub/x.py": "x = 1\n"})  # on origin only: init never pulls
    r = _cli(w, "runtime", "init", "--live", str(w.live))
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"pulled": False, "slot": "a", "commit": w.base, "pending": None}
    assert w.log("gh") == []
    assert not any("fetch" in argv for argv in w.log("git"))
    assert (w.state / "hook" / "hook_client.py").exists()
    r = _cli(w, "runtime", "status")
    assert r.returncode == 0, r.stderr
    status = json.loads(r.stdout)
    assert status["current"] == w.current()
    assert status["update"]["commit"] == w.base


def test_runtime_init_failure_exits_1(w):
    r = _cli(w, "runtime", "init", "--live", str(w.live), FAKE_UV_FAIL="1")
    assert r.returncode == 1
    assert "venv sync failed" in r.stderr
    assert not (w.state / "runtime" / "current.json").exists()


@pytest.mark.parametrize("how, reason", [
    ("other-branch", "on branch other, not master"),
    ("ahead", "diverged history"),
], ids=["other-branch", "ahead"])
def test_runtime_init_refuses_an_unchecked_head(w, how, reason):
    if how == "other-branch":
        w.git(w.live, "switch", "-q", "-c", "other")
    w.git(w.live, "commit", "-q", "--allow-empty", "-m", "local")
    r = _cli(w, "runtime", "init", "--live", str(w.live))
    assert r.returncode == 1
    assert r.stdout == ""
    assert f"error: {reason}: runtime init builds only a commit on branch master" in r.stderr
    assert not (w.state / "runtime").exists()
    assert w.log("uv") == []


def test_runtime_status_before_init(w):
    r = _cli(w, "runtime", "status")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"current": None, "update": None}
