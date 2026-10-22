import subprocess
import sys
from pathlib import Path

import pytest

from brainhub.gitrepo import BrainRepo


def test_init_commit_head(tmp_path):
    r = BrainRepo(tmp_path); r.init()
    (tmp_path/"a.md").write_text("x", encoding="utf-8")
    assert r.is_dirty() and r.changed_paths() == ["a.md"]
    sha = r.commit_all("first"); assert sha and r.head() == sha
    assert r.commit_all("nothing") is None


def test_revert_head(tmp_path):
    r = BrainRepo(tmp_path); r.init()
    (tmp_path/"a.md").write_text("1", encoding="utf-8"); r.commit_all("one")
    (tmp_path/"a.md").write_text("2", encoding="utf-8"); sha = r.commit_all("two")
    assert r.revert(sha) and (tmp_path/"a.md").read_text(encoding="utf-8") == "1"


def test_restore_paths_and_discard(tmp_path):
    r = BrainRepo(tmp_path); r.init()
    (tmp_path/"log").mkdir(); (tmp_path/"log"/"x.md").write_text("orig", encoding="utf-8"); r.commit_all("base")
    (tmp_path/"log"/"x.md").write_text("tampered", encoding="utf-8"); (tmp_path/"log"/"new.md").write_text("n", encoding="utf-8")
    r.restore_paths(["log"])
    assert (tmp_path/"log"/"x.md").read_text(encoding="utf-8") == "orig" and not (tmp_path/"log"/"new.md").exists()
    (tmp_path/"b.md").write_text("junk", encoding="utf-8"); r.discard_changes(); assert not r.is_dirty()


def test_push_to_bare_remote(tmp_path):
    remote = tmp_path/"remote.git"; subprocess.run(["git","init","--bare",str(remote)], check=True)
    r = BrainRepo(tmp_path/"b"); (tmp_path/"b").mkdir(); r.init()
    (tmp_path/"b"/"a.md").write_text("x", encoding="utf-8"); r.commit_all("c")
    ok, _ = r.push(str(remote)); assert ok


def test_worktree_commit_cherry_pick(tmp_path):
    r = BrainRepo(tmp_path/"b"); (tmp_path/"b").mkdir(); r.init()
    (tmp_path/"b"/"a.md").write_text("base", encoding="utf-8"); r.commit_all("base")
    wt = r.worktree_add(tmp_path/"wt")
    (tmp_path/"wt"/"n.md").write_text("agent", encoding="utf-8"); sha = wt.commit_all("agent")
    (tmp_path/"b"/"log.md").write_text("captured meanwhile", encoding="utf-8"); r.commit_all("capture")
    new = r.cherry_pick(sha)
    assert new == r.head() and (tmp_path/"b"/"n.md").read_text(encoding="utf-8") == "agent"
    assert (tmp_path/"b"/"log.md").read_text(encoding="utf-8") == "captured meanwhile"
    assert r.changed_files(new) == ["n.md"]
    r.worktree_remove(tmp_path/"wt"); assert not (tmp_path/"wt").exists()
    r.worktree_remove(tmp_path/"wt")   # idempotent


def test_cherry_pick_conflict_aborts(tmp_path):
    r = BrainRepo(tmp_path/"b"); (tmp_path/"b").mkdir(); r.init()
    (tmp_path/"b"/"a.md").write_text("base", encoding="utf-8"); r.commit_all("base")
    wt = r.worktree_add(tmp_path/"wt")
    (tmp_path/"wt"/"a.md").write_text("agent", encoding="utf-8"); sha = wt.commit_all("agent")
    (tmp_path/"b"/"a.md").write_text("owner", encoding="utf-8"); r.commit_all("owner")
    assert r.cherry_pick(sha) is None and not r.is_dirty()
    assert (tmp_path/"b"/"a.md").read_text(encoding="utf-8") == "owner"
    r.worktree_remove(tmp_path/"wt")


def test_worktree_remove_refuses_main_repo(tmp_path):
    r = BrainRepo(tmp_path/"b"); r.init()
    (tmp_path/"b"/"a.md").write_text("x", encoding="utf-8"); r.commit_all("c")
    for bad in (tmp_path/"b", tmp_path, tmp_path/"b"/"sub"):
        with pytest.raises(ValueError):
            r.worktree_remove(bad)
    assert (tmp_path/"b"/".git").exists() and (tmp_path/"b"/"a.md").exists()


def test_refuses_when_not_repo_root(tmp_path):
    parent = BrainRepo(tmp_path); parent.init()
    (tmp_path/"f.md").write_text("1", encoding="utf-8"); parent.commit_all("c")
    (tmp_path/"f.md").write_text("modified", encoding="utf-8")
    sub = tmp_path/"brain"; sub.mkdir()
    with pytest.raises(RuntimeError):
        BrainRepo(sub).discard_changes()
    assert (tmp_path/"f.md").read_text(encoding="utf-8") == "modified"


def test_git_failure_raises(tmp_path):
    r = BrainRepo(tmp_path); r.init()
    (tmp_path/"a.md").write_text("x", encoding="utf-8")
    (tmp_path/".git"/"index.lock").write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError):
        r.commit_all("fails")


def test_restore_paths_rejects_bad_paths(tmp_path):
    r = BrainRepo(tmp_path); r.init()
    for bad in ("", ".", "..", "a/../../b", ":(top)x", str(tmp_path), "/etc"):
        with pytest.raises(ValueError):
            r.restore_paths([bad])


def test_changed_files_returns_non_ascii_names_unquoted(tmp_path):
    r = BrainRepo(tmp_path); r.init()
    (tmp_path/"a.md").write_text("x", encoding="utf-8"); r.commit_all("base")
    (tmp_path/"rețete.md").write_text("sarmale", encoding="utf-8"); sha = r.commit_all("recipes")
    assert r.changed_files(sha) == ["rețete.md"]


def test_push_treats_dash_remote_as_a_destination_not_an_option(tmp_path):
    r = BrainRepo(tmp_path); r.init()
    (tmp_path/"a.md").write_text("x", encoding="utf-8"); r.commit_all("one")
    marker = tmp_path.parent / "pwned.txt"
    ok, _ = r.push(f"--receive-pack=touch {marker}")
    assert not ok and not marker.exists()


def test_filing_commits_and_in_history(tmp_path):
    r = BrainRepo(tmp_path); r.init()
    assert r.filing_commits() == {}
    (tmp_path/"rețete.md").write_text("x", encoding="utf-8")
    old = r.commit_all("file e-20261020-140322-ab12: first try")
    new = r.commit_all("file e-20261020-140322-ab12: second", allow_empty=True)
    r.commit_all('Revert "file e-20261020-150000-cd34: x"', allow_empty=True)
    got = r.filing_commits()
    assert list(got) == ["e-20261020-140322-ab12"]
    assert got["e-20261020-140322-ab12"] == (new, "second", [])
    assert r.in_history(old) and r.in_history(new)
    assert not r.in_history("0" * 40) and not r.in_history("--all") and not r.in_history("")


def _repo_with_commit(tmp_path):
    r = BrainRepo(tmp_path); r.init()
    (tmp_path/"a.md").write_text("x", encoding="utf-8"); r.commit_all("one")
    return r


def test_slow_push_does_not_block_commits(tmp_path, monkeypatch):
    import threading
    import brainhub.gitrepo as gr
    r = _repo_with_commit(tmp_path)
    real = gr.subprocess.run
    entered, release = threading.Event(), threading.Event()

    def fake_run(args, **kw):
        if "push" in args:
            entered.set()
            release.wait(10)
            return subprocess.CompletedProcess(args, 0, "", "")
        return real(args, **kw)

    monkeypatch.setattr(gr.subprocess, "run", fake_run)
    result = {}
    pusher = threading.Thread(target=lambda: result.update(push=r.push("https://example.com/r.git")))
    pusher.start()
    assert entered.wait(5)
    (tmp_path/"b.md").write_text("y", encoding="utf-8")
    assert r.commit_all("while pushing")      # would deadlock if push held the repo lock
    assert pusher.is_alive()
    release.set(); pusher.join(5)
    assert result["push"] == (True, "")


def test_push_is_non_interactive_and_times_out(tmp_path, monkeypatch):
    import brainhub.gitrepo as gr
    r = _repo_with_commit(tmp_path)
    seen = {}

    def fake_run(args, **kw):
        seen.update(args=args, **kw)
        raise subprocess.TimeoutExpired(args, kw["timeout"])

    monkeypatch.setattr(gr.subprocess, "run", fake_run)
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    ok, err = r.push("https://example.com/r.git")
    assert not ok and "timed out after 120s" in err
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0" and seen["env"]["GCM_INTERACTIVE"] == "never"
    assert seen["env"]["GIT_SSH_COMMAND"] == "ssh -oBatchMode=yes"  # no SSH passphrase prompt
    assert seen["args"][:3] == ["git", "-c", "credential.interactive=never"]
    assert seen["stdin"] is subprocess.DEVNULL and seen["timeout"] == 120


def test_push_redacts_credentials_before_truncating(tmp_path, monkeypatch):
    import brainhub.gitrepo as gr
    r = _repo_with_commit(tmp_path)
    stderr = "fatal: could not read from https://user:SECRETTOKEN@github.com/x " + "y" * 480
    monkeypatch.setattr(gr.subprocess, "run",
                        lambda args, **kw: subprocess.CompletedProcess(args, 128, "", stderr))
    ok, err = r.push("https://example.com/r.git")
    assert not ok and "SECRET" not in err and "TOKEN" not in err and len(err) <= 500


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows console flags")
def test_git_runs_without_console_window(tmp_path, monkeypatch):
    """Every git child starts with CREATE_NO_WINDOW (final review #1)."""
    import brainhub.gitrepo as g
    calls = []
    real = subprocess.run

    def spy(*args, **kw):
        calls.append(kw.get("creationflags", 0))
        return real(*args, **kw)

    monkeypatch.setattr(g.subprocess, "run", spy)
    r = BrainRepo(tmp_path); r.init()
    (tmp_path / "a.md").write_text("x", encoding="utf-8"); r.commit_all("first")
    ok, _ = r.push(str(tmp_path / "no-such-remote"), timeout=20)
    assert not ok and calls
    assert all(f & subprocess.CREATE_NO_WINDOW for f in calls), calls


def _git_options(args: list[str]) -> list[str]:
    """The `-c name=value` settings given before the git subcommand."""
    out, i = [], 1
    while i + 1 < len(args) and args[i] == "-c":
        out.append(args[i + 1])
        i += 2
    return out


def test_git_calls_pass_safe_directory(tmp_path, monkeypatch):
    """Every git call names its repo as safe, so git never refuses files the other OS created
    as "dubious ownership" (dual-boot design §5.4). A worktree is checked as its own path."""
    import brainhub.gitrepo as gr
    calls = []
    real = subprocess.run

    def spy(args, **kw):
        calls.append((list(args), str(Path(kw["cwd"]).resolve())))
        return real(args, **kw)

    monkeypatch.setattr(gr.subprocess, "run", spy)
    main, work = tmp_path / "b", tmp_path / "wt"
    r = BrainRepo(main); r.init()
    (main / "a.md").write_text("x", encoding="utf-8"); sha = r.commit_all("first")
    r.changed_files(sha)
    wt = r.worktree_add(work)
    (work / "n.md").write_text("agent", encoding="utf-8"); wt.commit_all("agent")
    r.cherry_pick(wt.head())
    r.worktree_remove(work)
    r.push(str(tmp_path / "no-such-remote"), timeout=20)
    seen = {cwd for _, cwd in calls}
    assert seen == {str(main.resolve()), str(work.resolve())}
    for args, cwd in calls:
        assert args[0] == "git"
        assert f"safe.directory={cwd.replace(chr(92), '/')}" in _git_options(args), args


def test_worktree_names_are_case_sensitive_on_posix(tmp_path):
    """A brain made on Windows has core.ignorecase=true; every git call on Linux (main and filing
    worktrees) overrides it (or git wouldn't see robot.md next to Robot.md), Windows keeps it. The
    shared config file itself is never changed."""
    r = _repo_with_commit(tmp_path / "b")
    r._run("config", "core.ignorecase", "true")
    wt = r.worktree_add(tmp_path / "wt")
    expected = "true" if __import__("sys").platform == "win32" else "false"
    assert wt._run("config", "--get", "core.ignorecase").stdout.strip() == expected
    assert r._run("config", "--get", "core.ignorecase").stdout.strip() == expected
    shared = r._run("config", "--file", ".git/config", "--get", "core.ignorecase").stdout.strip()
    assert shared == "true"  # the file Windows reads is untouched
    r.worktree_remove(tmp_path / "wt")


def test_init_sets_filemode_false(tmp_path):
    """ntfs3 reports execute bits: a repo made on Linux must not see every file as changed."""
    r = BrainRepo(tmp_path); r.init()
    assert r._run("config", "--get", "core.filemode").stdout.strip() == "false"


def test_clear_stale_locks_removes_ref_locks(tmp_path):
    r = _repo_with_commit(tmp_path)
    git = tmp_path / ".git"
    (git / "refs" / "heads" / "main.lock").write_text("", encoding="utf-8")
    (tmp_path / "b.md").write_text("y", encoding="utf-8")
    with pytest.raises(RuntimeError):
        r.commit_all("blocked by a power cut's leftover")
    names = ["index.lock", "HEAD.lock", "config.lock", "packed-refs.lock", "refs/tags/v1.lock"]
    for name in names:
        (git / name).parent.mkdir(parents=True, exist_ok=True)
        (git / name).write_text("", encoding="utf-8")
    removed = r.clear_stale_locks()
    assert sorted(removed) == sorted(f".git/{n}" for n in [*names, "refs/heads/main.lock"])
    assert (git / "refs" / "heads" / "main").is_file() and (git / "HEAD").is_file()
    assert r.commit_all("works again")
    assert r.clear_stale_locks() == []


def test_new_paths_lists_added_and_renamed(tmp_path):
    r = BrainRepo(tmp_path); r.init()
    for name in ("keep.md", "old.md", "gone.md"):
        (tmp_path / name).write_text(f"{name} body text", encoding="utf-8")
    r.commit_all("base")
    (tmp_path / "keep.md").write_text("changed", encoding="utf-8")
    (tmp_path / "old.md").rename(tmp_path / "renamed-here.md")
    (tmp_path / "gone.md").unlink()
    (tmp_path / "sub").mkdir(); (tmp_path / "sub" / "rețetă.md").write_text("new", encoding="utf-8")
    sha = r.commit_all("agent")
    assert sorted(r.new_paths(sha)) == ["renamed-here.md", "sub/rețetă.md"]
    assert sorted(r.tracked_paths()) == ["keep.md", "renamed-here.md", "sub/rețetă.md"]


@pytest.mark.skipif(sys.platform in ("win32", "darwin"), reason="needs a case-sensitive POSIX file system")
def test_main_repo_case_rule_ignores_shared_ignorecase(tmp_path):
    """A brain made on Windows carries core.ignorecase=true. A case-only rename Windows left on
    disk is committed as a rename on Linux, never as a deletion."""
    r = BrainRepo(tmp_path)
    r.init()
    subprocess.run(["git", "config", "core.ignorecase", "true"], cwd=tmp_path, check=True)
    (tmp_path / "Robot.md").write_text("wrist", encoding="utf-8")
    r.commit_all("base")
    (tmp_path / "Robot.md").rename(tmp_path / "robot.md")
    r.commit_all("owner edits")
    files = subprocess.run(["git", "ls-files"], cwd=tmp_path, capture_output=True, text=True).stdout.split()
    assert files == ["robot.md"]
