import asyncio
import subprocess
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

from brainhub.brainmap import add_node, default_map, ensure_folders, save_map
from brainhub.claude_runner import ClaudeError
from brainhub.filing import FilingWorker, build_filing_prompt, invalid_windows_paths, parse_filing_result
from brainhub.gitrepo import BrainRepo
from brainhub.models import Entry, Filing
from brainhub.rawlog import append_entry, read_log
from brainhub.store import commit_hook
from tests.fakes import FakeRunner

GOOD_RESULT = {
    "synopsis": "Checked the wrist joint on the robot arm; a new servo is needed.",
    "files": ["projects/robotarm/notes.md"],
    "items": [{"kind": "task", "text": "order a new wrist servo", "area": "projects",
               "project": "robotarm", "person": None, "due": "2026-10-24", "remind_at": None}],
    "updates": [],
    "new_nodes": [],
}


def only_proposals_pending(repo) -> bool:
    """After a filing the main checkout is clean except the hub's own proposals mirror, which
    rides along with the next hub commit."""
    return set(repo.changed_paths()) <= {"_items/proposals.json"}


@pytest.fixture
def m():
    mm = add_node(default_map(), "project", "RobotArm", None)
    return add_node(mm, "person", "Ana", "robotarm")


@pytest.fixture
def env(paths, store, m):
    save_map(paths.brain, m)
    ensure_folders(paths.brain, m)
    repo = BrainRepo(paths.brain)
    repo.init()
    entry = append_entry(paths.brain, "checked the wrist on the robot arm, need a new servo", "pc")
    store.add_entry(entry)
    repo.commit_all("setup")

    async def broadcast(type_: str, data: dict) -> None:
        pass

    def worker(runner):
        return FilingWorker(paths, store, repo, runner, broadcast,
                            on_commit=commit_hook(store, paths.brain, repo))

    return SimpleNamespace(paths=paths, store=store, repo=repo, entry=entry, worker=worker)


def test_prompt_anchors_dates_to_entry_timestamp(m):
    e = Entry("e-20261020-140322-ab12", datetime(2026,10,20,14,3,22), "pc", "buy present by next friday")
    p = build_filing_prompt(e, m, [])
    assert "Entry written: 2026-10-20 14:03 (Tuesday)" in p and "not today" in p
    assert "<<<ENTRY\nbuy present by next friday\nENTRY>>>" in p


def test_parse_flags_unknown_project_and_bad_date(m):
    raw = {"synopsis":"s","files":["general/notes.md"],"new_nodes":[],"updates":[],
           "items":[{"kind":"task","text":"t","area":"projects","project":"ghost","person":None,"due":"soonish","remind_at":None}]}
    _, _, props = parse_filing_result(raw, m, set())
    ptype, payload, warnings = props[0]
    assert payload["project"] is None and payload["due"] is None
    assert any("ghost" in w for w in warnings) and any("soonish" in w for w in warnings)


def test_parse_accepts_project_proposed_in_new_nodes(m):
    raw = {"synopsis":"s","files":[],"updates":[],"new_nodes":[{"type":"project","name":"Vibedine","parent":None}],
           "items":[{"kind":"task","text":"t","area":"projects","project":"vibedine","person":None,"due":None,"remind_at":None}]}
    _, _, props = parse_filing_result(raw, m, set())
    assert ("node" in [p[0] for p in props]) and props[0][1]["project"] == "vibedine"


async def test_file_entry_happy_path(env):           # env: paths, store, repo, worker factory, saved entry
    def edit(cwd): (cwd/"projects"/"robotarm"/"notes.md").write_text("wrist check (from e-1) [[robotarm]]", encoding="utf-8")
    worker = env.worker(FakeRunner(json_result=GOOD_RESULT, on_run=edit))
    await worker.file_entry(env.entry.id)
    f = env.store.get_filing(env.entry.id)
    assert f.status == "done" and f.commit == env.repo.head() and only_proposals_pending(env.repo)
    assert [p.ptype for p in env.store.list_proposals(entry_id=env.entry.id)] == ["item"]
    assert env.store.search('"wrist"', 5)


async def test_protected_files_restored(env):
    def tamper(cwd): (cwd/"log").mkdir(exist_ok=True); (cwd/"log"/"evil.md").write_text("x", encoding="utf-8")
    await env.worker(FakeRunner(json_result=GOOD_RESULT, on_run=tamper)).file_entry(env.entry.id)
    assert not (env.paths.brain/"log"/"evil.md").exists()
    assert any("protected" in w for p in env.store.list_proposals() for w in p.warnings)


async def test_failure_marks_failed_and_discards_partial_edits(env):
    def partial(cwd): (cwd/"general"/"half.md").write_text("half", encoding="utf-8")
    await env.worker(FakeRunner(raise_error=ClaudeError("timeout"), on_run=partial)).file_entry(env.entry.id)
    f = env.store.get_filing(env.entry.id)
    assert f.status == "failed" and "timeout" in f.error
    assert not (env.paths.brain/"general"/"half.md").exists()
    assert env.store.get_entry(env.entry.id).text == env.entry.text


async def test_owner_edits_committed_first(env):
    (env.paths.brain/"personal"/"mine.md").write_text("my own words", encoding="utf-8")
    await env.worker(FakeRunner(json_result=GOOD_RESULT)).file_entry(env.entry.id)
    assert (env.paths.brain/"personal"/"mine.md").read_text(encoding="utf-8") == "my own words"
    assert "owner edits" in subprocess.run(["git","log","--format=%s"], cwd=env.paths.brain, capture_output=True, text=True).stdout


async def test_refile_replaces_stale_proposals(env):
    w = env.worker(FakeRunner(json_result=GOOD_RESULT))
    await w.file_entry(env.entry.id); await w.file_entry(env.entry.id)
    assert len(env.store.list_proposals(entry_id=env.entry.id)) == 1


def test_requeue_unfinished(env):
    env.store.upsert_filing(Filing(env.entry.id, "running"))
    w = env.worker(FakeRunner()); ids = w.requeue_unfinished()
    assert ids == [env.entry.id] and env.store.get_filing(env.entry.id).status == "queued"
    assert w.queue.qsize() == 0  # the caller enqueues on the loop


async def test_correct_reverts_head_and_refiles(env):
    def edit(cwd): (cwd/"personal"/"gift.md").write_text("present by 14th (from e-1)", encoding="utf-8")
    runner = FakeRunner(json_result=GOOD_RESULT, on_run=edit); w = env.worker(runner)
    await w.file_entry(env.entry.id); first = env.repo.head()
    await w.correct(env.entry.id, "the present is for the 12th")
    assert env.repo.head() != first and "the 12th" in runner.calls[-1]["prompt"]


async def test_capture_during_filing_survives(env):
    def edit_and_capture(cwd):
        (cwd/"projects"/"robotarm"/"notes.md").write_text("agent note (from e-1) [[robotarm]]", encoding="utf-8")
        append_entry(env.paths.brain, "captured meanwhile", "pc")          # owner captures in the main checkout mid-run
        (cwd/"log").mkdir(exist_ok=True); (cwd/"log"/"evil.md").write_text("x", encoding="utf-8")  # agent also tampers
    await env.worker(FakeRunner(json_result=GOOD_RESULT, on_run=edit_and_capture)).file_entry(env.entry.id)
    texts = [e.text for e in read_log(env.paths.brain)]
    assert "captured meanwhile" in texts and env.entry.text in texts
    assert (env.paths.brain/"projects"/"robotarm"/"notes.md").exists()
    assert not (env.paths.brain/"log"/"evil.md").exists()
    assert env.store.get_filing(env.entry.id).status == "done"


async def test_cherry_pick_conflict_fails_cleanly(env):
    target = env.paths.brain/"personal"/"x.md"; target.write_text("base", encoding="utf-8"); env.repo.commit_all("base")
    def conflict(cwd):
        (cwd/"personal"/"x.md").write_text("agent", encoding="utf-8")
        target.write_text("owner", encoding="utf-8"); env.repo.commit_all("owner")
    await env.worker(FakeRunner(json_result=GOOD_RESULT, on_run=conflict)).file_entry(env.entry.id)
    f = env.store.get_filing(env.entry.id)
    assert f.status == "failed" and "conflict" in f.error
    assert target.read_text(encoding="utf-8") == "owner" and only_proposals_pending(env.repo)
    assert not (env.paths.data/"worktrees"/env.entry.id).exists()


class SlowRunner(FakeRunner):
    async def run_json(self, prompt, **kwargs):
        await asyncio.sleep(0.05)   # yield to the loop, so unserialized filings would overlap
        return await super().run_json(prompt, **kwargs)


async def test_concurrent_corrections_are_serialized(env):
    def edit(cwd): (cwd/"personal"/"gift.md").write_text("present (from e-1)", encoding="utf-8")
    w = env.worker(SlowRunner(json_result=GOOD_RESULT, on_run=edit))
    await w.file_entry(env.entry.id)
    results = await asyncio.gather(w.correct(env.entry.id, "it is for the 12th"),
                                   w.correct(env.entry.id, "it is for the 13th"), return_exceptions=True)
    assert all(r is None or (isinstance(r, ValueError) and "in progress" in str(r)) for r in results), results
    assert env.store.get_filing(env.entry.id).status == "done"
    assert len(env.store.list_proposals(entry_id=env.entry.id)) == 1
    assert only_proposals_pending(env.repo) and not (env.paths.data/"worktrees"/env.entry.id).exists()


async def test_correct_refuses_while_filing_queued(env):
    env.store.upsert_filing(Filing(env.entry.id, "queued"))
    with pytest.raises(ValueError, match="filing in progress"):
        await env.worker(FakeRunner(json_result=GOOD_RESULT)).correct(env.entry.id, "the 12th")


async def test_non_ascii_note_is_indexed(env):
    def edit(cwd): (cwd/"personal"/"rețete.md").write_text("sarmale for sunday (from e-1)", encoding="utf-8")
    await env.worker(FakeRunner(json_result=GOOD_RESULT, on_run=edit)).file_entry(env.entry.id)
    assert [h.ref for h in env.store.search('"sarmale"', 5)] == ["personal/rețete.md"]


async def test_non_utf8_note_is_indexed_with_replacement_and_filing_done(env):
    def edit(cwd): (cwd/"general"/"bad.md").write_bytes(b"\xff\xfe not utf-8 but has gizmos")
    await env.worker(FakeRunner(json_result=GOOD_RESULT, on_run=edit)).file_entry(env.entry.id)
    f = env.store.get_filing(env.entry.id)
    assert f.status == "done" and f.commit == env.repo.head()
    assert [h.ref for h in env.store.search('"gizmos"', 5)] == ["general/bad.md"]
    assert len(env.store.list_proposals(entry_id=env.entry.id)) == 1


async def test_done_broadcast_error_keeps_filing_done(env):
    async def broadcast(type_, data):
        if type_ == "filing.done":
            raise RuntimeError("socket gone")
    w = FilingWorker(env.paths, env.store, env.repo, FakeRunner(json_result=GOOD_RESULT), broadcast,
                     lambda sha: None)
    with pytest.raises(RuntimeError, match="socket gone"):
        await w.file_entry(env.entry.id)
    assert env.store.get_filing(env.entry.id).status == "done"


async def test_plain_leftover_worktree_dir_does_not_block(env):
    stale = env.paths.data/"worktrees"/env.entry.id; stale.mkdir(parents=True)
    (stale/"junk.md").write_text("left behind by a crash", encoding="utf-8")
    def edit(cwd): (cwd/"general"/"n.md").write_text("note (from e-1)", encoding="utf-8")
    await env.worker(FakeRunner(json_result=GOOD_RESULT, on_run=edit)).file_entry(env.entry.id)
    assert env.store.get_filing(env.entry.id).status == "done"
    assert (env.paths.brain/"general"/"n.md").exists() and not stale.exists()


async def test_rejects_malformed_entry_id(env):
    w = env.worker(FakeRunner(json_result=GOOD_RESULT))
    for bad in ("../../brain", "e-1", env.entry.id + "\n"):
        with pytest.raises(ValueError):
            await w.file_entry(bad)
        with pytest.raises(ValueError):
            await w.correct(bad, "x")
    assert not (env.paths.data/"worktrees").exists()


async def test_failure_after_cherry_pick_keeps_commit(env):
    def edit(cwd): (cwd/"general"/"n.md").write_text("note (from e-1)", encoding="utf-8")
    def on_commit(sha): raise RuntimeError("hook down")    # first called right after the cherry-pick
    async def broadcast(type_, data): pass
    w = FilingWorker(env.paths, env.store, env.repo, FakeRunner(json_result=GOOD_RESULT, on_run=edit),
                     broadcast, on_commit)
    await w.file_entry(env.entry.id)
    f = env.store.get_filing(env.entry.id)
    assert f.status == "failed" and "hook down" in f.error and f.commit == env.repo.head()


async def test_dot_files_and_git_config_are_protected(env):
    def tamper(cwd):
        (cwd/".claude").mkdir(exist_ok=True); (cwd/".claude"/"settings.json").write_text("{}", encoding="utf-8")
        (cwd/".gitattributes").write_text("* text=auto\n", encoding="utf-8")
        (cwd/".gitignore").write_text("*.md\n", encoding="utf-8")
        (cwd/"general"/"ok.md").write_text("fine (from e-1)", encoding="utf-8")
    await env.worker(FakeRunner(json_result=GOOD_RESULT, on_run=tamper)).file_entry(env.entry.id)
    b = env.paths.brain
    assert not (b/".claude").exists() and not (b/".gitignore").exists()
    assert not (b/".gitattributes").exists() or "text=auto" not in (b/".gitattributes").read_text(encoding="utf-8")
    assert (b/"general"/"ok.md").exists()
    assert any("protected" in w for p in env.store.list_proposals() for w in p.warnings)


async def test_filing_with_no_file_changes_still_leaves_a_filing_commit(env):
    await env.worker(FakeRunner(json_result=GOOD_RESULT)).file_entry(env.entry.id)
    f = env.store.get_filing(env.entry.id)
    assert f.commit == env.repo.head()
    subject = env.repo._run("log", "-1", "--format=%s").stdout.strip()
    assert subject.startswith(f"file {env.entry.id}: ")
    assert env.repo.filing_commits()[env.entry.id][0] == f.commit


async def test_pending_proposals_are_mirrored_and_swept_as_sync_items(env):
    import json as _json
    second = append_entry(env.paths.brain, "another", "pc"); env.store.add_entry(second)
    env.repo.commit_all("capture")
    w = env.worker(FakeRunner(json_result=GOOD_RESULT))
    await w.file_entry(env.entry.id)
    mirror = _json.loads((env.paths.brain/"_items"/"proposals.json").read_text(encoding="utf-8"))
    assert [d["id"] for d in mirror] == [p.id for p in env.store.list_proposals()]
    assert env.repo.changed_paths() == ["_items/proposals.json"]
    await w.file_entry(second.id)  # sweeps the mirror before filing
    subjects = env.repo._run("log", "--format=%s").stdout.splitlines()
    assert "sync _items" in subjects and "owner edits" not in subjects


def test_recover_from_history_marks_done(env):
    sha = env.repo.commit_all(f"file {env.entry.id}: wrist check", allow_empty=True)
    w = env.worker(FakeRunner())
    assert w.recover_from_history() == 1
    f = env.store.get_filing(env.entry.id)
    assert (f.status, f.commit, f.synopsis) == ("done", sha, "wrist check")
    assert w.requeue_unfinished() == []


def test_parse_slugifies_area_and_parent_and_drops_unknown_parent(m):
    raw = {"synopsis": "s", "files": [], "updates": [],
           "new_nodes": [{"type": "project", "name": "Vibe Dine", "parent": None},
                         {"type": "person", "name": "Bob", "parent": "Vibe Dine"},
                         {"type": "person", "name": "Cleo", "parent": "Ghost Co"},
                         {"type": "person", "name": "Dan", "parent": "RobotArm"}],
           "items": [{"kind": "task", "text": "t", "area": "Projects", "project": None, "person": None,
                      "due": None, "remind_at": None}]}
    _, _, props = parse_filing_result(raw, m, set())
    assert props[0][1]["area"] == "projects" and props[0][2] == []
    nodes = {p[1]["name"]: p for p in props if p[0] == "node"}
    assert nodes["Bob"][1]["parent"] == "vibe-dine" and nodes["Bob"][2] == []
    assert nodes["Cleo"][1]["parent"] is None and any("Ghost Co" in w for w in nodes["Cleo"][2])
    assert nodes["Dan"][1]["parent"] == "robotarm"


def test_parse_accepts_a_course_slug_as_project(m):
    m.courses.append({"slug": "fizica", "name": "Fizica"})
    raw = {"synopsis": "s", "files": [], "updates": [],
           "new_nodes": [{"type": "course", "name": "Chimie", "parent": None}],
           "items": [{"kind": "deadline", "text": "lab", "area": "uni", "project": "Fizica", "person": None,
                      "due": "2026-11-01", "remind_at": None},
                     {"kind": "task", "text": "read", "area": "uni", "project": "chimie", "person": None,
                      "due": None, "remind_at": None}]}
    _, _, props = parse_filing_result(raw, m, set())
    assert [(p[1]["project"], p[2]) for p in props[:2]] == [("fizica", []), ("chimie", [])]


async def test_rerun_after_the_commit_landed_fixes_in_place(env):
    from brainhub.filing import FIX_IN_PLACE
    def edit(cwd): (cwd/"general"/"n.md").write_text("note (from e-1)", encoding="utf-8")
    runner = FakeRunner(json_result=GOOD_RESULT, on_run=edit); w = env.worker(runner)
    await w.file_entry(env.entry.id)
    assert FIX_IN_PLACE not in runner.calls[0]["prompt"]
    f = env.store.get_filing(env.entry.id); f.status = "running"; env.store.upsert_filing(f)  # crash after landing
    await w.file_entry(env.entry.id)
    assert FIX_IN_PLACE in runner.calls[1]["prompt"]


async def test_correction_on_top_of_later_commits_fixes_in_place(env):
    from brainhub.filing import FIX_IN_PLACE
    def edit(cwd): (cwd/"personal"/"gift.md").write_text("present (from e-1)", encoding="utf-8")
    runner = FakeRunner(json_result=GOOD_RESULT, on_run=edit); w = env.worker(runner)
    await w.file_entry(env.entry.id)
    (env.paths.brain/"general"/"later.md").write_text("later", encoding="utf-8"); env.repo.commit_all("later")
    await w.correct(env.entry.id, "it is for the 12th")
    assert FIX_IN_PLACE in runner.calls[-1]["prompt"] and "the 12th" in runner.calls[-1]["prompt"]


async def test_failed_correction_setup_does_not_leave_the_filing_queued(env, monkeypatch):
    w = env.worker(FakeRunner(json_result=GOOD_RESULT))
    await w.file_entry(env.entry.id)
    w.mark_queued(env.entry.id)  # as the /correct route does

    def boom(*a, **k):
        raise RuntimeError("git exploded")

    monkeypatch.setattr(env.repo, "head", boom)
    with pytest.raises(RuntimeError):
        await w.correct(env.entry.id, "x", queued=True)
    f = env.store.get_filing(env.entry.id)
    assert f.status == "failed" and "git exploded" in f.error


# -- names Windows can't hold (dual-boot design §6.3) ---------------------------
# Such a file would look deleted to Windows git, and the next "owner edits" commit there would
# record the deletion. The names below can only be created on a case-sensitive POSIX file system.
posix_names = pytest.mark.skipif(sys.platform in ("win32", "darwin"),
                                 reason="needs a case-sensitive POSIX file system")


def test_invalid_windows_paths_rules():
    bad = invalid_windows_paths(
        ["general/a:b.md", "general/q?.md", "general/tab\there.md", "general/dot.", "general/space /x.md",
         "notes/con.md", "notes/Con", "notes/LPT9.tar.gz", "notes/com1", "nul/x.md", "notes/aux .md"],
        [])
    assert [b.split(" (")[0] for b in bad] == [
        "general/a:b.md", "general/q?.md", "general/tab\there.md", "general/dot.", "general/space /x.md",
        "notes/con.md", "notes/Con", "notes/LPT9.tar.gz", "notes/com1", "nul/x.md", "notes/aux .md"]
    fine = ["general/console.md", "general/com10.md", "general/nul-notes.md", "personal/rețete.md",
            "projects/robotarm/wrist-servo-2.md", "general/.hidden-ok.md"]
    assert invalid_windows_paths(fine, []) == []


def test_invalid_windows_paths_case_clashes():
    existing = ["projects/Robot.md", "projects/robotarm/notes.md", "general/a.md", "Mixed/x.md", "mixed/y.md"]
    assert invalid_windows_paths(["projects/robot.md"], existing) == [
        "projects/robot.md (same name as projects/Robot.md in another case)"]
    assert [b.split(" (")[0] for b in invalid_windows_paths(
        ["Projects/robotarm/new.md", "projects/RobotArm", "general/n.md", "general/N.md"], existing)] == [
        "Projects/robotarm/new.md", "projects/RobotArm", "general/n.md", "general/N.md"]
    # Exact names already there are fine, even inside a folder that exists in two cases.
    assert invalid_windows_paths(["projects/robotarm/new.md", "mixed/z.md", "Mixed/w.md"], existing) == []


@posix_names
async def test_filing_rejects_windows_invalid_paths(env):
    names = ["projects/a:b.md", "notes/con.md", "x/trailing. /f.md"]

    def edit(cwd):
        for rel in names:
            (cwd / rel).parent.mkdir(parents=True, exist_ok=True)
            (cwd / rel).write_text("agent text (from e-1)", encoding="utf-8")
        (cwd / "general" / "fine.md").write_text("fine (from e-1)", encoding="utf-8")
    head = env.repo.head()
    await env.worker(FakeRunner(json_result=GOOD_RESULT, on_run=edit)).file_entry(env.entry.id)
    f = env.store.get_filing(env.entry.id)
    assert f.status == "failed" and "filed paths not valid on Windows" in f.error
    assert all(rel in f.error for rel in names), f.error
    assert env.repo.head() == head and f.commit is None  # nothing cherry-picked
    b = env.paths.brain
    assert not (b / "projects" / "a:b.md").exists() and not (b / "notes").exists() and not (b / "x").exists()
    assert not (b / "general" / "fine.md").exists() and only_proposals_pending(env.repo)
    assert env.store.list_proposals(entry_id=env.entry.id) == []
    assert not (env.paths.data / "worktrees" / env.entry.id).exists()


@posix_names
@pytest.mark.parametrize("made_on_windows", [False, True], ids=["made-on-linux", "made-on-windows"])
async def test_filing_rejects_case_clash(env, made_on_windows):
    if made_on_windows:  # Windows git init sets this in the brain's shared .git/config
        env.repo._run("config", "core.ignorecase", "true")
    robot = env.paths.brain / "projects" / "Robot.md"
    robot.write_text("the owner's note", encoding="utf-8"); env.repo.commit_all("owner")
    head = env.repo.head()

    def edit(cwd): (cwd / "projects" / "robot.md").write_text("agent (from e-1)", encoding="utf-8")
    await env.worker(FakeRunner(json_result=GOOD_RESULT, on_run=edit)).file_entry(env.entry.id)
    f = env.store.get_filing(env.entry.id)
    assert f.status == "failed" and "projects/robot.md" in f.error and "projects/Robot.md" in f.error
    assert env.repo.head() == head and not (env.paths.brain / "projects" / "robot.md").exists()
    assert robot.read_text(encoding="utf-8") == "the owner's note"


async def test_valid_paths_still_file(env):
    def edit(cwd):
        (cwd / "projects" / "robotarm" / "wrist-servo-2.md").write_text("servo (from e-1)", encoding="utf-8")
        (cwd / "personal" / "rețete.md").write_text("sarmale (from e-1)", encoding="utf-8")
    await env.worker(FakeRunner(json_result=GOOD_RESULT, on_run=edit)).file_entry(env.entry.id)
    f = env.store.get_filing(env.entry.id)
    assert f.status == "done" and f.commit == env.repo.head()
    assert (env.paths.brain / "projects" / "robotarm" / "wrist-servo-2.md").exists()
    assert (env.paths.brain / "personal" / "rețete.md").exists()


def test_leftover_worktree_from_the_other_os_is_removed(env):
    """A filing cut off on the other OS leaves a worktree whose links name that OS's absolute
    paths (D:/... seen from Linux, /mnt/data/... seen from Windows): it is still removed and
    unregistered at startup (dual-boot design §6.6)."""
    wid = "e-20261020-000000-dead"
    wt_path = env.paths.data / "worktrees" / wid
    env.repo.worktree_add(wt_path)
    other = "/mnt/data/BrainHub" if sys.platform == "win32" else "D:/BrainHub"
    # Git for Windows marks a worktree's .git file hidden, and Windows won't open a hidden file
    # for overwriting: replace it instead.
    (wt_path / ".git").unlink()
    (wt_path / ".git").write_text(f"gitdir: {other}/brain/.git/worktrees/{wid}\n", encoding="utf-8")
    (env.paths.brain / ".git" / "worktrees" / wid / "gitdir").write_text(
        f"{other}/data/worktrees/{wid}/.git\n", encoding="utf-8")
    (wt_path / "general" / "half.md").parent.mkdir(parents=True, exist_ok=True)
    (wt_path / "general" / "half.md").write_text("cut off mid-run", encoding="utf-8")
    env.worker(FakeRunner()).requeue_unfinished()
    assert not wt_path.exists()
    assert not (env.paths.brain / ".git" / "worktrees" / wid).exists()
    listing = env.repo._run("worktree", "list", "--porcelain").stdout
    assert wid not in listing and not (env.paths.brain / "general" / "half.md").exists()
