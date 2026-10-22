"""Holding inbox (dual-boot design §6.5 item 3): a capture typed while the root isn't writable is
on disk before "saved", and later reaches the raw log exactly once, with its own ts and id."""
import json
import os
import re
from datetime import datetime

import pytest

from brainhub import holding
from brainhub.models import Entry
from brainhub.rawlog import append_entry, log_path, read_log


def _held_file(state, record):
    return state / "holding" / f"{record['id']}.json"


def test_hold_is_durable_and_listed(tmp_path, monkeypatch):
    state = tmp_path / "state"
    synced, real_fsync = [], os.fsync

    def fsync(fd):
        synced.append(fd)
        real_fsync(fd)
    monkeypatch.setattr(holding.os, "fsync", fsync)
    text = "remember the wrist servo\r\n  verbatim, with <!-- /entry --> and 🤖  "
    r = holding.hold(state, text, "widget", datetime(2026, 10, 20, 23, 59, 30, 123456))
    assert re.fullmatch(r"e-20261020-235930-[0-9a-f]{4}", r["id"])
    assert r == {"id": r["id"], "ts": "2026-10-20T23:59:30", "source": "widget", "text": text}
    assert json.loads(_held_file(state, r).read_text(encoding="utf-8")) == r
    # The file, then on POSIX its new folder and that folder's parent, all fsynced before return.
    assert len(synced) == (3 if os.name == "posix" else 1)
    assert [p.name for p in (state / "holding").iterdir()] == [_held_file(state, r).name]  # no tmp left
    earlier = holding.hold(state, "second, but older", "pc", datetime(2026, 10, 20, 9, 0, 0))
    assert holding.held(state) == [earlier, r]  # oldest first
    with pytest.raises(ValueError):
        holding.hold(state, "  \n", "pc", datetime.now())
    with pytest.raises(ValueError):
        holding.hold(state, "x", "fax", datetime.now())
    assert len(holding.held(state)) == 2
    assert holding.held(tmp_path / "nothing-held-here") == []


def test_hold_ids_never_clash_within_a_second(tmp_path, monkeypatch):
    draws = iter(["e-20261020-090000-aaaa", "e-20261020-090000-aaaa", "e-20261020-090000-bbbb"])
    monkeypatch.setattr(holding.rawlog, "new_entry_id", lambda ts: next(draws))
    now = datetime(2026, 10, 20, 9, 0, 0)
    a, b = holding.hold(tmp_path, "one", "pc", now), holding.hold(tmp_path, "two", "pc", now)
    assert (a["id"], b["id"]) == ("e-20261020-090000-aaaa", "e-20261020-090000-bbbb")
    assert [h["text"] for h in holding.held(tmp_path)] == ["one", "two"]


def test_capture_held_then_flushed_with_original_ts(tmp_path, paths, store):
    state = tmp_path / "state"
    r = holding.hold(state, "call the bank\nbefore Friday", "pc", datetime(2026, 10, 20, 23, 59, 30))
    # By the time the drive is back (the 22nd), that day has entries: the held one still goes to its own day.
    later = append_entry(paths.brain, "written on the 22nd", "widget", datetime(2026, 10, 22, 8, 0, 0))
    assert holding.flush(state, paths.brain, store) == [r["id"]]
    day = paths.brain / "log" / "2026" / "10" / "2026-10-20.md"
    assert log_path(paths.brain, datetime(2026, 10, 20)) == day
    assert day.read_bytes().decode("utf-8") == (
        f"<!-- entry id={r['id']} ts=2026-10-20T23:59:30 source=pc len=27 -->\n"
        "call the bank\nbefore Friday\n<!-- /entry -->\n\n")
    assert read_log(paths.brain) == [
        Entry(r["id"], datetime(2026, 10, 20, 23, 59, 30), "pc", "call the bank\nbefore Friday"), later]
    assert holding.held(state) == [] and not _held_file(state, r).exists()
    assert holding.flush(state, paths.brain, store) == []  # nothing left to do


def test_flush_is_idempotent_after_crash(tmp_path, paths, store):
    state = tmp_path / "state"
    r = holding.hold(state, "only once", "widget", datetime(2026, 10, 20, 10, 0, 0))
    # A crash between the append and the delete: the log has it and the file is still held.
    e = append_entry(paths.brain, r["text"], r["source"], now=datetime.fromisoformat(r["ts"]), entry_id=r["id"])
    before = log_path(paths.brain, e.ts).read_bytes()
    assert holding.flush(state, paths.brain, store) == []
    assert log_path(paths.brain, e.ts).read_bytes() == before  # no duplicate
    assert holding.held(state) == []
    # The same when the store has indexed it too.
    r2 = holding.hold(state, "indexed already", "pc", datetime(2026, 10, 20, 11, 0, 0))
    e2 = append_entry(paths.brain, r2["text"], "pc", now=datetime(2026, 10, 20, 11, 0, 0), entry_id=r2["id"])
    store.add_entry(e2)
    assert holding.flush(state, paths.brain, store) == []
    assert [x.id for x in read_log(paths.brain)] == [e.id, e2.id]
    assert holding.held(state) == []


def test_flush_never_drops_a_clashing_id(tmp_path, paths, store):
    state = tmp_path / "state"
    r = holding.hold(state, "mine", "pc", datetime(2026, 10, 20, 10, 0, 0))
    append_entry(paths.brain, "a different entry", "pc", now=datetime(2026, 10, 20, 10, 0, 0), entry_id=r["id"])
    problems = []
    assert holding.flush(state, paths.brain, store, problems) == []
    assert holding.held(state) == [r] and len(problems) == 1 and "kept" in problems[0]


def test_unreadable_held_file_is_kept(tmp_path, paths, store):
    state = tmp_path / "state"
    good = holding.hold(state, "good", "pc", datetime(2026, 10, 20, 9, 0, 0))
    folder = state / "holding"
    broken = folder / "e-20261020-090001-abcd.json"
    broken.write_text("{half", encoding="utf-8")
    mismatched = folder / "e-20261020-090002-abcd.json"  # its ts says another day than its id
    mismatched.write_text(json.dumps({"id": "e-20261020-090002-abcd", "ts": "2026-10-21T09:00:02",
                                      "source": "pc", "text": "x"}), encoding="utf-8")
    (folder / "e-20261020-090003-abcd.json.123.tmp").write_text("{", encoding="utf-8")  # an interrupted hold
    problems = []
    assert holding.held(state, problems) == [good]
    assert len(problems) == 2 and all("kept" in p for p in problems)
    problems = []
    assert holding.flush(state, paths.brain, store, problems) == [good["id"]]
    assert len(problems) == 2
    assert broken.read_text(encoding="utf-8") == "{half" and mismatched.exists()
    assert [e.id for e in read_log(paths.brain)] == [good["id"]]


def test_flush_stops_when_the_log_cant_be_written(tmp_path, paths, store, monkeypatch):
    state = tmp_path / "state"
    a = holding.hold(state, "first", "pc", datetime(2026, 10, 20, 9, 0, 0))
    b = holding.hold(state, "second", "pc", datetime(2026, 10, 20, 9, 0, 1))

    def full(*args, **kwargs):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(holding.rawlog, "append_entry", full)
    problems = []
    assert holding.flush(state, paths.brain, store, problems) == []
    assert holding.held(state) == [a, b] and len(problems) == 1 and "stay held" in problems[0]
