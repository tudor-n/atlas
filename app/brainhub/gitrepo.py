"""Git operations on the brain/ folder (its own repo). Thread-safe per repo path."""
import re
import shutil
import subprocess
import threading
from pathlib import Path

# Raw-log records carry exact lengths: no checkout (e.g. a clone with core.autocrlf=true)
# may ever convert line endings in brain/.
GITATTRIBUTES = "* -text\n"
_SHA = re.compile(r"[0-9a-f]{7,64}")
# The subject of a filing commit: "file <entry id>: <synopsis>" (filing.py writes it).
_FILING_SUBJECT = re.compile(r"file (e-[0-9]{8}-[0-9]{6}-[0-9a-f]{4}):\s?(.*)")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


class BrainRepo:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = _lock_for(self.path)

    # -- plumbing ----------------------------------------------------------
    def _run(self, *args: str) -> subprocess.CompletedProcess:
        """Run git without taking the lock (caller must hold it)."""
        return subprocess.run(
            ["git", *args], cwd=self.path, capture_output=True, text=True, encoding="utf-8"
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

    def push(self, remote_url: str) -> tuple[bool, str]:
        r = self._git("push", "--end-of-options", remote_url, "HEAD:refs/heads/main")
        return r.returncode == 0, r.stderr.strip()[-500:]

    # -- worktrees / cherry-pick ------------------------------------------
    def worktree_add(self, path: Path) -> "BrainRepo":
        path = Path(path).resolve()
        r = self._git("worktree", "add", "--detach", str(path), "HEAD")
        self._check(r, "worktree add")
        return BrainRepo(path)

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
