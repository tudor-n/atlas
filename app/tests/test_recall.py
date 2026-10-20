from pathlib import Path

import pytest

import brainhub
from brainhub.config import DEFAULT_SETTINGS
from brainhub.models import Hit
from brainhub.recall import (NOTHING, build_fts_query, build_quick_prompt, deeper_answer,
                             quick_answer, search_hits)
from tests.fakes import FakeRunner

PROMPTS = Path(brainhub.__file__).parent / "prompts"


@pytest.mark.parametrize("q", ['what about "ID-3 (base)?', "AND OR NEAR", ":", "*", "-", 'x"y', "ID-3 → ID-7"])
def test_fts_query_never_raises(store, q):
    fq = build_fts_query(q)
    if fq:
        store.search(fq, 5)  # must not raise sqlite3.OperationalError


def test_query_drops_stopwords():
    assert build_fts_query("what did I say about the wrist servo?") == '"wrist" OR "servo"'
    assert build_fts_query("what is it?") is None


def test_search_hits_empty_query_returns_empty(store):
    assert search_hits(store, "what is it?", 5) == []


def test_prompt_truncates_huge_hit_text():
    hit = Hit("entry", "e-1", "2026-10-20", "...", "x" * 5000)
    prompt = build_quick_prompt("q?", [hit])
    assert "…" in prompt and len(prompt) < 2000


async def test_no_hits_yields_nothing_without_calling_claude(tmp_path):
    r = FakeRunner(stream_chunks=["should not appear"])
    out = [c async for c in quick_answer(r, "q", [], DEFAULT_SETTINGS, PROMPTS, tmp_path)]
    assert out == [NOTHING] and r.calls == []


async def test_quick_answer_uses_no_tools_one_turn(tmp_path):
    r = FakeRunner(stream_chunks=["Oct 20: ", "wrist buzzes [e-20261020-140322-ab12]"])
    hit = Hit("entry", "e-20261020-140322-ab12", "2026-10-20", "...", "the wrist servo buzzes")
    out = "".join([c async for c in quick_answer(r, "wrist?", [hit], DEFAULT_SETTINGS, PROMPTS, tmp_path)])
    assert "[e-20261020-140322-ab12]" in out
    call = r.calls[0]
    assert call["allowed_tools"] == [] and call["max_turns"] == 1 and call["model"] == "haiku"
    assert "[e-20261020-140322-ab12] (2026-10-20) the wrist servo buzzes" in call["prompt"]


async def test_deeper_answer_uses_read_tools_in_brain(tmp_path):
    r = FakeRunner(stream_chunks=["ok"])
    out = [c async for c in deeper_answer(r, "wrist?", DEFAULT_SETTINGS, PROMPTS, tmp_path)]
    assert out == ["ok"]
    call = r.calls[0]
    assert call["allowed_tools"] == ["Read", "Glob", "Grep"] and call["max_turns"] == 15
    assert call["model"] == "sonnet" and call["cwd"] == tmp_path
    assert call["system_prompt_file"] == PROMPTS / "deeper.md"


def test_prompt_files_carry_the_rules():
    for name in ("recall.md", "deeper.md"):
        t = (PROMPTS / name).read_text(encoding="utf-8")
        assert "Nothing on that." in t and "data" in t and "[e-20261020-140322-ab12]" in t
    assert "_items/" in (PROMPTS / "deeper.md").read_text(encoding="utf-8")
