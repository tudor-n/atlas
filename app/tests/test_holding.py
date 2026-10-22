"""Holding inbox (dual-boot design §6.5 item 3): a capture typed while the root isn't writable is
on disk before "saved", and later reaches that root's raw log exactly once, with its own ts and id."""
import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from brainhub import holding, rootguard
from brainhub.config import Paths
from brainhub.models import Entry
from brainhub.rawlog import append_entry, log_path, read_log

win_only = pytest.mark.skipif(sys.platform != "win32", reason="MoveFileExW is Windows only")


def _held_file(state, record):
    return state / "holding" / f"{record['id']}.json"


def _write(path, record):
    """Exactly what a hold writes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return path


def _tmp_file(state, record, pid):
    """A hold's tmp for `record`, as it is before being put in place."""
    return _write(state / "holding" / f"{record['id']}.json.{pid}.tmp", record)


def _age(path, seconds=holding.TMP_GRACE + 5):
    old = time.time() - seconds
    os.utime(path, (old, old))


def _marked(root) -> Paths:
    """A marked brain at `root` (its map is enough for flush)."""
    paths = Paths(root)
    paths.brain.mkdir(parents=True)
    rootguard.create_marker(paths)
    return paths


def test_hold_is_durable_and_listed(tmp_path, paths, monkeypatch):
    state = tmp_path / "state"
    synced, real_fsync = [], os.fsync

    def fsync(fd):
        synced.append(fd)
        real_fsync(fd)
    monkeypatch.setattr(holding.os, "fsync", fsync)
    text = "remember the wrist servo\r\n  verbatim, with <!-- /entry --> and 🤖  "
    r = holding.hold(state, paths, text, "widget", datetime(2026, 10, 20, 23, 59, 30, 123456))
    assert re.fullmatch(r"e-20261020-235930-[0-9a-f]{4}", r["id"])
    assert r == {"id": r["id"], "ts": "2026-10-20T23:59:30", "source": "widget", "text": text,
                 "root": {"id": None, "path": str(paths.root.resolve())}}  # no marker yet: the path
    assert json.loads(_held_file(state, r).read_text(encoding="utf-8")) == r
    # The file, then on POSIX its folder, and the parents of the two folders the hold created
    # (state/ and state/holding/), all fsynced before return.
    assert len(synced) == (4 if os.name == "posix" else 1)
    assert [p.name for p in (state / "holding").iterdir()] == [_held_file(state, r).name]  # no tmp left
    earlier = holding.hold(state, paths, "second, but older", "pc", datetime(2026, 10, 20, 9, 0, 0))
    assert holding.held(state) == [earlier, r]  # oldest first
    with pytest.raises(ValueError):
        holding.hold(state, paths, "  \n", "pc", datetime.now())
    with pytest.raises(ValueError):
        holding.hold(state, paths, "x", "fax", datetime.now())
    assert len(holding.held(state)) == 2
    assert holding.held(tmp_path / "nothing-held-here") == []


def test_hold_records_the_marker_id_when_readable(tmp_path):
    paths = _marked(tmp_path / "BrainHub")
    r = holding.hold(tmp_path / "state", paths, "x", "pc", datetime(2026, 10, 20, 9, 0, 0))
    assert r["root"] == {"id": rootguard.read_marker(paths)["id"], "path": str(paths.root.resolve())}


def test_hold_ids_never_clash_within_a_second(tmp_path, paths, monkeypatch):
    draws = iter(["e-20261020-090000-aaaa", "e-20261020-090000-aaaa", "e-20261020-090000-bbbb"])
    monkeypatch.setattr(holding.rawlog, "new_entry_id", lambda ts: next(draws))
    now = datetime(2026, 10, 20, 9, 0, 0)
    a, b = holding.hold(tmp_path, paths, "one", "pc", now), holding.hold(tmp_path, paths, "two", "pc", now)
    assert (a["id"], b["id"]) == ("e-20261020-090000-aaaa", "e-20261020-090000-bbbb")
    assert [h["text"] for h in holding.held(tmp_path)] == ["one", "two"]


def test_hold_never_overwrites_a_capture_another_process_held(tmp_path, paths, monkeypatch):
    state = tmp_path / "state"
    # Another hub sharing this state folder held a capture in the same second, with the same id.
    theirs = {"id": "e-20261020-090000-aaaa", "ts": "2026-10-20T09:00:00", "source": "widget",
              "text": "theirs", "root": {"id": None, "path": "/elsewhere"}}
    _write(_held_file(state, theirs), theirs)
    before = _held_file(state, theirs).read_bytes()
    draws = iter(["e-20261020-090000-aaaa", "e-20261020-090000-bbbb"])
    monkeypatch.setattr(holding.rawlog, "new_entry_id", lambda ts: next(draws))
    mine = holding.hold(state, paths, "mine", "pc", datetime(2026, 10, 20, 9, 0, 0))
    assert mine["id"] == "e-20261020-090000-bbbb"
    assert _held_file(state, theirs).read_bytes() == before
    assert [h["text"] for h in holding.held(state)] == ["theirs", "mine"]
    assert sorted(p.name for p in (state / "holding").iterdir()) == [
        "e-20261020-090000-aaaa.json", "e-20261020-090000-bbbb.json"]  # no tmp left


def test_hold_moves_with_write_through_on_windows(tmp_path, paths, monkeypatch):
    """On Windows the tmp is put in place with MoveFileExW (write-through, never overwriting), not
    os.replace; faked here so every OS checks the wiring."""
    moves = []

    def win_move(src, dst, *, replace):
        moves.append((src.name, dst.name, replace))
        os.rename(src, dst)
    monkeypatch.setattr(holding, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(holding, "_win_move", win_move)
    r = holding.hold(tmp_path / "state", paths, "x", "pc", datetime(2026, 10, 20, 9, 0, 0))
    assert moves == [(f"{r['id']}.json.{os.getpid()}.tmp", f"{r['id']}.json", False)]


@win_only
def test_windows_move_writes_through_and_never_overwrites(tmp_path):
    a, b, dst = tmp_path / "a", tmp_path / "b", tmp_path / "dst"
    a.write_text("first", encoding="utf-8")
    b.write_text("second", encoding="utf-8")
    holding._win_move(a, dst, replace=False)
    assert dst.read_text(encoding="utf-8") == "first" and not a.exists()
    with pytest.raises(FileExistsError):
        holding._win_move(b, dst, replace=False)
    assert dst.read_text(encoding="utf-8") == "first" and b.read_text(encoding="utf-8") == "second"
    holding._win_move(b, dst, replace=True)
    assert dst.read_text(encoding="utf-8") == "second" and not b.exists()
    with pytest.raises(FileNotFoundError):
        holding._win_move(tmp_path / "missing", dst, replace=True)


def test_interrupted_hold_is_recovered(tmp_path, paths, store):
    """A power cut after the tmp was written but before its rename was on disk leaves only the tmp:
    it is put in place and flushed. A tmp that can't be used is reported, and nothing is deleted."""
    state = tmp_path / "state"
    root = holding.root_identity(paths)

    def record(second, text):
        return {"id": f"e-20261020-0900{second:02d}-abcd", "ts": f"2026-10-20T09:00:{second:02d}",
                "source": "pc", "text": text, "root": root}
    lone = record(1, "only the tmp survived")
    tmp = _tmp_file(state, lone, os.getppid())
    _age(tmp)  # from before the reboot: its pid may well belong to another process by now
    twice = record(2, "a crash right after the link")
    _write(_held_file(state, twice), twice)
    _age(_tmp_file(state, twice, 4242))
    clash = record(3, "the placed one")
    _write(_held_file(state, clash), clash)
    clash_tmp = _tmp_file(state, {**clash, "text": "a different one"}, 4242)
    _age(clash_tmp)
    broken = state / "holding" / "e-20261020-090004-abcd.json.4242.tmp"
    broken.write_text("{half", encoding="utf-8")
    _age(broken)
    live = _tmp_file(state, record(5, "still being written by another hub"), os.getppid())  # new, live pid
    live_bytes = live.read_bytes()

    problems = []
    assert holding.held(state, problems) == [lone, twice, clash]
    assert len(problems) == 2 and all("kept" in p for p in problems)
    assert any(clash_tmp.name in p and "different capture" in p for p in problems)
    assert any(broken.name in p for p in problems)
    assert not tmp.exists() and json.loads(_held_file(state, lone).read_text(encoding="utf-8")) == lone
    assert clash_tmp.exists() and broken.read_text(encoding="utf-8") == "{half"
    assert live.read_bytes() == live_bytes and not (state / "holding" / f"{record(5, '')['id']}.json").exists()
    assert sorted(p.name for p in (state / "holding").iterdir()) == sorted([
        f"{lone['id']}.json", f"{twice['id']}.json", f"{clash['id']}.json", clash_tmp.name, broken.name,
        live.name])

    flushed = holding.flush(state, paths, store)
    assert flushed.appended == [lone["id"], twice["id"], clash["id"]]
    assert [e.text for e in read_log(paths.brain)] == [lone["text"], twice["text"], clash["text"]]


def test_capture_held_then_flushed_with_original_ts(tmp_path, paths, store):
    state = tmp_path / "state"
    r = holding.hold(state, paths, "call the bank\nbefore Friday", "pc", datetime(2026, 10, 20, 23, 59, 30))
    # By the time the drive is back (the 22nd), that day has entries: the held one still goes to its own day.
    later = append_entry(paths.brain, "written on the 22nd", "widget", datetime(2026, 10, 22, 8, 0, 0))
    assert holding.flush(state, paths, store) == holding.Flushed(appended=[r["id"]])
    day = paths.brain / "log" / "2026" / "10" / "2026-10-20.md"
    assert log_path(paths.brain, datetime(2026, 10, 20)) == day
    assert day.read_bytes().decode("utf-8") == (
        f"<!-- entry id={r['id']} ts=2026-10-20T23:59:30 source=pc len=27 -->\n"
        "call the bank\nbefore Friday\n<!-- /entry -->\n\n")
    assert read_log(paths.brain) == [
        Entry(r["id"], datetime(2026, 10, 20, 23, 59, 30), "pc", "call the bank\nbefore Friday"), later]
    assert holding.held(state) == [] and not _held_file(state, r).exists()
    assert holding.flush(state, paths, store) == holding.Flushed()  # nothing left to do


def test_flush_is_idempotent_after_crash(tmp_path, paths, store):
    state = tmp_path / "state"
    r = holding.hold(state, paths, "only once", "widget", datetime(2026, 10, 20, 10, 0, 0))
    # A crash between the append and the delete: the log has it and the file is still held.
    e = append_entry(paths.brain, r["text"], r["source"], now=datetime.fromisoformat(r["ts"]), entry_id=r["id"])
    before = log_path(paths.brain, e.ts).read_bytes()
    assert holding.flush(state, paths, store).appended == []
    assert log_path(paths.brain, e.ts).read_bytes() == before  # no duplicate
    assert holding.held(state) == []
    # The same when the store has indexed it too.
    r2 = holding.hold(state, paths, "indexed already", "pc", datetime(2026, 10, 20, 11, 0, 0))
    e2 = append_entry(paths.brain, r2["text"], "pc", now=datetime(2026, 10, 20, 11, 0, 0), entry_id=r2["id"])
    store.add_entry(e2)
    assert holding.flush(state, paths, store).appended == []
    assert [x.id for x in read_log(paths.brain)] == [e.id, e2.id]
    assert holding.held(state) == []


def test_flush_never_drops_a_clashing_id(tmp_path, paths, store):
    state = tmp_path / "state"
    r = holding.hold(state, paths, "mine", "pc", datetime(2026, 10, 20, 10, 0, 0))
    append_entry(paths.brain, "a different entry", "pc", now=datetime(2026, 10, 20, 10, 0, 0), entry_id=r["id"])
    problems = []
    assert holding.flush(state, paths, store, problems).appended == []
    assert holding.held(state) == [r] and len(problems) == 1 and "kept" in problems[0]


def test_unreadable_held_file_is_kept(tmp_path, paths, store):
    state = tmp_path / "state"
    good = holding.hold(state, paths, "good", "pc", datetime(2026, 10, 20, 9, 0, 0))
    folder = state / "holding"
    broken = folder / "e-20261020-090001-abcd.json"
    broken.write_text("{half", encoding="utf-8")
    mismatched = folder / "e-20261020-090002-abcd.json"  # its ts says another day than its id
    mismatched.write_text(json.dumps({"id": "e-20261020-090002-abcd", "ts": "2026-10-21T09:00:02",
                                      "source": "pc", "text": "x"}), encoding="utf-8")
    bad_root = folder / "e-20261020-090003-abcd.json"
    bad_root.write_text(json.dumps({"id": "e-20261020-090003-abcd", "ts": "2026-10-20T09:00:03",
                                    "source": "pc", "text": "x", "root": "D:/BrainHub"}), encoding="utf-8")
    interrupted = folder / "e-20261020-090004-abcd.json.123.tmp"  # a hold cut off mid-write
    interrupted.write_text("{", encoding="utf-8")
    _age(interrupted)
    problems = []
    assert holding.held(state, problems) == [good]
    assert len(problems) == 4 and all("kept" in p for p in problems)
    problems = []
    assert holding.flush(state, paths, store, problems).appended == [good["id"]]
    assert len(problems) == 4
    assert broken.read_text(encoding="utf-8") == "{half" and mismatched.exists() and bad_root.exists()
    assert interrupted.read_text(encoding="utf-8") == "{"
    assert [e.id for e in read_log(paths.brain)] == [good["id"]]


def test_unreadable_file_is_logged_once(tmp_path, caplog):
    folder = tmp_path / "state" / "holding"
    folder.mkdir(parents=True)
    (folder / "e-20261020-090001-abcd.json").write_text("{half", encoding="utf-8")
    caplog.set_level(logging.ERROR, logger="brainhub.holding")
    for _ in range(3):  # /api/state and /api/widget polls
        problems = []
        holding.held(tmp_path / "state", problems)
        assert len(problems) == 1  # still listed every time
    assert len([r for r in caplog.records if "could not be read" in r.getMessage()]) == 1


def test_flush_stops_when_the_log_cant_be_written(tmp_path, paths, store, monkeypatch):
    state = tmp_path / "state"
    a = holding.hold(state, paths, "first", "pc", datetime(2026, 10, 20, 9, 0, 0))
    b = holding.hold(state, paths, "second", "pc", datetime(2026, 10, 20, 9, 0, 1))

    def full(*args, **kwargs):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(holding.rawlog, "append_entry", full)
    problems = []
    assert holding.flush(state, paths, store, problems).appended == []
    assert holding.held(state) == [a, b] and len(problems) == 1 and "stay held" in problems[0]


def test_flush_keeps_a_capture_whose_day_file_is_damaged(tmp_path, paths, store):
    """The tolerant reader would skip an entry appended to a damaged day file with the damage."""
    state = tmp_path / "state"
    a = holding.hold(state, paths, "for the bad-UTF-8 day", "pc", datetime(2026, 10, 19, 9, 0, 0))
    b = holding.hold(state, paths, "for the truncated day", "pc", datetime(2026, 10, 20, 9, 0, 0))
    c = holding.hold(state, paths, "for an intact day", "pc", datetime(2026, 10, 21, 9, 0, 0))
    bad_utf8 = log_path(paths.brain, datetime(2026, 10, 19))
    bad_utf8.parent.mkdir(parents=True)
    bad_utf8.write_bytes(b"\xff\xfe junk")
    cut = append_entry(paths.brain, "cut short later", "pc", datetime(2026, 10, 20, 8, 0, 0))
    truncated = log_path(paths.brain, cut.ts)
    truncated.write_bytes(truncated.read_bytes()[:-30])
    before = {p: p.read_bytes() for p in (bad_utf8, truncated)}
    problems = []
    assert holding.flush(state, paths, store, problems).appended == [c["id"]]
    assert holding.held(state) == [a, b]
    assert len(problems) == 2 and all("stays held" in p for p in problems)
    assert any("2026-10-19.md" in p and "UTF-8" in p for p in problems)
    assert any("2026-10-20.md" in p and "truncated" in p for p in problems)
    assert {p: p.read_bytes() for p in (bad_utf8, truncated)} == before  # never touched


# -- one holding folder per OS, many roots ----------------------------------------
def test_capture_held_for_another_root_is_not_flushed(tmp_path, store):
    state = tmp_path / "state"
    real = Paths(tmp_path / "mnt" / "data" / "BrainHub")  # missing while the capture was typed
    r = holding.hold(state, real, "for the real brain", "widget", datetime(2026, 10, 20, 9, 0, 0))
    throwaway = _marked(tmp_path / "dev-root")  # a dev hub on a temp root, same state folder
    flushed = holding.flush(state, throwaway, store)
    assert flushed == holding.Flushed(appended=[], elsewhere=[r])
    assert read_log(throwaway.brain) == [] and holding.held(state) == [r]
    assert holding.held(state, paths=throwaway) == [] and holding.held(state, paths=real) == [r]
    _marked(real.root)  # the real drive is back: its own hub flushes it
    assert holding.flush(state, real, store).appended == [r["id"]]
    assert [e.text for e in read_log(real.brain)] == ["for the real brain"]


def test_marker_id_decides_over_the_path(tmp_path, store):
    state = tmp_path / "state"
    first = _marked(tmp_path / "media" / "DATA" / "BrainHub")  # read-only: marker readable, id recorded
    r = holding.hold(state, first, "typed while read-only", "pc", datetime(2026, 10, 20, 9, 0, 0))
    # The same drive, mounted at another path the next time.
    again = Paths(tmp_path / "mnt" / "data" / "BrainHub")
    shutil.copytree(first.root, again.root)
    shutil.rmtree(first.root)
    assert holding.held(state, paths=again) == [r]
    # A different brain later set up at the first path is not the same root.
    other = _marked(first.root)
    assert holding.held(state, paths=other) == []
    assert holding.flush(state, other, store).elsewhere == [r]
    assert holding.flush(state, again, store).appended == [r["id"]]
    assert [e.text for e in read_log(again.brain)] == ["typed while read-only"]


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS paths compare without case")
def test_root_paths_compare_without_case_on_windows(tmp_path):
    state = tmp_path / "state"
    r = holding.hold(state, Paths(tmp_path / "BrainHub"), "x", "pc", datetime(2026, 10, 20, 9, 0, 0))
    assert holding.held(state, paths=Paths(str(tmp_path / "BRAINHUB"))) == [r]


def test_record_from_before_roots_matches_any_root(tmp_path, store):
    state = tmp_path / "state"
    folder = state / "holding"
    folder.mkdir(parents=True)
    old = {"id": "e-20261020-090000-abcd", "ts": "2026-10-20T09:00:00", "source": "pc", "text": "old shape"}
    (folder / f"{old['id']}.json").write_text(json.dumps(old), encoding="utf-8")
    somewhere = _marked(tmp_path / "anywhere")
    assert holding.held(state, paths=somewhere) == [old]
    assert holding.flush(state, somewhere, store).appended == [old["id"]]
