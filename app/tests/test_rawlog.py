import re
from datetime import datetime

import pytest

from brainhub.rawlog import append_entry, log_path, read_log

HOSTILE = ("## Heading\r\n<script>alert(1)</script>\n<!-- /entry -->\n"
           "<!-- entry id=e-x ts=x source=pc len=1 -->\nșțăî 🤖\n" + "z" * 10000)


def test_append_and_read(tmp_path):
    e = append_entry(tmp_path, "buy present before the 14th", "pc", datetime(2026, 10, 20, 14, 3, 22))
    assert re.fullmatch(r"e-20261020-140322-[0-9a-f]{4}", e.id)
    assert log_path(tmp_path, e.ts).exists()
    assert log_path(tmp_path, e.ts) == tmp_path / "log" / "2026" / "10" / "2026-10-20.md"
    assert read_log(tmp_path) == [e]


def test_on_disk_format_exact(tmp_path):
    e = append_entry(tmp_path, "hello", "widget", datetime(2026, 10, 20, 14, 3, 22))
    raw = log_path(tmp_path, e.ts).read_bytes().decode("utf-8")
    assert raw == (f"<!-- entry id={e.id} ts=2026-10-20T14:03:22 source=widget len=5 -->\n"
                   "hello\n<!-- /entry -->\n\n")


def test_roundtrip_hostile_text(tmp_path):
    append_entry(tmp_path, HOSTILE, "pc")
    append_entry(tmp_path, "second", "widget")
    got = read_log(tmp_path)
    assert got[0].text == HOSTILE and got[1].text == "second"


def test_read_in_date_order_across_files(tmp_path):
    b = append_entry(tmp_path, "later", "pc", datetime(2026, 11, 2, 8, 0, 0))
    a = append_entry(tmp_path, "earlier", "tablet", datetime(2026, 10, 20, 9, 0, 0))
    assert read_log(tmp_path) == [a, b]


def test_read_empty(tmp_path):
    assert read_log(tmp_path) == []


def test_rejects_blank_and_bad_source(tmp_path):
    with pytest.raises(ValueError):
        append_entry(tmp_path, "   \n", "pc")
    with pytest.raises(ValueError):
        append_entry(tmp_path, "x", "fax")
    assert not (tmp_path / "log").exists()


def test_append_never_recreates_a_vanished_brain(tmp_path):
    """The drive went away mid-run: the append refuses, and neither brain/ nor the root comes back."""
    root = tmp_path / "BrainHub"
    with pytest.raises(OSError, match="isn't there"):
        append_entry(root / "brain", "typed after the drive went", "pc")
    assert not root.exists()
    (root / "brain").mkdir(parents=True)  # brain/ there, log/ not yet: only the levels below it are made
    e = append_entry(root / "brain", "first entry", "pc", datetime(2026, 10, 20, 9, 0, 0))
    assert read_log(root / "brain") == [e]


def test_file_problems(tmp_path):
    from brainhub.rawlog import file_problems
    e = append_entry(tmp_path, "some text here", "pc", datetime(2026, 10, 20, 9, 0, 0))
    assert file_problems(log_path(tmp_path, e.ts)) == []
    assert file_problems(log_path(tmp_path, datetime(2026, 10, 21))) == []  # absent
    p = log_path(tmp_path, e.ts)
    p.write_bytes(p.read_bytes()[:-30])
    [problem] = file_problems(p)
    assert "2026-10-20.md" in problem and "truncated" in problem


def test_append_only(tmp_path):
    a = append_entry(tmp_path, "one", "pc", datetime(2026, 10, 20, 9, 0, 0))
    before = log_path(tmp_path, a.ts).read_bytes()
    append_entry(tmp_path, "two", "pc", datetime(2026, 10, 20, 10, 0, 0))
    assert log_path(tmp_path, a.ts).read_bytes().startswith(before)


def test_truncated_file_raises(tmp_path):
    e = append_entry(tmp_path, "some text here", "pc", datetime(2026, 10, 20, 9, 0, 0))
    p = log_path(tmp_path, e.ts)
    p.write_bytes(p.read_bytes()[:-30])
    with pytest.raises(ValueError, match=r"2026-10-20\.md.*offset 0"):
        read_log(tmp_path)


def test_bad_len_raises(tmp_path):
    e = append_entry(tmp_path, "abc", "pc", datetime(2026, 10, 20, 9, 0, 0))
    p = log_path(tmp_path, e.ts)
    p.write_bytes(p.read_bytes().replace(b"len=3", b"len=2"))
    with pytest.raises(ValueError, match="2026-10-20.md"):
        read_log(tmp_path)


def test_concurrent_appends_from_threads_stay_intact(tmp_path):
    import threading
    n = 8
    barrier = threading.Barrier(n)

    def work(i):
        barrier.wait()
        append_entry(tmp_path, f"thread {i} " + "y" * (100 * i), "pc")

    threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    got = sorted(e.text for e in read_log(tmp_path))
    assert got == sorted(f"thread {i} " + "y" * (100 * i) for i in range(n))


def test_tolerant_read_skips_damage_and_keeps_intact_entries(tmp_path):
    good = append_entry(tmp_path, "good day", "pc", datetime(2026, 10, 19, 9, 0, 0))
    a = append_entry(tmp_path, "before the damage", "pc", datetime(2026, 10, 20, 9, 0, 0))
    b = append_entry(tmp_path, "the damaged one", "pc", datetime(2026, 10, 20, 10, 0, 0))
    c = append_entry(tmp_path, "after the damage", "pc", datetime(2026, 10, 20, 11, 0, 0))
    bad = log_path(tmp_path, a.ts)
    raw = bad.read_bytes()
    bad.write_bytes(raw.replace(b"len=15", b"len=99"))  # b's len no longer matches its text
    stray = tmp_path / "log" / "notes.md"
    stray.write_text("not a log file", encoding="utf-8")
    before = {p: p.read_bytes() for p in (tmp_path / "log").rglob("*.md")}
    with pytest.raises(ValueError):
        read_log(tmp_path)
    problems = []
    got = read_log(tmp_path, problems)
    assert [e.id for e in got] == [good.id, a.id, c.id]
    assert any("2026-10-20.md" in p and "offset" in p for p in problems)
    assert any("notes.md" in p for p in problems)
    assert {p: p.read_bytes() for p in (tmp_path / "log").rglob("*.md")} == before  # never rewritten
    assert b.id not in [e.id for e in got]


def test_tolerant_read_truncated_tail_and_bad_utf8(tmp_path):
    good = append_entry(tmp_path, "intact", "pc", datetime(2026, 10, 19, 9, 0, 0))
    t = append_entry(tmp_path, "some text here", "pc", datetime(2026, 10, 20, 9, 0, 0))
    p = log_path(tmp_path, t.ts)
    p.write_bytes(p.read_bytes()[:-30])
    u = log_path(tmp_path, datetime(2026, 10, 21))
    u.parent.mkdir(parents=True, exist_ok=True)
    u.write_bytes(b"\xff\xfe junk")
    problems = []
    assert read_log(tmp_path, problems) == [good]
    assert any("2026-10-20.md" in x and "truncated" in x for x in problems)
    assert any("2026-10-21.md" in x and "UTF-8" in x for x in problems)


def test_same_second_ids_never_collide(tmp_path, monkeypatch):
    import brainhub.rawlog as rl
    draws = iter(["aaaa", "aaaa", "aaaa", "bbbb"])
    monkeypatch.setattr(rl.secrets, "token_hex", lambda n: next(draws))
    now = datetime(2026, 10, 20, 9, 0, 0)
    first = append_entry(tmp_path, "one", "pc", now)
    second = append_entry(tmp_path, "two", "pc", now)
    assert first.id.endswith("-aaaa") and second.id.endswith("-bbbb")
    assert [e.text for e in read_log(tmp_path)] == ["one", "two"]


def test_append_fsyncs_inside_the_lock(tmp_path, monkeypatch):
    import brainhub.rawlog as rl
    seen = []
    real = rl.os.fsync
    monkeypatch.setattr(rl.os, "fsync", lambda fd: (seen.append(rl._APPEND_LOCK.locked()), real(fd)))
    append_entry(tmp_path, "durable", "pc")
    assert seen == [True]


def test_append_keeps_a_given_id(tmp_path):
    ts = datetime(2026, 10, 20, 23, 59, 30)
    e = append_entry(tmp_path, "held while D: was away", "widget", ts, entry_id="e-20261020-235930-beef")
    assert e.id == "e-20261020-235930-beef" and e.ts == ts
    assert read_log(tmp_path) == [e]
    for bad in ("e-20261020-235931-beef",  # its timestamp part is not ts
                "e-20261020-235930-BEEF", "x", "e-20261020-235930-beef\n", 42):
        with pytest.raises(ValueError):
            append_entry(tmp_path, "nope", "pc", ts, entry_id=bad)
    assert read_log(tmp_path) == [e]  # nothing written for a refused id
