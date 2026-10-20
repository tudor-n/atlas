"""Git operations on the brain/ folder (its own repo). Thread-safe per repo path."""
import shutil
import subprocess
import threading
from pathlib import Path

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
            return self._run(*args)

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
                self._run("init", "-b", "main")
            self._run("config", "user.name", "BrainHub")
            self._run("config", "user.email", "brainhub@localhost")
            self._run("config", "core.autocrlf", "false")

    def is_dirty(self) -> bool:
        with self._lock:
            return bool(self._changed())

    def changed_paths(self) -> list[str]:
        with self._lock:
            return self._changed()

    def commit_all(self, message: str) -> str | None:
        with self._lock:
            self._run("add", "-A")
            if not self._changed():
                return None
            r = self._run("commit", "-m", message)
            if r.returncode != 0:
                return None
            return self._head()

    def head(self) -> str | None:
        with self._lock:
            return self._head()

    def revert(self, sha: str) -> bool:
        with self._lock:
            r = self._run("revert", "--no-edit", sha)
            if r.returncode != 0:
                self._run("revert", "--abort")
                return False
            return True

    def restore_paths(self, paths: list[str]) -> None:
        if not paths:
            return
        with self._lock:
            if self._head() is not None:
                tracked = self._run("ls-tree", "-r", "--name-only", "HEAD", "--", *paths).stdout.split()
                if tracked:
                    self._run("checkout", "HEAD", "--", *paths)
            self._run("clean", "-fd", "--", *paths)

    def discard_changes(self) -> None:
        with self._lock:
            if self._head() is not None:
                self._run("reset", "--hard", "HEAD")
            self._run("clean", "-fd")

    def push(self, remote_url: str) -> tuple[bool, str]:
        r = self._git("push", remote_url, "HEAD:refs/heads/main")
        return r.returncode == 0, r.stderr.strip()[-500:]

    # -- worktrees / cherry-pick ------------------------------------------
    def worktree_add(self, path: Path) -> "BrainRepo":
        r = self._git("worktree", "add", "--detach", str(path), "HEAD")
        if r.returncode != 0:
            raise RuntimeError(f"git worktree add failed: {r.stderr.strip()}")
        return BrainRepo(Path(path))

    def worktree_remove(self, path: Path) -> None:
        with self._lock:
            self._run("worktree", "remove", "--force", str(path))
            if Path(path).exists():  # e.g. not a registered worktree, or Windows left residue
                shutil.rmtree(path, ignore_errors=True)
            self._run("worktree", "prune")

    def cherry_pick(self, sha: str) -> str | None:
        with self._lock:
            r = self._run("cherry-pick", sha)
            if r.returncode != 0:
                self._run("cherry-pick", "--abort")
                return None
            return self._head()

    def changed_files(self, sha: str) -> list[str]:
        r = self._git("diff-tree", "--no-commit-id", "--name-only", "-r", sha)
        return [p.replace("\\", "/") for p in r.stdout.splitlines() if p]
