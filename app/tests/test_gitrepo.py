import subprocess

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
