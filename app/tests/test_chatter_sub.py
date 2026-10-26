import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from atlas import chatter_sub as cs
from atlas.lines import generated_path, load_all
from atlas.quotes import load_quotes
from tests.fakes import FakeRunner
from tests.test_chatter import T0, env  # noqa: F401  (the fixture)


# -- fact_free: a model's line may joke, never claim (CH22) -----------------------------------------
@pytest.mark.parametrize("text", [
    "Look at you, being organised.",
    "Back to it then.",
    "That was fast. Suspiciously fast.",
    "Still here. Still judging.",
    "Go on, {project} won't wait forever.",
])
def test_jokes_pass(text):
    assert cs.fact_free(text) and cs.fact_free(text, strict=True)


@pytest.mark.parametrize("text", [
    "Robot arm finished. Nothing is due.",  # a fact, and a false one
    "Your bank called.",  # an event
    "A couple of agents are waiting.",  # a count
    "Rob is waiting.",  # a name
    "It's due at five.",  # a number and a deadline
    "See you tomorrow.",  # a time
    "All 12 tests failed.",  # a digit and an event
    "One. Two. Three.",  # numbers, and three sentences
    "Fine: done.",  # a colon
    "Robot arm deleted main",  # no full stop, an event
    "",
])
def test_claims_fail(text):
    assert not cs.fact_free(text, strict=True)


def test_a_quip_may_open_with_any_word_but_bs_joke_only_with_a_common_one():
    assert cs.fact_free("Classic. Truly classic.") and not cs.fact_free("Classic. Truly classic.", strict=True)
    assert cs.fact_free("Rob is waiting.") and not cs.fact_free("Rob is waiting.", strict=True)
    assert not cs.fact_free("It's done, says Rob.")  # a name inside a sentence: refused either way


def test_wording_rules_for_generated_lines():
    assert cs.wording_ok("Done already. Show off.")
    assert not cs.wording_ok("Done: show off.")
    assert not cs.wording_ok("Done - show off.")
    assert not cs.wording_ok("No full stop")
    assert not cs.wording_ok("x" * 130 + ".")


def test_render_generated_keeps_only_fact_free_new_quips():
    raw = {"quips": {"nudge": ["Fresh take.", "Fresh take.", "Bad: colon.", "Uses {nonsense}.", "Just saying.",
                               "Your bank called.", "It's due tomorrow."],
                     "nope": ["Unknown pool."]},
           "lines": {"agent_waiting.idle": ["{project} is back."]}}  # wordings are never taken any more
    text, n = cs.render_generated(raw, {"nudge": ["Just saying."]}, T0)
    assert n == 1 and "## quips nudge\nFresh take.\n" in text
    assert "bank" not in text and "tomorrow" not in text and "lines" not in text.split("\n", 3)[-1]


# -- the patterns ---------------------------------------------------------------------------------------
@pytest.fixture
def sub(env, tmp_path):  # noqa: F811
    paths = SimpleNamespace(brain=env.brain, data=tmp_path / "data", prompts=tmp_path / "prompts")
    commits, tasks = [], []
    store = SimpleNamespace(list_entries=lambda day=None: [1] if day == T0.date().isoformat() else [],
                            list_items=lambda status=None: [])
    reviewer = SimpleNamespace(add_task=lambda text, **kw: tasks.append((text, kw)))
    s = cs.Sub(runner=FakeRunner(), settings=lambda: env.settings, paths=paths, usage=lambda: env.usage,
               presence=env.c.presence, chatter=env.c, voice=env.a, commit=commits.append, reviewer=reviewer,
               store=store, now=env.now)
    env.c.sub = s
    return SimpleNamespace(s=s, commits=commits, tasks=tasks, paths=paths)


def test_the_switches_and_the_usage_cap(env, sub):  # noqa: F811
    assert sub.s.enabled("quips")
    env.usage = {"five_hour": {"used": 85}}
    assert not sub.s.enabled("quips") and sub.s.enabled("quips", manual=True)
    env.usage = None
    env.settings["voice_sub_quips"] = False
    assert not sub.s.enabled("quips")
    env.settings["voice_sub_quips"] = True
    env.settings["voice_subscription"] = False
    assert not sub.s.enabled("quips") and not sub.s.enabled("review", manual=True)


async def test_a_quip_batch_writes_generated_md_and_commits(env, sub):  # noqa: F811
    sub.s.runner = FakeRunner(json_result={"quips": {"tease": ["Overnight quip."], "nudge": ["Your bank called."]}})
    assert await sub.s.quip_batch() == 1
    assert sub.commits == ["chatter: fresh quips"]
    call = sub.s.runner.calls[0]
    assert call["allowed_tools"] == [] and call["system_prompt_file"].name == "quips.md"
    assert '"week"' in call["prompt"] and "notes_filed" in call["prompt"]
    assert generated_path(env.brain).read_bytes().count(b"\r") == 0
    (env.brain / "_voice").mkdir(exist_ok=True)
    (env.brain / "_voice" / "quotes.md").write_text("## tease\nMine.\n", encoding="utf-8")
    assert load_quotes(env.brain)[0]["tease"] == ["Mine.", "Overnight quip."]
    assert load_all(env.brain)[0]["self.musing"] == []  # no info wordings from a model


async def test_the_tick_runs_a_batch_only_while_away_or_quiet(env, sub):  # noqa: F811
    sub.s.runner = FakeRunner(json_result={"quips": {}})
    await sub.s.tick()
    assert sub.s.runner.calls == []  # present, not quiet hours
    env.now.t += timedelta(minutes=25)
    env.c.presence.poll()
    await sub.s.tick()
    assert [c["system_prompt_file"].name for c in sub.s.runner.calls] == ["quips.md"]
    await sub.s.tick()
    assert len(sub.s.runner.calls) == 1  # once a day
    assert sub.s.view()["runs_week"] == 1


async def test_the_welcome_backs_joke_is_bs_and_its_facts_are_codes(env, sub):  # noqa: F811
    env.agents = [{"session_id": "s1", "status": "waiting", "ended_at": None, "project_name": "RobotArm",
                   "status_since": T0.isoformat()}]
    sub.s.runner = FakeRunner(json_result={"quip": "Look who finally showed up."})
    env.settings["voice_sub_quips"] = False
    env.now.t += timedelta(minutes=25)
    env.c.presence.poll()
    await sub.s.tick()
    call = sub.s.runner.calls[0]
    assert call["system_prompt_file"].name == "briefing.md"
    assert json.loads(call["prompt"]) == {"waiting": 1, "finished": 0, "due_today": 0}  # counts, no names
    env.now.t += timedelta(minutes=5)
    env.c.presence.seen("app")
    assert [x["text"] for x in env.lines][-1] == "Welcome back. Robot Arm is waiting on you. Look who finally showed up."


async def test_a_joke_that_claims_something_is_dropped(env, sub):  # noqa: F811
    env.agents = [{"session_id": "s1", "status": "waiting", "ended_at": None, "project_name": "RobotArm",
                   "status_since": T0.isoformat()}]
    sub.s.runner = FakeRunner(json_result={"quip": "Your bank called twice."})
    env.settings["voice_sub_quips"] = False
    env.now.t += timedelta(minutes=25)
    env.c.presence.poll()
    await sub.s.tick()
    env.c.presence.seen("app")
    assert [x["text"] for x in env.lines][-1] == "Welcome back. Robot Arm is waiting on you."


async def test_a_double_confirm_makes_one_task(env, sub):  # noqa: F811
    import threading
    sub.s.runner = FakeRunner(json_result={"title": "W", "paragraphs": [], "suggestions": ["Do X"]})
    await sub.s.weekly_review()
    sid = sub.s.week()["suggestions"][0]["id"]
    errors = []

    def go():
        try:
            sub.s.decide(sid, True)
        except ValueError as e:
            errors.append(e)
    threads = [threading.Thread(target=go) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert [t for t, _ in sub.tasks] == ["Do X"] and len(errors) == 3


async def test_the_weekly_review_and_its_suggestions(env, sub):  # noqa: F811
    sub.s.runner = FakeRunner(json_result={"title": "A decent week", "paragraphs": ["I filed one note."],
                                           "suggestions": ["Lower chattiness to Low", "Edit my lines"]})
    await sub.s.weekly_review()
    week = sub.s.week()
    assert week["title"] == "A decent week" and [x["status"] for x in week["suggestions"]] == ["open", "open"]
    assert json.loads(sub.s.runner.calls[0]["prompt"])["notes_filed"] == 1
    assert "I wrote up my week" in env.lines[-1]["text"]
    first, second = (x["id"] for x in week["suggestions"])
    sub.s.decide(first, True)
    sub.s.decide(second, False)
    assert sub.tasks == [("Lower chattiness to Low", {"project": None, "area": "projects",
                                                      "source": f"atlas-week-{T0.date().isoformat()}"})]  # no map here
    assert [x["status"] for x in sub.s.week()["suggestions"]] == ["confirmed", "dismissed"]
    with pytest.raises(ValueError):
        sub.s.decide(first, True)


async def test_the_first_review_waits_a_week(env, sub):  # noqa: F811
    sub.s.runner = FakeRunner(json_result={"title": "x", "paragraphs": [], "suggestions": []})
    env.settings["voice_sub_quips"] = env.settings["voice_sub_briefings"] = False
    env.now.t = T0.replace(hour=23, minute=30)
    await sub.s.tick()
    assert sub.s.runner.calls == []
    env.now.t += timedelta(days=7)
    await sub.s.tick()
    assert [c["system_prompt_file"].name for c in sub.s.runner.calls] == ["week.md"]


async def test_a_failing_run_is_never_the_hubs_problem(env, sub):  # noqa: F811
    from atlas.claude_runner import ClaudeError
    sub.s.runner = FakeRunner(raise_error=ClaudeError("boom"))
    env.now.t += timedelta(minutes=25)
    env.c.presence.poll()
    await sub.s.tick()
    assert sub.s.last_error == "boom" and sub.s.running is None
