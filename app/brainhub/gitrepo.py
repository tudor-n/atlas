"""Git operations on the brain/ folder (its own repo). Thread-safe per repo path."""
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

# Raw-log records carry exact lengths: no checkout (e.g. a clone with core.autocrlf=true)
# may ever convert line endings in brain/.
GITATTRIBUTES = "* -text\n"
_SHA = re.compile(r"[0-9a-f]{7,64}")
PUSH_TIMEOUT_S = 120
# No console window for git (or the credential helper it starts): under the desktop shell the hub
# has a hidden console that git inherits; without this flag a console-less parent would make git
# pop up a visible one (Ruling 13).
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
# Lock files a crash or power cut can leave in .git (plus any refs/**/*.lock); each one blocks
# the git commands that need it until it is removed (dual-boot design §6.3).
STALE_LOCKS = ("index.lock", "HEAD.lock", "config.lock", "packed-refs.lock")


def redact(text: str) -> str:
    """Hide the user:token part of any URL (https://user:tok@host -> https://***@host)."""
    return re.sub(r"(?<=://)[^/@\s]+@", "***@", text)
# The subject of a filing commit: "file <entry id>: <synopsis>" (filing.py writes it).
_FILING_SUBJECT = re.compile(r"file (e-[0-9]{8}-[0-9]{6}-[0-9a-f]{4}):\s?(.*)")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


class BrainRepo:
    def __init__(self, path: Path, *, case_sensitive: bool = False):
        self.path = Path(path)
        self._lock = _lock_for(self.path)
        # Every git call names this repo as safe: files the other OS created may have another
        # owner, and git would refuse them as "dubious ownership" (dual-boot design §5.4). git
        # wants / separators here on Windows too. A worktree is checked as its own path.
        self._safe = "safe.directory=" + str(self.path.resolve()).replace("\\", "/")
        self._config = ["-c", self._safe]
        if case_sensitive:  # see worktree_add
            self._config += ["-c", "core.ignorecase=false"]

    # -- plumbing ----------------------------------------------------------
    def _run(self, *args: str) -> subprocess.CompletedProcess:
        """Run git without taking the lock (caller must hold it)."""
        return subprocess.run(
            ["git", *self._config, *args], cwd=self.path, capture_output=True, text=True,
            encoding="utf-8", **_NO_WINDOW
        )

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        with self._lock:
            self._ensure_repo()
            return self._run(*args)

    def _ensure_repo(self) -> None:
        """Refuse to act unless self.path is itself a git repo root (dir or worktree file)."""
        if not (self.path / ".git").exists():
            raise RuntimeError(f"{self.path} is not a git repository root (no .git)")

    def _check(self, r: subprocess.CompletedProcess, what: str) -> subprocess.CompletedProcess:
        if r.returncode != 0:
            raise RuntimeError(f"git {what} failed: {r.stderr.strip()[-500:]}")
        return r

    def _head(self) -> str | None:
        r = self._run("rev-parse", "--verify", "-q", "HEAD")
        return r.stdout.strip() if r.returncode == 0 else None

    def _changed(self) -> list[str]:
        r = self._run("status", "--porcelain", "-z", "-uall")
        out: list[str] = []
        parts = r.stdout.split("\0")
        i = 0
        while i < len(parts):
            entry = parts[i]
            i += 1
            if len(entry) < 4:
                continue
            out.append(entry[3:].replace("\\", "/"))
            if entry[0] in "RC":  # rename/copy: next field is the original path
                i += 1
        return out

    # -- basic operations --------------------------------------------------
    def init(self) -> None:
        with self._lock:
            self.path.mkdir(parents=True, exist_ok=True)
            if not (self.path / ".git").exists():
                self._check(self._run("init", "-b", "main"), "init")
            self._ensure_repo()
            self._check(self._run("config", "user.name", "BrainHub"), "config")
            self._check(self._run("config", "user.email", "brainhub@localhost"), "config")
            self._check(self._run("config", "core.autocrlf", "false"), "config")
            # ntfs3 reports execute bits, so a repo made on Linux would otherwise see mode changes.
            self._check(self._run("config", "core.filemode", "false"), "config")

    def clear_stale_locks(self) -> list[str]:
        """Delete the git lock files a crash or power cut left behind (STALE_LOCKS and any
        refs/**/*.lock) and return them, repo-relative with / separators. A leftover
        refs/heads/main.lock would otherwise block every commit. Only safe while no git process
        runs on this repo: the hub calls it once at startup, before any of its git calls."""
        git = self.path / ".git"
        if not git.is_dir():
            return []
        found = [git / name for name in STALE_LOCKS]
        if (git / "refs").is_dir():
            found += sorted((git / "refs").rglob("*.lock"))
        removed = []
        for f in found:
            if f.is_file():
                f.unlink()
                removed.append(f.relative_to(self.path).as_posix())
        return removed

    def ensure_gitattributes(self) -> bool:
        """Write brain/.gitattributes (`* -text`) if it is missing. True if it was created;
        the caller commits it."""
        f = self.path / ".gitattributes"
        if f.exists():
            return False
        with open(f, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(GITATTRIBUTES)
        return True

    def is_dirty(self) -> bool:
        with self._lock:
            self._ensure_repo()
            return bool(self._changed())

    def changed_paths(self) -> list[str]:
        with self._lock:
            self._ensure_repo()
            return self._changed()

    def commit_all(self, message: str, allow_empty: bool = False) -> str | None:
        """Commit everything; None when there is nothing to commit (unless allow_empty)."""
        with self._lock:
            self._ensure_repo()
            self._check(self._run("add", "-A"), "add")
            if not self._changed() and not allow_empty:
                return None
            self._check(self._run("commit", "--allow-empty", "-m", message), "commit")
            return self._head()

    def head(self) -> str | None:
        with self._lock:
            self._ensure_repo()
            return self._head()

    def in_history(self, sha: str) -> bool:
        """True if `sha` is a commit reachable from HEAD."""
        if not sha or not _SHA.fullmatch(sha):
            return False
        with self._lock:
            self._ensure_repo()
            return self._run("merge-base", "--is-ancestor", sha, "HEAD").returncode == 0

    def filing_commits(self) -> dict[str, tuple[str, str, list[str]]]:
        """{entry id: (sha, synopsis, files)} from every `file <entry id>: <synopsis>` commit
        in history, newest per entry. Lets a rebuilt index know what was already filed."""
        with self._lock:
            self._ensure_repo()
            if self._head() is None:
                return {}
            r = self._check(self._run("-c", "core.quotepath=false", "log", "--name-only",
                                      "--format=%x1e%H%x1f%s"), "log")
        out: dict[str, tuple[str, str, list[str]]] = {}
        for record in r.stdout.split("\x1e"):
            head, _, rest = record.partition("\n")
            sha, _, subject = head.partition("\x1f")
            m = _FILING_SUBJECT.match(subject)
            if m and m.group(1) not in out:  # git log is newest first
                files = [ln.strip() for ln in rest.splitlines() if ln.strip()]
                out[m.group(1)] = (sha.strip(), m.group(2).strip(), files)
        return out

    def revert(self, sha: str) -> str | None:
        """Revert `sha`; the new commit's sha, or None (aborted) on a conflict."""
        with self._lock:
            self._ensure_repo()
            r = self._run("revert", "--no-edit", sha)
            if r.returncode != 0:
                self._run("revert", "--abort")
                return None
            return self._head()

    @staticmethod
    def _validate_rel(p: str) -> None:
        norm = p.replace("\\", "/")
        if (
            not p or p == "." or p.startswith(":") or Path(p).is_absolute()
            or norm.startswith("/") or ".." in norm.split("/")
        ):
            raise ValueError(f"invalid repo-relative path: {p!r}")

    def restore_paths(self, paths: list[str]) -> None:
        for p in paths:
            self._validate_rel(p)
        if not paths:
            return
        with self._lock:
            self._ensure_repo()
            if self._head() is not None:
                ls = self._check(
                    self._run("ls-tree", "-r", "-z", "--name-only", "HEAD", "--", *paths), "ls-tree")
                if ls.stdout.strip("\0"):
                    self._check(self._run("checkout", "HEAD", "--", *paths), "checkout")
            self._check(self._run("clean", "-fd", "--", *paths), "clean")

    def discard_changes(self) -> None:
        with self._lock:
            self._ensure_repo()
            if self._head() is not None:
                self._check(self._run("reset", "--hard", "HEAD"), "reset")
            self._check(self._run("clean", "-fd"), "clean")

    def push(self, remote_url: str, timeout: float = PUSH_TIMEOUT_S) -> tuple[bool, str]:
        """Push HEAD to the remote's main. Runs WITHOUT the repo lock (a push never touches
        the index), so a slow network can't stall commits. Never prompts: no terminal, no
        credential-manager window, stdin closed; a hang ends at `timeout` as a failure.
        The error text has credentials redacted before it is shortened."""
        self._ensure_repo()
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
        try:
            r = subprocess.run(
                ["git", "-c", "credential.interactive=never", "-c", self._safe, "push",
                 "--end-of-options", remote_url, "HEAD:refs/heads/main"],
                cwd=self.path, capture_output=True, text=True, encoding="utf-8", errors="replace",
                env=env, stdin=subprocess.DEVNULL, timeout=timeout, **_NO_WINDOW)
        except subprocess.TimeoutExpired:
            return False, f"push timed out after {timeout:g}s"
        return r.returncode == 0, redact(r.stderr.strip())[-500:]

    # -- worktrees / cherry-pick ------------------------------------------
    def worktree_add(self, path: Path) -> "BrainRepo":
        """A detached worktree at HEAD. On POSIX its git calls treat names as case-sensitive,
        whatever the shared config says: a brain made on Windows has core.ignorecase=true, and
        git on Linux would then not even see an agent's robot.md next to Robot.md. The filing
        would drop it quietly instead of failing visibly on the case clash (design §6.3)."""
        path = Path(path).resolve()
        r = self._git("worktree", "add", "--detach", str(path), "HEAD")
        self._check(r, "worktree add")
        return BrainRepo(path, case_sensitive=sys.platform != "win32")

    def worktree_remove(self, path: Path) -> None:
        path = Path(path).resolve()
        me = self.path.resolve()
        if path == me or path in me.parents or me in path.parents:
            raise ValueError(f"refusing to remove {path}: it is, contains or is inside the repo {me}")
        with self._lock:
            self._ensure_repo()
            listing = self._run("worktree", "list", "--porcelain", "-z").stdout.split("\0")
            registered = any(
                line.startswith("worktree ") and Path(line[9:]).resolve() == path for line in listing
            )
            if registered:
                self._run("worktree", "remove", "--force", str(path))
                if path.exists():  # Windows may leave residue after git removed the registration
                    shutil.rmtree(path, ignore_errors=True)
            self._run("worktree", "prune")

    def cherry_pick(self, sha: str) -> str | None:
        with self._lock:
            self._ensure_repo()
            # A filing that changed no files is still a commit (its record in history).
            r = self._run("cherry-pick", "--allow-empty", "--keep-redundant-commits", sha)
            if r.returncode != 0:
                self._run("cherry-pick", "--abort")
                return None
            return self._head()

    def changed_files(self, sha: str) -> list[str]:
        # -z: names come back verbatim, not C-quoted (non-ASCII would be "re\310\233ete.md")
        r = self._check(
            self._git("diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-z", sha),
            "diff-tree")  # --root: the first commit (setup) lists its files too
        return [p.replace("\\", "/") for p in r.stdout.split("\0") if p]

    def new_paths(self, sha: str) -> list[str]:
        """The paths `sha` adds, copies or renames to (a rename lists its new name only)."""
        r = self._check(
            self._git("diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-z", "-M",
                      "--diff-filter=ACR", sha), "diff-tree")
        return [p for p in r.stdout.split("\0") if p]

    def tracked_paths(self) -> list[str]:
        """Every path in the index (`git ls-files`), verbatim."""
        r = self._check(self._git("ls-files", "-z"), "ls-files")
        return [p for p in r.stdout.split("\0") if p]
