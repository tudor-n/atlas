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
