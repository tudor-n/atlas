"""The update step (plan Task 9): real git (a bare "origin", a "live" clone), and fake gh and uv
on a temp PATH. PATH holds only the fakes and a git wrapper that logs each call and passes it on
to the real git, so the real gh can never run."""
import importlib.util
import io
import json
import os
import shutil
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


GREEN = runs(("test (ubuntu-latest)", "completed", "success"),
             ("test (windows-latest)", "completed", "success"),
             ("changes", "completed", "success"),
             ("desktop (ubuntu-latest)", "completed", "skipped"))

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
        env = dict(os.environ, PATH=str(self.bin), GIT_CONFIG_GLOBAL=str(self.gitconfig),
                   GIT_CONFIG_NOSYSTEM="1")
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

    def step(self, live: Path | None = None, timeout: float = 60, **extra):
        r = subprocess.run([sys.executable, "-I", "-S", str(STEP), "--live", str(live or self.live),
                            "--state", str(self.state), "--timeout", str(timeout)],
                           env=self.env(**extra), capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr
        assert r.stderr == ""
        lines = r.stdout.splitlines()
        assert len(lines) == 1, r.stdout
        return json.loads(lines[0])

    def log(self, name: str) -> list:
        p = self.logs / f"{name}.log"
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def current(self) -> dict:
        return json.loads((self.state / "runtime" / "current.json").read_text(encoding="utf-8"))

    def slot(self, name: str) -> Path:
        return self.state / "runtime" / f"slot-{name}"


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


# -- pull --------------------------------------------------------------------------------------

def test_fast_forward_when_ci_green(w):
    new = w.commit({"app/brainhub/x.py": "x = 1\n"})
    w.gh(GREEN)
    assert w.step() == {"pulled": True, "slot": "a", "commit": new, "pending": None}
    assert w.head() == new
    assert w.log("gh") == [["api", f"repos/owner/repo/commits/{new}/check-runs?per_page=100"]]
    assert (w.slot("a") / "app" / "brainhub" / "x.py").read_text(encoding="utf-8") == "x = 1\n"


def test_up_to_date_is_not_pending(w):
    assert w.step() == {"pulled": False, "slot": "a", "commit": w.base, "pending": None}
    assert w.log("gh") == []  # nothing to gate


@pytest.mark.parametrize("canned, reason", [
    (runs(("test (ubuntu-latest)", "completed", "success"), ("test (windows-latest)", "in_progress", None)),
     "CI pending"),
    (runs(("test (ubuntu-latest)", "completed", "success"),), "CI pending"),
    ({**GREEN, "check_runs": GREEN["check_runs"] + [{"name": "desktop (windows-latest)",
                                                      "status": "completed", "conclusion": "failure"}]},
     "CI failed"),
    (runs(("test (ubuntu-latest)", "completed", "success"), ("test (windows-latest)", "completed", "cancelled")),
     "CI failed"),
    (runs(("test (ubuntu-latest)", "completed", "success"), ("test (windows-latest)", "completed", "neutral")),
     "CI not green"),
    ("not json", "CI status unreadable"),
    ({"message": "Not Found"}, "CI status unreadable"),
    (None, "CI status unreadable (gh exit 1)"),  # gh not logged in
    ("missing", "CI status unreadable (gh not found)"),
], ids=["pending", "check-missing", "failed", "cancelled", "neutral", "garbage", "not-found", "gh-error",
        "gh-missing"])
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
    assert r["pending"] == "dirty working tree"
    assert r["pulled"] is False
    assert w.head() == w.base
    assert w.head("refs/remotes/origin/master") == w.base  # not even fetched
    assert target.read_text(encoding="utf-8") == "mine\n"


def test_other_branch_is_left_alone(w):
    w.git(w.live, "switch", "-q", "-c", "other")
    w.commit({"app/brainhub/x.py": "x = 1\n"})
    w.gh(GREEN)
    r = w.step()
    assert r["pending"] == "on branch other, not master"
    assert r["pulled"] is False
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
    assert r["pending"] == "diverged history"
    assert r["pulled"] is False
    assert w.head() == local
    assert w.log("gh") == []


def test_offline_fetch_failure_is_quiet(w):
    w.point_origin_at(w.tmp / "nowhere.git")
    w.commit({"app/brainhub/x.py": "x = 1\n"})
    w.gh(GREEN)
    r = w.step()  # asserts exit 0, no stderr, one line
    assert r == {"pulled": False, "slot": "a", "commit": w.base, "pending": "fetch failed"}


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
    update = json.loads((w.state / "update.json").read_text(encoding="utf-8"))
    assert update["pending"] == "live copy is not a git repository"
    assert update["shell_source"] is None
    assert w.current()["commit"] == w.base


def test_live_missing_without_slot(w):
    assert w.step(live=w.tmp / "nowhere") == {"pulled": False, "slot": None, "commit": None,
                                               "pending": "live copy not found"}


def test_never_runs_destructive_git(w):
    w.gh(GREEN)
    w.commit({"app/brainhub/x.py": "x = 1\n"})
    assert w.step()["pulled"] is True  # fetch, gate, ff merge, archive
    (w.live / "notes.txt").write_text("mine\n", encoding="utf-8")
    w.commit({"app/brainhub/x.py": "x = 2\n"})
    w.step()  # dirty
    (w.live / "notes.txt").unlink()
    w.git(w.live, "commit", "-q", "--allow-empty", "-m", "local")
    w.step()  # diverged, refresh from the local commit
    w.git(w.live, "switch", "-q", "-c", "other")
    w.step()  # other branch
    calls = w.log("git")
    assert calls
    safe = "safe.directory=" + os.path.realpath(w.live).replace("\\", "/")
    allowed = {"status", "symbolic-ref", "rev-parse", "fetch", "merge-base", "config", "merge", "archive"}
    seen = set()
    for argv in calls:
        assert argv[:2] == ["-c", safe], argv
        sub, rest = argv[2], argv[3:]
        seen.add(sub)
        assert sub in allowed, argv
        if sub == "merge":
            assert rest[:2] == ["--ff-only", "--no-autostash"], argv
        if sub == "config":
            assert rest[0] == "--get", argv
    assert {"merge", "fetch", "archive"} <= seen


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

    new = w.commit({"app/brainhub/x.py": "x = 1\n"})
    w.gh(GREEN)
    assert w.step() == {"pulled": True, "slot": "b", "commit": new, "pending": None}
    assert w.current()["slot"] == "b"
    assert (w.slot("b") / "app" / "brainhub" / "x.py").exists()
    assert not (a / "app" / "brainhub" / "x.py").exists()  # the old slot is left as it was
    assert json.loads((a / "slot.json").read_text(encoding="utf-8"))["commit"] == w.base


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
    assert json.loads((w.state / "update.json").read_text(encoding="utf-8"))["pending"] == r["pending"]
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


def test_update_json_records_the_check(w):
    w.step()
    update = json.loads((w.state / "update.json").read_text(encoding="utf-8"))
    assert set(update) == {"pulled", "slot", "commit", "pending", "checked", "shell_source"}
    assert len(update["checked"]) == 19  # local ISO, seconds
    assert len(update["shell_source"]) == 64
    first = update["shell_source"]
    w.gh(GREEN)
    w.commit({"app/brainhub/hook_client.py": "# hook v2\n"})
    w.step()
    assert json.loads((w.state / "update.json").read_text(encoding="utf-8"))["shell_source"] == first
    w.commit({"app/desktop/src-tauri/src/tray.rs": "// tray\n"})
    w.step()
    assert json.loads((w.state / "update.json").read_text(encoding="utf-8"))["shell_source"] != first


def test_usage_error_exits_2(tmp_path):
    r = subprocess.run([sys.executable, "-I", "-S", str(STEP), "--live", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 2
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


def test_ci_verdict_needs_both_oses():
    assert us.ci_verdict(GREEN) is None
    assert us.ci_verdict(runs(("test (windows-latest)", "completed", "success"))) == "CI pending"
    assert us.ci_verdict(runs()) == "CI pending"
    assert us.ci_verdict({**GREEN, "check_runs": GREEN["check_runs"] + [
        {"name": "x", "status": "completed", "conclusion": "timed_out"}]}) == "CI failed"
    assert us.ci_verdict({**GREEN, "check_runs": GREEN["check_runs"] + [
        {"name": "x", "status": "completed", "conclusion": "action_required"}]}) == "CI failed"
    assert us.ci_verdict([]) == "CI status unreadable"
    assert us.ci_verdict({"check_runs": ["x"]}) == "CI status unreadable"


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
                                 "other/evil.txt", ("app/link", "link")])
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


def test_deps_hash_follows_pyproject_and_lock(tmp_path):
    (tmp_path / "pyproject.toml").write_text("a", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("b", encoding="utf-8")
    first = us.deps_hash(str(tmp_path))
    (tmp_path / "uv.lock").write_text("c", encoding="utf-8")
    assert us.deps_hash(str(tmp_path)) != first


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


def test_runtime_status_before_init(w):
    r = _cli(w, "runtime", "status")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"current": None, "update": None}
