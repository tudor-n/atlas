import random
from datetime import datetime, timedelta

from atlas import quotes as q
from atlas.filing import protected_tops
from atlas.store import is_note_path

NOW = datetime(2026, 10, 23, 15, 0)


def test_seed_has_at_least_100_quotes_in_every_pool_and_no_problems():
    pools, problems = q.parse(q.SEED)
    assert problems == []
    assert sum(len(v) for v in pools.values()) >= 100  # the owner's requirement (spec §8)
    assert all(len(pools[p]) >= 8 for p in q.POOLS)


def test_seed_follows_the_wording_rules():
    pools, _ = q.parse(q.SEED)
    for quote in (x for v in pools.values() for x in v):
        assert ":" not in quote and " - " not in quote and "—" not in quote, quote
        assert quote[-1] in ".?!", quote


def test_parse_reports_problems_with_line_numbers_and_keeps_the_rest():
    text = "stray\n## agent_finished\nGood one.\nBad {nope}.\nBrace } here.\n## nonsense\nlost\n## reminder\nOk {text}.\n"
    pools, problems = q.parse(text)
    assert pools["agent_finished"] == ["Good one."] and pools["reminder"] == ["Ok {text}."]
    assert problems == ["line 1: a quote before the first ## heading", "line 4: unknown placeholder {nope}",
                        "line 5: a stray brace", "line 6: unknown pool 'nonsense' (known: agent_finished, "
                        "agent_waiting, reminder, readback, catch_up)"]


def test_picker_cycles_without_repeats_then_reshuffles():
    quotes = [f"Q{i}." for i in range(5)]
    p = q.QuotePicker({}, random.Random(1))
    t = NOW
    first = []
    for _ in range(5):
        first.append(p.pick("reminder", quotes, {}, t))
        t += timedelta(minutes=2)
    assert sorted(first) == quotes  # every quote once before any repeats
    nxt = p.pick("reminder", quotes, {}, t)
    assert nxt in quotes and nxt != first[-1]  # a new cycle never opens with the last one


def test_picker_state_survives_a_restart():
    quotes = [f"Q{i}." for i in range(4)]
    state: dict = {}
    a = q.QuotePicker(state, random.Random(2))
    got = [a.pick("readback", quotes, {}, NOW), a.pick("readback", quotes, {}, NOW + timedelta(minutes=2))]
    b = q.QuotePicker(state, random.Random(99))  # same state, as loaded from voice.json
    got += [b.pick("readback", quotes, {}, NOW + timedelta(minutes=4)),
            b.pick("readback", quotes, {}, NOW + timedelta(minutes=6))]
    assert sorted(got) == quotes


def test_cooldown_skips_the_quip_within_a_minute():
    p = q.QuotePicker({}, random.Random(3))
    assert p.pick("reminder", ["A."], {}, NOW) == "A."
    assert p.pick("reminder", ["A."], {}, NOW + timedelta(seconds=59)) is None
    assert p.pick("reminder", ["A."], {}, NOW + timedelta(seconds=60)) == "A."


def test_placeholders_are_filled_or_the_quote_is_skipped():
    p = q.QuotePicker({}, random.Random(4))
    quotes = ["That's {project} sorted.", "Plain."]
    picks = {p.pick("agent_finished", quotes, {"project": None}, NOW + timedelta(minutes=2 * i)) for i in range(4)}
    assert picks == {"Plain."}
    assert p.pick("catch_up", ["Good {time_of_day}."], {}, NOW + timedelta(hours=1)) == "Good afternoon."
    assert p.pick("agent_finished", ["{project} done."], {"project": "Robot arm"},
                  NOW + timedelta(hours=2)) == "Robot arm done."


def test_a_changed_pool_is_reshuffled_and_a_corrupt_state_is_ignored():
    state = {"pools": {"reminder": {"key": "x", "order": [7, 7], "pos": 5}}, "last_quip": "garbage"}
    p = q.QuotePicker(state, random.Random(5))
    assert p.pick("reminder", ["A.", "B."], {}, NOW) in ("A.", "B.")
    assert sorted(state["pools"]["reminder"]["order"]) == [0, 1]


def test_empty_pool_gives_no_quip():
    assert q.QuotePicker({}).pick("readback", [], {}, NOW) is None


def test_time_of_day():
    assert [q.time_of_day(NOW.replace(hour=h)) for h in (6, 13, 19, 23, 2)] == \
        ["morning", "afternoon", "evening", "night", "night"]


def test_ensure_quotes_seeds_once_and_never_overwrites(tmp_path):
    assert q.ensure_quotes(tmp_path) is True
    path = tmp_path / "_voice" / "quotes.md"
    assert path.read_bytes().count(b"\r") == 0  # LF in shared space
    path.write_text("## readback\nMine.\n", encoding="utf-8")
    assert q.ensure_quotes(tmp_path) is False
    assert q.load_quotes(tmp_path)[0]["readback"] == ["Mine."]


def test_load_quotes_tolerates_a_missing_or_unreadable_file(tmp_path):
    pools, problems = q.load_quotes(tmp_path)
    assert problems == [] and all(v == [] for v in pools.values())
    (tmp_path / "_voice").mkdir()
    (tmp_path / "_voice" / "quotes.md").write_bytes(b"\xff\xfe\x00bad")
    pools, problems = q.load_quotes(tmp_path)
    assert all(v == [] for v in pools.values()) and "can't be read" in problems[0]


def test_voice_folder_is_protected_from_filing_and_not_a_note():
    assert protected_tops(["_voice/quotes.md", "notes/a.md"]) == ["_voice"]
    assert not is_note_path("_voice/quotes.md") and is_note_path("notes/a.md")
