import pytest

from brainhub.brainmap import add_node, default_map, ensure_folders, load_map, save_map
from brainhub.gitrepo import BrainRepo
from brainhub.review import Reviewer


@pytest.fixture
def rv(paths, store):
    m = add_node(default_map(), "project", "RobotArm", None)
    m = add_node(m, "person", "Ana", "robotarm")
    save_map(paths.brain, m)
    ensure_folders(paths.brain, m)
    repo = BrainRepo(paths.brain)
    repo.init()
    repo.commit_all("setup")
    r = Reviewer(paths, store, repo, on_commit=lambda sha: r.commits.append(1))
    r.commits = []
    return r


def _item_payload(**kw):
    return {"kind": "deadline", "text": "homework", "area": "uni", "project": None,
            "person": None, "due": "2026-11-14", "remind_at": None, **kw}


def test_confirm_item_sets_default_remind_and_commits(rv):
    [p] = rv.store.add_proposals("e-1", [("item", _item_payload(), [])])
    [item] = rv.confirm([p.id])
    assert item.remind_at == "2026-11-12" and item.status == "open"
    assert (rv.paths.brain / "_items" / "items.json").exists() and not rv.repo.is_dirty()
    assert rv.commits == [1]
    assert rv.store.list_proposals(status="confirmed")[0].id == p.id


def test_default_remind_keeps_datetime_shape(rv):
    [p] = rv.store.add_proposals("e-1", [("item", _item_payload(due="2026-11-14T15:30"), [])])
    [item] = rv.confirm([p.id])
    assert item.remind_at == "2026-11-12T15:30"


def test_confirm_node_updates_map(rv):
    [p] = rv.store.add_proposals("e-1", [("node", {"type": "project", "name": "Vibedine", "parent": None}, [])])
    rv.confirm([p.id])
    assert any(x["slug"] == "vibedine" for x in load_map(rv.paths.brain).projects)
    assert (rv.paths.brain / "projects" / "vibedine").is_dir()


def test_confirm_area_node_creates_folder(rv):
    [p] = rv.store.add_proposals("e-1", [("node", {"type": "area", "name": "Health", "parent": None}, [])])
    rv.confirm([p.id])
    assert (rv.paths.brain / "health").is_dir()


def test_confirm_update_moves_waiting_to_read(rv):
    w = rv.store.create_item(kind="waiting", text="API endpoints", area="projects", project="robotarm",
                             person=None, due=None, remind_at=None, source_entry="e-0")
    [p] = rv.store.add_proposals("e-1", [("update", {"item_id": w.id, "status": "to_read"}, [])])
    rv.confirm([p.id])
    assert rv.store.get_item(w.id).status == "to_read"


def test_unknown_item_update_cannot_be_confirmed(rv):
    [p] = rv.store.add_proposals("e-1", [("update", {"item_id": "i-ghost", "status": "done"}, ["unknown item id"])])
    with pytest.raises(ValueError):
        rv.confirm([p.id])


def test_update_without_item_id_cannot_be_confirmed_but_can_be_dismissed(rv):
    [p] = rv.store.add_proposals("e-1", [("update", {}, ["agent tried to edit protected files"])])
    with pytest.raises(ValueError):
        rv.confirm([p.id])
    rv.dismiss([p.id])
    assert rv.store.list_proposals(status="dismissed")[0].id == p.id


def test_confirm_is_all_or_nothing(rv):
    [good, bad] = rv.store.add_proposals("e-1", [("item", _item_payload(), []),
                                                 ("update", {"item_id": "i-ghost", "status": "done"}, [])])
    with pytest.raises(ValueError):
        rv.confirm([good.id, bad.id])
    assert rv.store.list_items() == [] and len(rv.store.list_proposals()) == 2
    with pytest.raises(ValueError):
        rv.confirm([good.id, "p-nope"])
    rv.confirm([good.id])
    with pytest.raises(ValueError):  # already confirmed
        rv.confirm([good.id])


def test_dismiss_keeps_record(rv):
    [p] = rv.store.add_proposals("e-1", [("item", {"kind": "task", "text": "x", "area": "general"}, [])])
    rv.dismiss([p.id])
    assert rv.store.list_proposals(status="dismissed")[0].id == p.id


def test_edit_proposal_validates(rv):
    [p] = rv.store.add_proposals("e-1", [("item", _item_payload(), [])])
    q = rv.edit_proposal(p.id, {"text": "new", "project": "robotarm", "due": "2026-12-01"})
    assert q.payload["text"] == "new" and q.payload["project"] == "robotarm"
    for bad in ({"area": "../x"}, {"project": "ghost"}, {"person": "ghost"}, {"kind": "chore"},
                {"due": "2026-13-45"}, {"nonsense": 1}, {"item_id": "i-1"}):
        with pytest.raises(ValueError):
            rv.edit_proposal(p.id, bad)
    assert rv.store.list_proposals()[0].payload["text"] == "new"


def test_edit_item_commits_and_validates(rv):
    it = rv.store.create_item(kind="task", text="t", area="general", source_entry="e-0")
    out = rv.edit_item(it.id, {"area": "uni", "project": "robotarm"})
    assert out.area == "uni" and not rv.repo.is_dirty() and rv.commits == [1]
    for bad in ({"area": "../etc"}, {"project": "ghost"}, {"kind": "x"}, {"status": "x"},
                {"due": "tomorrow"}, {"id": "i-2"}, {"status": "snoozed"}):
        with pytest.raises(ValueError):
            rv.edit_item(it.id, bad)
    s = rv.edit_item(it.id, {"status": "snoozed", "snooze_until": "2026-10-25T09:00"})
    assert s.status == "snoozed" and s.snooze_until == "2026-10-25T09:00"
    assert rv.edit_item(it.id, {"status": "open"}).snooze_until is None
    with pytest.raises(ValueError):
        rv.edit_item("i-ghost", {"text": "x"})


def test_item_pulls_in_same_entry_node(rv):
    [n, p] = rv.store.add_proposals("e-1", [
        ("node", {"type": "project", "name": "Vibedine", "parent": None}, []),
        ("item", _item_payload(project="vibedine"), [])])
    [item] = rv.confirm([p.id])
    assert item.project == "vibedine"
    assert any(x["slug"] == "vibedine" for x in load_map(rv.paths.brain).projects)
    assert rv.store.list_proposals() == []
    assert "(+ node vibedine)" in rv.repo._run("log", "-1", "--format=%s").stdout


def test_duplicate_node_is_skipped(rv):
    [p] = rv.store.add_proposals("e-1", [("node", {"type": "project", "name": "RobotArm", "parent": None}, [])])
    rv.confirm([p.id])
    assert rv.store.list_proposals(status="confirmed")[0].id == p.id


def test_mid_apply_failure_leaves_consistent_state(rv, monkeypatch):
    ps = rv.store.add_proposals("e-1", [("item", _item_payload(text=f"t{i}"), []) for i in range(3)])
    real = rv.store.create_item
    calls = []

    def flaky(**kw):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return real(**kw)

    monkeypatch.setattr(rv.store, "create_item", flaky)
    with pytest.raises(RuntimeError):
        rv.confirm([p.id for p in ps])
    assert [i.text for i in rv.store.list_items()] == ["t0"]
    assert [p.id for p in rv.store.list_proposals(status="confirmed")] == [ps[0].id]
    monkeypatch.undo()
    rv.confirm([p.id for p in rv.store.list_proposals()])
    assert sorted(i.text for i in rv.store.list_items()) == ["t0", "t1", "t2"]


def test_dismiss_validates(rv):
    [p] = rv.store.add_proposals("e-1", [("item", _item_payload(), [])])
    rv.confirm([p.id])
    with pytest.raises(ValueError):
        rv.dismiss([p.id])
    with pytest.raises(ValueError):
        rv.dismiss(["p-nope"])


def test_update_and_node_edit_rules(rv):
    [u, n] = rv.store.add_proposals("e-1", [("update", {"item_id": "i-1", "status": "done"}, []),
                                            ("node", {"type": "project", "name": "X", "parent": None}, [])])
    with pytest.raises(ValueError):
        rv.edit_proposal(u.id, {"status": "snoozed"})
    with pytest.raises(ValueError):
        rv.edit_proposal(n.id, {"name": "  "})
    rv.edit_proposal(u.id, {"status": "to_read"})
