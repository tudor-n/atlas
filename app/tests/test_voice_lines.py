from datetime import date, datetime

import pytest

from atlas import voice_lines as v


@pytest.mark.parametrize("n,words", [(0, "zero"), (7, "seven"), (19, "nineteen"), (20, "twenty"),
                                     (42, "forty-two"), (100, "one hundred"), (412, "four hundred and twelve"),
                                     (1000, "one thousand"), (1005, "one thousand and five"),
                                     (2310, "two thousand three hundred and ten"), (1_000_000, "1000000"),
                                     (-3, "minus three")])
def test_number_words(n, words):
    assert v.number_words(n) == words


@pytest.mark.parametrize("s,words", [(5, "less than a minute"), (60, "a minute"), (119, "a minute"),
                                     (42 * 60, "forty-two minutes"), (3600, "an hour"),
                                     (70 * 60, "an hour and ten minutes"), (121 * 60, "two hours and a minute"),
                                     (3 * 3600, "three hours")])
def test_duration_words(s, words):
    assert v.duration_words(s) == words


@pytest.mark.parametrize("name,spoken", [("TraceGreen", "Trace Green"), ("trace-green", "trace green"),
                                         ("6-dof-arm", "6 dof arm"), ("Robot arm", "Robot arm"),
                                         ("RobotArmControl", "Robot Arm Control"), ("HTTPServer", "HTTP Server"),
                                         ("", "An agent"), (None, "An agent"),
                                         ("ATLAS", "Atlas"), ("ATLAS-desktop", "Atlas desktop"),
                                         ("CNC", "CNC"), ("<script>", "script"),
                                         ("x" * 60, "x" * 40)])
def test_spoken_project(name, spoken):
    assert v.spoken_project(name) == spoken


def test_agent_lines():
    assert v.agent_waiting_line("Robot arm", "idle_prompt") == "Robot arm needs you."
    assert v.agent_waiting_line("Robot arm", "permission_prompt") == "Robot arm needs you. It's asking for permission."
    assert v.agent_finished_line("Robot arm", 42 * 60) == "Robot arm has finished. It took forty-two minutes."


@pytest.mark.parametrize("value,spoken", [("412 mm", "412 millimetres"), ("412mm", "412 millimetres"),
                                          ("1 mm", "1 millimetre"), ("3.5 cm", "3.5 centimetres"),
                                          ("90 °", "90 degrees"), ("12 Ω", "12 ohms"), ("5 furlongs", "5 furlongs"),
                                          ("about 4 mm", "about 4 mm"), ("  7   V ", "7 volts")])
def test_unit_words(value, spoken):
    assert v.unit_words(value) == spoken


def test_readback_answer_reads_only_the_owners_values():
    # the item labels come from an agent (atlas-request): never spoken
    assert v.readback_answer({0: "412 mm"}) == "412 millimetres. Saved."
    assert v.readback_answer({0: "412 mm", 2: "74 cm"}) == "First, 412 millimetres. Third, 74 centimetres. Saved."
    assert v.readback_answer({11: "1 mm", 12: "2 mm"}).startswith("Item twelve, 1 millimetre. Item thirteen,")


def test_readback_note_and_cap():
    assert v.readback_note("buy  m3 screws") == "Note saved. buy m3 screws."
    long = v.readback_note("word " * 100)
    assert len(long) <= v.CAP + 12 and long.endswith("… and more.")


def test_reminder_line_with_and_without_due():
    now = datetime(2026, 10, 23, 9, 0)  # a Friday
    assert v.reminder_line("Submit the lab report", None, now) == "A reminder. Submit the lab report."
    assert v.reminder_line("Submit the lab report", "2026-10-23", now) == \
        "A reminder. Submit the lab report. It's due today."
    assert v.reminder_line("Submit it.", "2026-10-24T14:00", now) == "A reminder. Submit it. It's due tomorrow at 2 pm."
    assert v.reminder_line("x", "2026-10-27T09:05", now).endswith("It's due on Tuesday at 9 oh 5 am.")
    assert v.reminder_line("x", "2026-11-03", now).endswith("It's due on the 3rd of November.")
    assert v.reminder_line("x", "not a date", now) == "A reminder. x."


@pytest.mark.parametrize("d,words", [(date(2026, 10, 22), "yesterday"), (date(2026, 11, 1), "on the 1st of November"),
                                     (date(2026, 11, 11), "on the 11th of November"),
                                     (date(2026, 11, 22), "on the 22nd of November")])
def test_day_words(d, words):
    assert v.day_words(d, date(2026, 10, 23)) == words


@pytest.mark.parametrize("h,m,words", [(0, 0, "at midnight"), (12, 0, "at noon"), (14, 30, "at 2 30 pm"),
                                       (9, 5, "at 9 oh 5 am"), (0, 15, "at 12 15 am")])
def test_time_words(h, m, words):
    assert v.time_words(datetime(2026, 1, 1, h, m)) == words


def test_catch_up_line():
    assert v.catch_up_line(1) == "You have one reminder from while I was off."
    assert v.catch_up_line(3) == "You have three reminders from while I was off."


def test_merge():
    one = [{"line": "Robot arm has finished. It took an hour.", "project": "Robot arm"}]
    assert v.merge("agent_finished", one) == one[0]["line"]
    two = one + [{"line": "…", "project": "ATLAS"}]
    assert v.merge("agent_finished", two) == "Two agents have finished. Robot arm and Atlas."
    three = two + [{"line": "…", "project": "Robot arm"}]
    assert v.merge("agent_waiting", three) == "Three agents need you. Robot arm and Atlas."
    rem = [{"line": "…", "text": "Call mum"}, {"line": "…", "text": "Pay rent"}]
    assert v.merge("reminder", rem) == "Two reminders. Call mum. Pay rent."


def test_lines_have_no_colons_or_dashes_between_words():
    now = datetime(2026, 10, 23, 9, 0)
    lines = [v.agent_waiting_line("ATLAS", "permission_prompt"), v.agent_finished_line("x", 4000),
             v.reminder_line("Pay rent", "2026-10-24T09:00", now), v.catch_up_line(2),
             v.readback_answer({0: "3 mm", 1: "4 mm"})]
    for line in lines:
        assert ":" not in line and " - " not in line and "—" not in line


def test_is_workout_matches_whole_words_and_phrases_any_case():
    from atlas.config import DEFAULT_SETTINGS
    words = DEFAULT_SETTINGS["voice_workout_words"]
    for yes in ("Leg day", "GYM at 6", "go for a run", "Morning run 5k", "push ups x50", "Yoga with Ana", "physio exercises"):
        assert v.is_workout(yes, words), yes
    for no in ("Run the tests", "Model training run", "Update the weights folder", "Call Ana", "gymnastics on TV", "", None):
        assert not v.is_workout(no, words), no
    assert v.is_workout("badminton at 7", ["badminton"]) and not v.is_workout("badminton", [])


def test_a_workout_reminder_opens_with_workout_time():
    from datetime import datetime
    now = datetime(2026, 10, 24, 9, 0)
    assert v.reminder_line("leg day", "2026-10-24T18:00", now, v.WORKOUT_OPENER) == "Workout time. leg day. It's due today at 6 pm."
    assert v.reminder_line("leg day", None, now) == "A reminder. leg day."
