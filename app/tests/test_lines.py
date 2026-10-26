import json
import random
from datetime import timedelta
from types import SimpleNamespace

import pytest

from atlas import lines as ln
from atlas import voice as v
from atlas.config import DEFAULT_SETTINGS
from tests.test_voice import T0, Now, Sink, _tr, item


# -- the file ---------------------------------------------------------------------------------------
def test_the_seed_parses_cleanly_and_fills_every_pool():
    pools, nums, problems = ln.parse(ln.SEED)
    assert problems == []
    assert all(pools[p] for p in ln.POOLS), [p for p in ln.POOLS if not pools[p]]
    assert all(len(pools[p]) == len(nums[p]) for p in ln.POOLS)


def test_the_seed_follows_the_wording_rules():
    pools, _, _ = ln.parse(ln.SEED)
    for wordings in pools.values():
        for w in wordings:
            body, _ = ln.unfuse(w)
            assert ":" not in body and " - " not in body and "—" not in body, w
            assert body.rstrip().endswith((".", "?", "}")), w


def test_a_wording_must_carry_its_facts_and_no_others():
    text = "## agent_finished\n{project} is done.\n{project} took {duration}.\n{project} took {duration} for {text}.\n"
    pools, nums, problems = ln.parse(text)
    assert pools["agent_finished"] == ["{project} took {duration}."]
    assert nums["agent_finished"] == [3]
    assert problems == ["line 2: agent_finished must say {duration}",
                        "line 4: {text} doesn't belong in agent_finished"]


def test_optional_placeholders_unknown_pools_and_stray_braces():
    text = ("stray before\n## agent_waiting.idle\n{project} done.\n{project} after {duration}.\n"
            "{project} {time_of_day}.\n{project} }\n## nope\nignored\n")
    pools, _, problems = ln.parse(text)
    assert pools["agent_waiting.idle"] == ["{project} done.", "{project} after {duration}.", "{project} {time_of_day}."]
    assert problems == ["line 1: a wording before the first ## heading", "line 6: a stray brace",
                        "line 7: unknown pool 'nope'"]


def test_fused_wordings():
    assert ln.unfuse("~ It's done. Shocking.") == ("It's done. Shocking.", True)
    assert ln.unfuse("It's done.") == ("It's done.", False)
    pools, _, problems = ln.parse("## agent_waiting\n~ {project} again. Shocking.\n")
    assert pools["agent_waiting"] == ["~ {project} again. Shocking."] and problems == []


def test_ensure_lines_seeds_once_and_never_overwrites(tmp_path):
    assert ln.ensure_lines(tmp_path) is True
    path = ln.lines_path(tmp_path)
    assert path.read_bytes() == ln.SEED.encode()
    path.write_text("## agent_waiting\nmine {project}.\n", encoding="utf-8")
    assert ln.ensure_lines(tmp_path) is False
    assert ln.load_lines(tmp_path)[0]["agent_waiting"] == ["mine {project}."]


def test_a_missing_or_unreadable_file_is_empty_never_fatal(tmp_path):
    pools, nums, problems = ln.load_lines(tmp_path)
    assert all(p == [] for p in pools.values()) and problems == []
    ln.lines_path(tmp_path).parent.mkdir()
    ln.lines_path(tmp_path).write_bytes(b"\xff\xfe\x00bad")
    _, _, problems = ln.load_lines(tmp_path)
    assert problems and "can't be read" in problems[0]


# -- the announcer ----------------------------------------------------------------------------------
@pytest.fixture
def env(tmp_path):
    brain = tmp_path / "brain"
    brain.mkdir()
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    settings["voice_sass"] = False
    box = SimpleNamespace(settings=settings, items=[], agents=[], now=Now(), sink=Sink(), brain=brain,
                          state=tmp_path / "state" / "voice.json")

    def make():
        return v.Announcer(settings=lambda: box.settings, brain=brain, state_path=box.state,
                           files=lambda s: {"model": "/m/x.onnx", "voices": "/m/v.bin"},
                           items=lambda: box.items, speaker=box.sink, now=box.now, merge_s=0,
                           rng=random.Random(7), agents=lambda: box.agents)
    box.make = make
    box.a = make()
    return box


def write(env, text):
    path = ln.lines_path(env.brain)
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")


def info(env):
    return env.sink.lines[-1].info


def test_each_kind_of_waiting_has_its_own_wording(env):
    write(env, "## agent_waiting.idle\n{project} is done. Your move.\n## agent_waiting.permission\n"
               "{project} wants permission.\n## agent_waiting.question\n{project} has a question.\n"
               "## agent_waiting.error\n{project} fell over.\n## agent_waiting\n{project} needs you.\n")
    for i, (kind, said) in enumerate([("idle_prompt", "Robot arm is done. Your move."),
                                      ("permission_prompt", "Robot arm wants permission."),
                                      ("elicitation_dialog", "Robot arm has a question."),
                                      ("stop_failure", "Robot arm fell over."),
                                      ("something_new", "Robot arm needs you.")]):
        env.a.agent_transition(_tr(f"s{i}", "running", "waiting", T0 + timedelta(seconds=i), kind=kind))
        assert info(env) == said


def test_no_repeat_until_the_pool_cycles(env):
    write(env, "## agent_waiting.permission\nA {project}.\nB {project}.\nC {project}.\n")
    said = []
    for i in range(6):
        env.now.t = T0 + timedelta(minutes=20 * i)
        env.a.agent_transition(_tr("s1", "running", "waiting", env.now.t, kind="permission_prompt"))
        said.append(info(env)[0])
    assert sorted(said[:3]) == ["A", "B", "C"] and sorted(said[3:]) == ["A", "B", "C"]


def test_the_short_form_is_for_the_same_kind_again(env):
    write(env, "## agent_waiting.idle\n{project} is done.\n## agent_waiting.permission\n{project} wants permission.\n"
               "## agent_waiting.again\n{project} again.\n")
    env.a.agent_transition(_tr("s9", "running", "waiting", T0, kind="idle_prompt"))
    env.a.agent_transition(_tr("s9", "running", "waiting", T0 + timedelta(minutes=2), kind="permission_prompt"))
    assert info(env) == "Robot arm wants permission."  # another kind: it says what it wants


def test_the_same_session_again_soon_gets_the_short_form(env):
    write(env, "## agent_waiting.permission\n{project} wants permission.\n## agent_waiting.again\n{project} again.\n")
    env.a.agent_transition(_tr("s1", "running", "waiting", T0, kind="permission_prompt"))
    t = T0 + timedelta(minutes=4)
    env.a.agent_transition(_tr("s1", "running", "waiting", t, kind="permission_prompt"))
    assert info(env) == "Robot arm again."
    t += timedelta(minutes=11)
    env.a.agent_transition(_tr("s1", "running", "waiting", t, kind="permission_prompt"))
    assert info(env) == "Robot arm wants permission."
    env.a.agent_transition(_tr("s2", "running", "waiting", t, kind="permission_prompt"))  # another session
    assert info(env) == "Robot arm wants permission."


def test_a_finished_turn_says_how_long_it_ran(env):
    write(env, "## agent_waiting.idle\n{project} finished after {duration}.\n{project} is done.\n")
    env.a.agent_transition(_tr("s1", "idle", "running", T0))
    env.a.agent_transition(_tr("s1", "running", "waiting", T0 + timedelta(minutes=12), kind="idle_prompt"))
    assert info(env) in ("Robot arm finished after twelve minutes.", "Robot arm is done.")
    env.a.agent_transition(_tr("s2", "running", "waiting", T0, kind="idle_prompt"))  # no turn seen: no duration
    assert info(env) == "Robot arm is done."


def test_others_waiting_are_counted_from_the_agents(env):
    write(env, "## agent_waiting.permission\n{project} wants permission.\n## others_waiting\nThat makes {count} waiting.\n")
    stamp = T0.isoformat()
    env.agents = [{"session_id": "s1", "status": "waiting", "ended_at": None, "last_event_at": stamp},
                  {"session_id": "s2", "status": "waiting", "ended_at": None, "last_event_at": stamp},
                  {"session_id": "s3", "status": "waiting", "ended_at": stamp, "last_event_at": stamp},
                  {"session_id": "s4", "status": "running", "ended_at": None, "last_event_at": stamp},
                  {"session_id": "s5", "status": "waiting", "ended_at": None,
                   "last_event_at": (T0 - timedelta(days=2)).isoformat()}]
    env.a.agent_transition(_tr("s1", "running", "waiting", T0, kind="permission_prompt"))
    assert info(env) == "Robot arm wants permission. That makes two waiting."
    env.agents = env.agents[:1]
    env.a.agent_transition(_tr("s1", "running", "waiting", T0 + timedelta(minutes=30), kind="permission_prompt"))
    assert info(env) == "Robot arm wants permission."


def test_a_fused_wording_gets_no_quip(env):
    env.settings["voice_sass"] = True
    write(env, "## agent_finished\n~ {project} took {duration}. Shocking.\n")
    q = env.brain / "_voice" / "quotes.md"
    q.write_text("## agent_finished\nA quip.\n", encoding="utf-8")
    env.a.agent_transition(_tr("s1", "idle", "running", T0))
    env.a.agent_transition(_tr("s1", "running", "finished", T0 + timedelta(minutes=5)))
    assert env.sink.said[-1] == ["Robot arm took five minutes. Shocking."]
    write(env, "## agent_finished\n{project} is done after {duration}.\n")
    env.a.agent_transition(_tr("s2", "idle", "running", T0))
    env.a.agent_transition(_tr("s2", "running", "finished", T0 + timedelta(minutes=5)))
    assert env.sink.said[-1] == ["Robot arm is done after five minutes.", "A quip."]


def test_a_broken_file_never_silences_an_event(env):
    write(env, "## agent_waiting.permission\nno project here.\n")
    env.a.agent_transition(_tr("s1", "running", "waiting", T0, kind="permission_prompt"))
    assert info(env) == "Robot arm needs you. It's asking for permission."
    assert env.sink.lines[-1].meta["origin"] == "fixed"
    assert any("must say {project}" in p for p in env.a.view()["quote_problems"])


def test_the_origin_names_the_line(env):
    write(env, "# comment\n## agent_waiting.permission\n{project} wants permission.\n")
    env.a.agent_transition(_tr("s1", "running", "waiting", T0, kind="permission_prompt"))
    assert env.sink.lines[-1].meta["origin"] == "_voice/lines.md:3"


def test_reminders_readbacks_and_replies_use_the_wordings(env):
    write(env, "## reminder.due\nHeads up. {text} Due {when}.\n## readback.note\nGot it. {text}\n"
               "## reply.tasks\nI found {tasks}.\n## reply.more\nPlus {things}.\n## reply.found\n{entries} on that.\n")
    env.a.tick()
    env.items = [item("a", text="pay rent", due=(T0 + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M"))]
    env.now.t = T0 + timedelta(minutes=1)
    env.a.tick()
    env.now.t = T0 + timedelta(minutes=1, seconds=30)
    env.a.tick()
    assert info(env) == "Heads up. pay rent. Due today at 3 oh 1 pm."
    env.a.readback_note("ordered servos")
    assert info(env) == "Got it. ordered servos."
    assert env.a.reply("tasks", 2, 1) == "I found two possible tasks. Plus one more thing."
    assert env.a.reply("found", 3) == "Three entries on that."
    assert env.a.reply("nothing") == "Nothing on that."  # an empty pool: the fixed line


def test_merged_lines_use_the_many_pool(env):
    write(env, "## agent_finished.many\n{count} of them are done. {projects}.\n")
    a = env.a
    a._merge_s = 0.05
    for sid, proj in (("s1", "Robot arm"), ("s2", "ATLAS")):
        a.agent_transition(_tr(sid, "idle", "running", T0, project=proj))
        a.agent_transition(_tr(sid, "running", "finished", T0 + timedelta(minutes=5), project=proj))
    a.flush("agent_finished")
    assert info(env) == "Two of them are done. Robot arm and Atlas."


def test_debug_walks_the_pool_in_file_order(env):
    env.settings["voice_chattiness"] = "debug"
    write(env, "## agent_waiting.permission\nA {project}.\nB {project}.\nC {project}.\n")
    said = []
    for i in range(4):
        env.now.t = T0 + timedelta(minutes=20 * i)
        env.a.agent_transition(_tr("s1", "running", "waiting", env.now.t, kind="permission_prompt"))
        said.append(info(env)[0])
    assert said == ["A", "B", "C", "A"]


def test_new_pools_reach_an_older_file_once_and_a_deleted_one_stays_deleted(tmp_path):
    path = ln.lines_path(tmp_path)
    path.parent.mkdir()
    path.write_text("## agent_waiting\nMine {project}.\n", encoding="utf-8")
    added = ln.ensure_line_pools(tmp_path)
    assert "request" in added and "agent_waiting" not in added
    pools, _, problems = ln.load_lines(tmp_path)
    assert problems == [] and pools["agent_waiting"] == ["Mine {project}."] and pools["request"]
    assert ln.ensure_line_pools(tmp_path) == []
    text = path.read_text(encoding="utf-8")
    start = text.index("## request\n")
    path.write_text(text[:start] + text[text.index("\n\n", start) + 2:], encoding="utf-8")  # the owner deletes it
    assert ln.ensure_line_pools(tmp_path) == [] and "## request\n" not in path.read_text(encoding="utf-8")
    assert path.read_bytes().count(b"\r") == 0


def test_every_pool_has_a_seed_section():
    sections = ln.seed_sections()
    assert set(ln.POOLS) <= set(sections)
    for name, block in sections.items():
        assert f"## {name}" in block and ln.parse(block)[2] == []
