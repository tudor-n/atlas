import json
from datetime import datetime

import pytest

from brainhub.models import Entry, Filing
from brainhub.rawlog import append_entry
from brainhub.store import Store


def test_entries_and_unfinished(store):
    e = Entry("e-20261020-140322-ab12", datetime(2026,10,20,14,3,22), "pc", "hello")
    t = Entry("e-20261020-140400-cd34", datetime(2026,10,20,14,4,0), "tablet", "412 mm")
    store.add_entry(e); store.add_entry(t)
    assert store.unfinished_filings() == [e.id]          # tablet entries are never filed
    store.upsert_filing(Filing(e.id, "done")); assert store.unfinished_filings() == []


def test_unfinished_includes_queued_and_running(store):
    for n, st in enumerate(["queued", "running", "failed"]):
        eid = f"e-20261020-14000{n}-aaaa"
        store.add_entry(Entry(eid, datetime(2026,10,20,14,0,n), "pc", "x"))
        store.upsert_filing(Filing(eid, st, files=["a.md"], attempts=n))
    assert store.unfinished_filings() == ["e-20261020-140000-aaaa", "e-20261020-140001-aaaa"]
    assert store.get_filing("e-20261020-140002-aaaa") == Filing(
        "e-20261020-140002-aaaa", "failed", files=["a.md"], attempts=2)
    assert store.get_filing("nope") is None


def test_list_entries_newest_first_day_before(store):
    ids = []
    for d, h in [(19, 9), (20, 9), (20, 10), (20, 11)]:
        e = Entry(f"e-202610{d}-{h:02d}0000-aaaa", datetime(2026,10,d,h,0,0), "pc", f"t{d}{h}")
        store.add_entry(e); ids.append(e.id)
    assert [e.id for e in store.list_entries()] == ids[::-1]
    assert [e.id for e in store.list_entries(day="2026-10-20")] == ids[:0:-1]
    assert [e.id for e in store.list_entries(before=ids[2])] == [ids[1], ids[0]]
    assert len(store.list_entries(limit=2)) == 2
    assert store.get_entry(ids[0]).ts == datetime(2026,10,19,9,0,0)


def test_proposals_lifecycle(store):
    [p] = store.add_proposals("e-1", [("item", {"kind":"task","text":"x","area":"general"}, [])])
    store.update_proposal_payload(p.id, {"text":"y"})
    assert store.list_proposals()[0].payload["text"] == "y"
    store.set_proposal_status([p.id], "confirmed"); assert store.list_proposals() == []
    store.add_proposals("e-1", [("item", {"kind":"task","text":"z","area":"general"}, [])])
    store.delete_proposals("e-1"); assert store.list_proposals() == []
    assert store.list_proposals(status="confirmed")[0].id == p.id


def test_items_crud_and_filters(store):
    i = store.create_item(kind="task", text="check backlash", area="projects", project="robotarm",
                          person=None, due=None, remind_at=None, source_entry="e-1")
    assert i.status == "open" and i.id.startswith("i-")
    store.update_item(i.id, {"status": "done"})
    assert store.list_items(status="open") == [] and store.list_items(project="robotarm")[0].status == "done"
    with pytest.raises(KeyError): store.update_item("i-missing", {"status": "done"})
    with pytest.raises(ValueError): store.update_item(i.id, {"bogus": 1})
    assert [x.id for x in store.list_items(status=["open", "done"])] == [i.id]


def test_fts_search_entry_and_note(store, tmp_path):
    store.add_entry(Entry("e-20261020-140322-ab12", datetime(2026,10,20,14,3,22), "pc", "the wrist servo buzzes"))
    store.index_entry(store.get_entry("e-20261020-140322-ab12"))
    (tmp_path/"projects").mkdir(); (tmp_path/"projects"/"n.md").write_text("# Wrist\nservo backlash 2mm\n", encoding="utf-8")
    store.index_note_file(tmp_path, "projects/n.md")
    hits = store.search('"servo"', 10)
    assert {h.kind for h in hits} == {"entry", "note"} and any(h.ref == "projects/n.md#Wrist" for h in hits)
    assert all("[servo]" in h.snippet for h in hits)


def test_index_note_replaces_rows_and_preamble(store, tmp_path):
    f = tmp_path / "n.md"
    f.write_text("intro zebra\n# A\nalpha\n## B\nbeta\n", encoding="utf-8")
    store.index_note_file(tmp_path, "n.md")
    assert [h.ref for h in store.search('"zebra"', 5)] == ["n.md"]
    assert [h.ref for h in store.search('"beta"', 5)] == ["n.md#B"]
    f.write_text("# A\ngamma\n", encoding="utf-8")
    store.index_note_file(tmp_path, "n.md")
    assert store.search('"zebra"', 5) == [] and store.search('"beta"', 5) == []
    assert [h.ref for h in store.search('"gamma"', 5)] == ["n.md#A"]


def test_diacritics_insensitive(store):
    e = Entry("e-20261020-150000-aaaa", datetime(2026,10,20,15,0,0), "pc", "temă la fizică")
    store.add_entry(e); store.index_entry(e)
    assert store.search('"fizica"', 5)


def test_search_malformed_query_propagates(store):
    import sqlite3
    with pytest.raises(sqlite3.OperationalError):
        store.search('"unterminated', 5)


def test_reindex_all_skips_log_and_items(store, tmp_path):
    append_entry(tmp_path, "quokka sighting", "pc", datetime(2026,10,20,10,0,0))
    (tmp_path/"people").mkdir(); (tmp_path/"people"/"ana.md").write_text("# Ana\nlikes quokka\n", encoding="utf-8")
    (tmp_path/"_items").mkdir(); (tmp_path/"_items"/"uni.md").write_text("- [ ] quokka\n", encoding="utf-8")
    store.reindex_all(tmp_path)
    store.reindex_all(tmp_path)  # idempotent
    hits = store.search('"quokka"', 10)
    assert sorted((h.kind, h.ref.split("#")[0] if h.kind == "note" else "") for h in hits) == [
        ("entry", ""), ("note", "people/ana.md")]
    assert len(store.list_entries()) == 1


def test_export_and_restore(store, tmp_path):
    store.create_item(kind="deadline", text="homework", area="uni", project=None, person=None,
                      due="2026-11-14", remind_at="2026-11-12", source_entry="e-1")
    store.export_items(tmp_path)
    assert "homework" in (tmp_path/"_items"/"uni.md").read_text(encoding="utf-8")
    fresh = Store(tmp_path/"fresh.db"); fresh.init_schema()
    assert fresh.restore_items_if_empty(tmp_path) == 1
    assert fresh.restore_items_if_empty(tmp_path) == 0


def test_export_format_grouping_and_determinism(store, tmp_path):
    store.create_item(kind="task", text="check backlash", area="projects", project="robotarm",
                          person=None, due=None, remind_at=None, source_entry="e-7")
    store.create_item(kind="task", text="finished", area="projects", project="robotarm",
                      person=None, due=None, remind_at=None, source_entry="e-8")
    store.update_item([i for i in store.list_items() if i.text == "finished"][0].id, {"status": "done"})
    store.export_items(tmp_path)
    md = (tmp_path/"_items"/"robotarm.md").read_text(encoding="utf-8")
    assert "- [ ] check backlash (from e-7)" in md and "finished" not in md
    first = (tmp_path/"_items"/"items.json").read_text(encoding="utf-8")
    data = json.loads(first)
    assert [d["id"] for d in data] == sorted(d["id"] for d in data) and len(data) == 2
    store.export_items(tmp_path)
    assert (tmp_path/"_items"/"items.json").read_text(encoding="utf-8") == first
