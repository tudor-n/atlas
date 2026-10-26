import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from atlas import chatter_sub as cs
from atlas.lines import generated_path, load_all
from atlas.quotes import load_quotes
from tests.fakes import FakeRunner
from tests.test_chatter import T0, env  # noqa: F401  (the fixture)


# -- the honesty check (V16 amended) ----------------------------------------------------------------
SHEET = {"waiting": ["Robot Arm"], "finished": ["Atlas", "Trace Green"], "due_today": ["Send the lab report."],
         "due_count": 1}


@pytest.mark.parametrize("text", [
    "Robot Arm is waiting on you. Atlas and Trace Green both finished.",
    "Two agents finished while you were out. Robot Arm wants you.",
    "One thing due today. Send the lab report.",
])
def test_honest_briefings_pass(text):
    assert cs.honest(text, SHEET)


@pytest.mark.parametrize("text", [
    "Three agents finished.",  # a number that isn't in the sheet
    "Robot Arm has 2 questions.",  # a digit
    "Dummy finished. Robot Arm waits.",  # a name that isn't in the sheet
    "Robot Arm: waiting.",  # a colon
    "One. Two. Three. Four.",  # four sentences
    "Robot Arm is waiting {project}.",
    "",
])
def test_dishonest_briefings_fail(text):
    assert not cs.honest(text, SHEET)


def test_wording_rules_for_generated_lines():
    assert cs.wording_ok("Done already. Show off.")
    assert cs.wording_ok("~ {project} is done. Shocking.")
    assert not cs.wording_ok("Done: show off.")
    assert not cs.wording_ok("Done - show off.")
    assert not cs.wording_ok("No full stop")
    assert not cs.wording_ok("x" * 130 + ".")


def test_render_generated_keeps_only_good_new_lines():
    raw = {"quips": {"nudge": ["Fresh one.", "Fresh one.", "Bad: colon.", "Uses {nonsense}.", "Just saying."],
                     "nope": ["Unknown pool."]},
           "lines": {"agent_waiting.idle": ["{project} is back.", "No project here."]}}
    text, n = cs.render_generated(raw, {"nudge": ["Just saying."]}, {"agent_waiting.idle": []}, T0)
    assert n == 2
    assert "## quips nudge\nFresh one.\n" in text and "## lines agent_waiting.idle\n{project} is back.\n" in text
    assert "colon" not in text and "nonsense" not in text and "No project" not in text and "Unknown" not in text


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
    sub.s.runner = FakeRunner(json_result={"quips": {"tease": ["Overnight quip."]},
                                           "lines": {"self.musing": ["Overnight musing."]}})
    assert await sub.s.quip_batch() == 2
    assert sub.commits == ["chatter: fresh quips"]
    call = sub.s.runner.calls[0]
    assert call["allowed_tools"] == [] and call["system_prompt_file"].name == "quips.md"
    assert '"week"' in call["prompt"] and "notes_filed" in call["prompt"]
    assert generated_path(env.brain).read_bytes().count(b"\r") == 0
    assert "Overnight quip." in load_quotes(env.brain)[0]["tease"] if (env.brain / "_voice" / "quotes.md").exists() \
        else True
    pools, nums, _ = load_all(env.brain)
    assert pools["self.musing"] == ["Overnight musing."] and nums["self.musing"][0].startswith("generated.md:")
    env.settings["voice_chattiness"] = "debug"
    said = env.a.chat("musing", "self.musing", {}, "fixed", force=True)
    assert said["text"] == "Overnight musing." and said["origin"].startswith("generated.md:")


async def test_the_tick_runs_a_batch_only_while_away_or_quiet(env, sub):  # noqa: F811
    sub.s.runner = FakeRunner(json_result={"quips": {}, "lines": {}})
    await sub.s.tick()
    assert sub.s.runner.calls == []  # present, not quiet hours
    env.now.t += timedelta(minutes=25)
    env.c.presence.poll()
    await sub.s.tick()
    assert [c["system_prompt_file"].name for c in sub.s.runner.calls] == ["quips.md"]
    await sub.s.tick()
    assert len(sub.s.runner.calls) == 1  # once a day
    assert sub.s.view()["runs_week"] == 1


async def test_a_worded_briefing_is_spoken_only_while_it_still_fits(env, sub):  # noqa: F811
    env.agents = [{"session_id": "s1", "status": "waiting", "ended_at": None, "project_name": "RobotArm",
                   "status_since": T0.isoformat()}]
    sub.s.runner = FakeRunner(json_result={"text": "Robot Arm is still waiting on you. Shocking."})
    env.settings["voice_sub_quips"] = False
    env.now.t += timedelta(minutes=25)
    env.c.presence.poll()
    await sub.s.tick()
    assert sub.s.runner.calls[0]["system_prompt_file"].name == "briefing.md"
    assert json.loads(sub.s.runner.calls[0]["prompt"])["waiting"] == ["Robot Arm"]
    env.now.t += timedelta(minutes=5)
    env.c.presence.seen("app")
    assert [x["text"] for x in env.lines][-1] == "Welcome back. Robot Arm is still waiting on you. Shocking."


async def test_a_briefing_that_fails_the_check_falls_back_to_the_template(env, sub):  # noqa: F811
    env.agents = [{"session_id": "s1", "status": "waiting", "ended_at": None, "project_name": "RobotArm",
                   "status_since": T0.isoformat()}]
    sub.s.runner = FakeRunner(json_result={"text": "Robot Arm and Dummy are waiting."})
    env.settings["voice_sub_quips"] = False
    env.now.t += timedelta(minutes=25)
    env.c.presence.poll()
    await sub.s.tick()
    env.c.presence.seen("app")
    assert [x["text"] for x in env.lines][-1] == "Welcome back. Robot Arm is waiting on you."


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
    assert sub.tasks == [("Lower chattiness to Low", {"project": "atlas", "area": "projects",
                                                      "source": f"atlas-week-{T0.date().isoformat()}"})]
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
