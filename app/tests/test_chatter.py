import json
import random
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from atlas import chattiness as ch
from atlas import voice as v
from atlas.chatter import Chatter, farewell, greeting, settings_remark
from atlas.config import DEFAULT_SETTINGS
from atlas.presence import Presence

T0 = datetime(2026, 10, 26, 15, 0, 0)  # a Monday afternoon, outside the default quiet hours


class Now:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


class Sink:
    def __init__(self):
        self.lines, self.last_error = [], None

    def say(self, line):
        self.lines.append(line)
        return True

    def stop(self):
        pass

    def wait_quiet(self, timeout):
        self.waited = timeout
        return True


def item(id_, text="Pay rent", status="open", due=None, kind="task", project=None, created=None,
         remind_at=None, snooze_until=None):
    return SimpleNamespace(id=id_, text=text, status=status, due=due, kind=kind, project=project,
                           created=(created or T0 - timedelta(days=1)).isoformat(timespec="seconds"),
                           remind_at=remind_at, snooze_until=snooze_until)


@pytest.fixture
def env(tmp_path):
    brain = tmp_path / "brain"
    brain.mkdir()
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    settings["voice_sass"] = False
    box = SimpleNamespace(settings=settings, items=[], agents=[], now=Now(), sink=Sink(), brain=brain, lines=[],
                          state=tmp_path / "state" / "voice.json", entries=0, pending=0, usage=None, saved=[],
                          commit="c1", map=SimpleNamespace(projects=[{"slug": "atlas"}]), probe={"locked": False})

    def save(patch):
        box.saved.append(patch)
        box.settings.update(patch)
        return box.settings

    def make():
        presence = Presence(now=box.now, probe=lambda: dict(box.probe),
                            away_after=lambda: ch.level(box.settings).away_after)
        a = v.Announcer(settings=lambda: box.settings, brain=brain, state_path=box.state,
                        files=lambda s: {"model": "/m/x.onnx", "voices": "/m/v.bin"}, items=lambda: box.items,
                        speaker=box.sink, now=box.now, merge_s=0, rng=random.Random(7), agents=lambda: box.agents,
                        presence=presence, on_line=box.lines.append)
        c = Chatter(voice=a, settings=lambda: box.settings, brain=brain, items=lambda: box.items,
                    agents=lambda: box.agents, entries_today=lambda day: box.entries, unconfirmed=lambda: box.pending,
                    usage=lambda: box.usage, presence=presence, now=box.now, save_settings=save,
                    load_map=lambda: box.map, commit=lambda: box.commit, count_commits=lambda a, b: 3, settle_s=0)
        presence.seen("test")  # the owner is at the PC
        return a, c
    box.make = make
    box.a, box.c = make()
    return box


def spoken(env):
    return [ln.info for ln in env.sink.lines]


def said(env):
    return [x["text"] for x in env.lines]


def chat(env, n=1, **kw):
    out = []
    for i in range(n):
        out.append(env.a.chat("self", "self.musing", {}, f"Line {i}.", key=("t", i, str(kw)), **kw))
    return out


# -- the levels -------------------------------------------------------------------------------------
def test_the_level_table():
    assert [ch.TABLE[n].per_hour for n in ch.ORDER] == [0, 2, 4, 8, None]
    assert ch.level({"voice_chattiness": "medium"}).per_hour == 4
    assert ch.level({"voice_chattiness": "low", "voice_chatter_per_hour": 6}).per_hour == 6
    assert ch.level({"voice_chattiness": "debug", "voice_chatter_per_hour": 6}).per_hour is None
    assert ch.level({"voice_chattiness": "nonsense"}).name == "medium"


def test_medium_speaks_four_an_hour_then_only_captions(env):
    out = chat(env, 5)
    assert [o["spoken"] for o in out] == [True, True, True, True, False]
    assert out[4]["why"] == "over this hour's budget"
    assert said(env) == [f"Line {i}." for i in range(5)]  # every one is captioned
    env.now.t += timedelta(minutes=61)
    env.c.presence.seen()
    assert chat(env, 1, why="x")[0]["spoken"] is True


def test_mute_only_captions_and_force_skips_the_level(env):
    env.settings["voice_chattiness"] = "mute"
    assert chat(env)[0] == {"text": "Line 0.", "spoken": False, "origin": "fixed", "why": "chattiness is mute"}
    assert chat(env, 1, force=True)[0]["spoken"] is True


def test_unbudgeted_lines_ignore_the_cap(env):
    env.settings["voice_chattiness"] = "low"
    chat(env, 2)
    assert chat(env, 1, budgeted=False)[0]["spoken"] is True


def test_budgeted_lines_wait_for_the_owner_at_the_pc(env):
    env.now.t += timedelta(minutes=30)
    env.c.presence.poll()
    assert chat(env)[0]["why"] == "you're not at the PC"
    env.settings["voice_chattiness"] = "debug"
    assert chat(env, 1, why="d")[0]["spoken"] is True


def test_pause_and_the_voice_switch_win_even_over_force(env):
    env.a.pause(10)
    assert chat(env, 1, force=True)[0]["spoken"] is False
    env.a.resume()
    env.settings["voice_enabled"] = False
    assert chat(env, 1, force=True, why="z")[0]["spoken"] is False


def test_debug_ignores_quiet_hours(env):
    env.now.t = T0.replace(hour=23, minute=30)
    env.c.presence.seen()
    assert chat(env)[0]["spoken"] is False
    env.settings["voice_chattiness"] = "debug"
    assert chat(env, 1, why="q")[0]["spoken"] is True


def test_quips_one_in_three_at_low_and_every_line_at_debug(env, tmp_path):
    env.settings["voice_sass"] = True
    q = env.brain / "_voice" / "quotes.md"
    q.parent.mkdir()
    q.write_text("## self\nA.\nB.\nC.\n", encoding="utf-8")
    env.settings["voice_chattiness"] = "low"
    env.settings["voice_chatter_per_hour"] = 60
    out = [o["text"] for o in chat(env, 6)]
    with_quips = [t for t in out if t.count(".") == 2]
    assert len(with_quips) <= 2
    env.settings["voice_chattiness"] = "debug"
    out = [o["text"] for o in chat(env, 3, why="d")]
    assert [t.split(" ", 2)[-1] for t in out] == ["A.", "B.", "C."]  # every line, no cooldown, in file order


def test_learned_restraint_after_three_shhs(env):
    env.settings["voice_chatter_per_hour"] = 60
    for i in range(3):
        env.a.chat("tease", "tease", {}, "Tease.", key=("x", i))
        env.now.t += timedelta(seconds=10)
        hit = env.a.shh()
        env.now.t += timedelta(minutes=1)
    assert hit == "tease" and env.a.restrained("tease")
    outs = [env.a.chat("tease", "tease", {}, "Tease.", key=("y", i)) for i in range(4)]
    assert [o["spoken"] for o in outs] == [False, True, False, True]
    assert outs[0]["why"] == "learned restraint"
    env.a.reset_restraint()
    assert not env.a.restrained("tease")


def test_a_shh_needs_a_line_just_before(env):
    env.a.chat("tease", "tease", {}, "Tease.")
    env.now.t += timedelta(seconds=40)
    assert env.a.shh() is None and not env.a.chatter_state().get("shh")


def test_every_phase_four_line_is_captioned_too(env):
    env.a.reply("nothing")
    assert env.lines[-1]["event"] == "reply" and env.lines[-1]["text"] == "Nothing on that." and env.lines[-1]["spoken"]


# -- due today --------------------------------------------------------------------------------------
def test_due_today_waits_for_the_morning_digest_then_repeats_at_the_cadence(env):
    env.now.t = T0.replace(hour=9)
    env.c.presence.seen()
    env.items = [item("a", "Send the lab report", due=T0.date().isoformat())]
    env.c.tick()
    assert said(env)[0].startswith("Good morning.") and "Send the lab report" in said(env)[0]
    n = len(said(env))
    env.now.t += timedelta(minutes=30)
    env.c.presence.seen()
    env.c.tick()
    assert len(said(env)) == n  # the digest named it: the first repeat is a cadence later
    env.now.t += timedelta(hours=2, minutes=1)
    env.c.presence.seen()
    env.c.tick()
    assert said(env)[-1] == "Still due today. Send the lab report."
    env.items[0].status = "done"
    n = len(said(env))
    env.now.t += timedelta(hours=2, minutes=1)
    env.c.presence.seen()
    env.c.tick()
    assert not any("Still due" in x for x in said(env)[n:])  # done: no more


def test_due_today_approach_slots_fire_on_time_never_late(env):
    due = T0 + timedelta(hours=3)
    env.items = [item("a", "Networks exam", due=due.strftime("%Y-%m-%dT%H:%M"))]
    env.c.tick()  # the digest
    env.now.t = due - timedelta(hours=2)
    env.c.presence.seen()
    env.c.tick()
    assert said(env)[-1] == "Networks exam. That's due in two hours."
    env.now.t = due - timedelta(minutes=14)  # the hour slot passed while ATLAS was off: skipped, not late
    env.c.presence.seen()
    env.c.tick()
    assert said(env)[-1] == "Networks exam. That's due in fifteen minutes."
    assert not any("in an hour" in x for x in said(env))
    a2, c2 = env.make()  # a restart repeats none of them
    n = len(said(env))
    c2.tick()
    assert not any("fifteen" in x for x in said(env)[n:])


def test_the_evening_brings_the_digest_and_the_last_call(env):
    env.items = [item("a", "Order servos", due=T0.date().isoformat())]
    env.entries, env.pending = 2, 1
    env.c.tick()
    env.now.t = T0.replace(hour=18, minute=0, second=30)
    env.c.presence.seen()
    env.c.tick()
    tail = said(env)[-2:]
    assert tail[0].startswith("Evening.") and "two notes" in tail[0] and "one suggestions" not in tail[0]
    assert tail[1] == "Last call. Order servos. Still open today."


def test_mute_has_no_due_today_repeats(env):
    env.settings["voice_chattiness"] = "mute"
    env.items = [item("a", due=T0.date().isoformat())]
    for _ in range(3):
        env.c.tick()
        env.now.t += timedelta(hours=3)
    assert said(env) == []


# -- presence and briefings -------------------------------------------------------------------------
def test_welcome_back_names_what_happened(env):
    env.c.presence.seen()
    env.now.t += timedelta(minutes=21)
    env.c.presence.poll()
    env.c.agent_transition({"session_id": "s1", "before": "running", "after": "finished", "at": env.now.t.isoformat(),
                            "since": T0.isoformat(), "project_name": "RobotArm", "project": "arm"})
    env.agents = [{"session_id": "s2", "status": "waiting", "ended_at": None, "project_name": "ATLAS",
                   "status_since": env.now.t.isoformat()}]
    env.now.t += timedelta(minutes=5)
    env.c.presence.seen("app")
    assert said(env)[-1] == "Welcome back. Atlas is waiting on you. Robot Arm finished."


def test_welcome_back_with_nothing_to_say(env):
    env.now.t += timedelta(minutes=25)
    env.c.presence.poll()
    env.c.presence.seen()
    assert said(env)[-1] == "Welcome back. Nothing happened."
    env.settings["voice_chattiness"] = "low"
    env.now.t += timedelta(minutes=25)
    env.c.presence.poll()
    env.c.presence.seen()
    assert said(env)[-1] == "Welcome back. Nothing happened."  # low: no gimmick line


# -- nudges, teasing, usage --------------------------------------------------------------------------
def test_an_agent_left_waiting_is_nudged_once_per_stretch(env):
    env.c.tick()
    env.agents = [{"session_id": "s1", "status": "waiting", "ended_at": None, "project_name": "RobotArm",
                   "status_since": (env.now.t - timedelta(minutes=12)).isoformat()}]
    env.c.tick()
    env.c.tick()
    assert [x for x in said(env) if "waiting twelve minutes" in x] == ["Robot Arm has been waiting twelve minutes."]


def test_teasing_only_at_high(env):
    env.items = [item("a", "Fix my intent rules", project="atlas", created=T0 - timedelta(days=6)),
                 item("b", "Due today one", project="atlas", due=T0.date().isoformat())]
    env.c.tick()
    assert not any("intent" in x for x in said(env))
    env.settings["voice_chattiness"] = "high"
    env.now.t += timedelta(minutes=1)
    env.c.presence.seen()
    env.c.tick()
    assert "Fix my intent rules. That's been open six days." in said(env)
    env.now.t += timedelta(hours=1)
    env.c.presence.seen()
    env.c.tick()
    assert sum("intent" in x for x in said(env)) == 1  # once a day, and the due-today one is never teased


def test_usage_crossing_the_warning_once_per_window(env):
    env.usage = {"five_hour": {"used": 85, "resets_at": "2026-10-26T18:00"}}
    env.c.tick()
    env.c.tick()
    assert [x for x in said(env) if "percent" in x] == ["You're at eighty-five percent of your window."]


# -- self-awareness -------------------------------------------------------------------------------------
def test_atlas_sessions_and_notes_about_itself(env):
    tr = {"session_id": "s1", "before": "idle", "after": "running", "at": T0.isoformat(), "since": T0.isoformat(),
          "project_name": "ATLAS", "project": "atlas"}
    env.c.agent_transition(tr)
    env.c.agent_transition({**tr, "before": "finished"})
    assert [x for x in said(env) if "working on me" in x] == ["Atlas is working on me."]
    env.c.agent_transition({**tr, "before": "running", "after": "finished",
                            "at": (T0 + timedelta(minutes=40)).isoformat()})
    assert said(env)[-1] == "They finished working on me. It took forty minutes."
    env.c.on_filed(["projects/atlas/ideas.md"])
    assert said(env)[-1] == "A note about me. Filed it."
    env.c.on_filed(["projects/arm/notes.md"])
    assert said(env)[-1] == "A note about me. Filed it."


def test_the_atlas_project_comes_from_settings_the_map_or_the_slug(env):
    assert env.c.atlas_project() == "atlas"
    env.settings["voice_atlas_project"] = "my-assistant"
    assert env.c.atlas_project() == "my-assistant"
    env.settings["voice_atlas_project"] = ""
    env.map = SimpleNamespace(projects=[{"slug": "other"}])
    assert env.c.atlas_project() is None


def test_startup_says_it_was_updated_and_drops_out_of_debug(env):
    env.c.startup()
    assert said(env) == ["Hello."]  # the first start: a hello, no update to compare
    env.commit = "c2"
    env.settings["voice_chattiness"] = "debug"
    env.a.chatter_state()["debug"] = {"since": T0.isoformat(), "before": "low"}
    env.c.startup(flushed=2)
    assert env.saved == [{"voice_chattiness": "low"}]
    assert any(x.startswith("I've been updated. Three") or x == "I've been updated." for x in said(env))


def test_debug_drops_back_after_two_hours(env):
    before = dict(env.settings)
    env.settings["voice_chattiness"] = "debug"
    env.c.settings_changed(before, dict(env.settings))
    assert env.a.chatter_state()["debug"]["before"] == "medium"
    assert said(env)[-1] == "Very high. You asked for it."
    env.now.t += timedelta(hours=2, minutes=1)
    env.c.tick()
    assert env.saved[-1] == {"voice_chattiness": "medium"} and "debug" not in env.a.chatter_state()


def test_settings_remarks():
    base = json.loads(json.dumps(DEFAULT_SETTINGS))
    assert settings_remark(base, {**base, "voice_chattiness": "low"})[:2] == ("self.set.chattiness_down", {"level": "Low"})
    assert settings_remark(base, {**base, "voice_chattiness": "high"})[0] == "self.set.chattiness_up"
    assert settings_remark(base, {**base, "voice_chattiness": "mute"})[0] == "self.set.mute"
    assert settings_remark(base, {**base, "voice_sass": False})[0] == "self.set.sass_off"
    events = {**base["voice_events"], "reminder": False}
    assert settings_remark(base, {**base, "voice_events": events})[:2] == ("self.set.event_off", {"what": "reminders"})
    assert settings_remark(base, {**base, "voice_speed": 1.2})[0] == "self.set.speed_up"
    assert settings_remark(base, {**base, "quiet_hours": ["22:00", "07:00"]})[0] == "self.set.quiet_hours"
    assert settings_remark(base, {**base, "voice_enabled": False}) is None
    assert settings_remark(base, dict(base)) is None


def test_sass_off_is_said_plainly(env):
    env.settings["voice_sass"] = True
    q = env.brain / "_voice" / "quotes.md"
    q.parent.mkdir()
    q.write_text("## self\nA quip.\n", encoding="utf-8")
    before = dict(env.settings)
    env.settings["voice_sass"] = False
    env.c.settings_changed(before, dict(env.settings))
    assert said(env)[-1] == "Sass off."


def test_a_burst_of_settings_saves_is_one_line(env):
    env.c._settle_s = 0.05
    base = dict(env.settings)
    env.c.settings_changed(base, {**base, "voice_volume": 60})
    env.c.settings_changed({**base, "voice_volume": 60}, {**base, "voice_volume": 60, "voice_chattiness": "high"})
    import time
    time.sleep(0.3)
    assert said(env) == ["High. Noted."]


# -- reactions ------------------------------------------------------------------------------------------
def test_reactions_follow_the_level_and_a_minute_apart(env):
    assert env.c.react("confirm_all", 3)["text"] == "Noted."
    assert env.c.react("correction") is None  # within a minute
    env.now.t += timedelta(minutes=2)
    env.settings["voice_chattiness"] = "low"
    assert env.c.react("dismiss_all") is None  # low: mistakes only
    assert env.c.react("flip_ask") is not None
    env.settings["voice_chattiness"] = "mute"
    env.now.t += timedelta(minutes=2)
    assert env.c.react("correction") is None


def test_try_it_now(env):
    env.settings["voice_chattiness"] = "debug"
    for what in Chatter.TRY:
        env.c.try_now(what)
    assert len(said(env)) >= 5
    with pytest.raises(ValueError):
        env.c.try_now("nope")


def test_no_good_morning_in_the_afternoon_the_first_repeat_is_the_roll_call(env):
    env.items = [item("a", "Send the lab report", due=T0.date().isoformat())]
    env.c.tick()
    assert said(env) == ["Still due today. Send the lab report."]
    assert env.a.chatter_state()["digest"]["morning"] == "skipped"


def test_shushed_three_times_by_pausing_is_announced_once(env):
    env.settings["voice_chatter_per_hour"] = 60
    for i in range(3):
        env.a.resume()
        env.a.chat("tease", "tease", {}, "Tease.", key=("x", i))
        env.now.t += timedelta(seconds=5)
        env.a.pause(1)
        env.c.react("pause")
        env.now.t += timedelta(minutes=2)
    restraint = [x for x in env.lines if "to myself" in x["text"]]
    assert len(restraint) == 1 and restraint[0]["text"] == "Noted. I'll keep the teasing to myself."
    assert restraint[0]["spoken"] is False  # paused: captioned


def test_try_self_never_makes_up_a_percentage(env):
    env.settings["voice_chattiness"] = "debug"
    assert "percent" not in env.c.try_now("self")["text"]
    env.usage = {"five_hour": {"used": 42}}
    assert "forty-two percent" in env.c.try_now("self")["text"]


# -- hello and goodbye (owner, 2026-10-26) ---------------------------------------------------------------
class Coin:
    def __init__(self, heads):
        self.heads = heads

    def random(self):
        return 0.0 if self.heads else 0.99

    def choice(self, xs):
        return xs[0]


@pytest.mark.parametrize("hour,pool", [(6, "greeting.early"), (9, "greeting.morning"), (14, "greeting.afternoon"),
                                       (19, "greeting.evening"), (23, "greeting.late"), (2, "greeting.late")])
def test_a_hello_follows_the_time_of_day(hour, pool):
    tuesday = datetime(2026, 10, 27, hour, 0)
    assert greeting(tuesday, tuesday - timedelta(days=1), Coin(True))[0] == pool


def test_a_hello_notices_the_day_and_how_long_it_has_been():
    monday9, friday15, saturday11 = datetime(2026, 10, 26, 9), datetime(2026, 10, 30, 15), datetime(2026, 10, 31, 11)
    assert greeting(monday9, monday9 - timedelta(days=1), Coin(True))[0] == "greeting.monday"
    assert greeting(monday9, monday9 - timedelta(days=1), Coin(False))[0] == "greeting.morning"  # half the time
    assert greeting(friday15, friday15 - timedelta(days=1), Coin(True))[0] == "greeting.friday"
    assert greeting(saturday11, saturday11 - timedelta(days=1), Coin(True)) == ("greeting.weekend", {"day": "Saturday"})
    back = greeting(monday9, monday9 - timedelta(days=4), Coin(False))
    assert back == ("greeting.back_days", {"day": "Monday", "days": "four days"})  # always said
    assert greeting(friday15, friday15 - timedelta(hours=1), Coin(True))[0] == "greeting.again"


def test_a_goodbye_notices_a_short_or_long_run_and_the_weekend():
    t = datetime(2026, 10, 27, 20)
    assert farewell(t, t - timedelta(hours=2), Coin(True)) == ("goodbye.evening", {"day": "Tuesday"})
    assert farewell(t, t - timedelta(minutes=8), Coin(True)) == ("goodbye.short", {"duration": "eight minutes"})
    assert farewell(t, t - timedelta(hours=7), Coin(True))[0] == "goodbye.long"
    assert farewell(datetime(2026, 10, 30, 17), None, Coin(True))[0] == "goodbye.friday"
    assert farewell(datetime(2026, 10, 27, 6), None, Coin(True))[0] == "goodbye.morning"
    assert farewell(datetime(2026, 10, 27, 1), None, Coin(True))[0] == "goodbye.late"


def test_hello_on_a_real_start_never_after_a_restart(env):
    env.a.tick()  # this run is alive: last_tick saved
    a2, c2 = env.make()
    env.now.t += timedelta(minutes=1)
    assert c2.greet() is None  # a restart
    env.now.t += timedelta(hours=1)
    a3, c3 = env.make()
    said3 = c3.greet()
    assert said3["spoken"] and said3["why"] == "ATLAS started"


def test_hello_and_goodbye_ignore_quiet_hours_but_not_mute_or_pause(env):
    from atlas.lines import ensure_lines
    ensure_lines(env.brain)
    env.now.t = T0.replace(hour=23, minute=30)
    assert env.c.greet()["spoken"] is True
    said = env.c.goodbye()
    assert said["spoken"] is True and env.sink.waited == 7.0
    env.settings["voice_chattiness"] = "mute"
    assert env.c.goodbye()["spoken"] is False
    env.settings["voice_chattiness"] = "medium"
    env.a.pause(10)
    assert env.c.goodbye()["spoken"] is False


def test_the_seeded_hellos_follow_the_wording_rules():
    from atlas.lines import POOLS, SEED, parse
    pools, _, problems = parse(SEED)
    hello = [p for p in POOLS if p.startswith(("greeting.", "goodbye."))]
    assert problems == [] and len(hello) == 18 and all(len(pools[p]) >= 3 for p in hello)
