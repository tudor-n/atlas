import subprocess

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
