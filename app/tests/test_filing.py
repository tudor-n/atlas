import subprocess
from datetime import datetime
from types import SimpleNamespace

import pytest

from brainhub.brainmap import add_node, default_map, ensure_folders, save_map
from brainhub.claude_runner import ClaudeError
from brainhub.filing import FilingWorker, build_filing_prompt, parse_filing_result
from brainhub.gitrepo import BrainRepo
from brainhub.models import Entry, Filing
from brainhub.rawlog import append_entry, read_log
from tests.fakes import FakeRunner

GOOD_RESULT = {
    "synopsis": "Checked the wrist joint on the robot arm; a new servo is needed.",
    "files": ["projects/robotarm/notes.md"],
    "items": [{"kind": "task", "text": "order a new wrist servo", "area": "projects",
               "project": "robotarm", "person": None, "due": "2026-10-24", "remind_at": None}],
    "updates": [],
    "new_nodes": [],
}


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
        return FilingWorker(paths, store, repo, runner, broadcast, on_commit=lambda: None)

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
    assert f.status == "done" and f.commit == env.repo.head() and not env.repo.is_dirty()
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
    w = env.worker(FakeRunner()); w.requeue_unfinished()
    assert env.store.get_filing(env.entry.id).status == "queued" and w.queue.qsize() == 1


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
    assert target.read_text(encoding="utf-8") == "owner" and not env.repo.is_dirty()
    assert not (env.paths.data/"worktrees"/env.entry.id).exists()
