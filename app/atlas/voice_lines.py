"""What the voice says (phase 4 design §4.1): the info lines, before any quip.

The wording rules come from the owner's listening test: short spoken sentences that end in a full
stop, no colons, dashes or asides, and numbers in words where espeak reads digits oddly. Nothing
here reads agent-supplied text: a project is its name from the map or its folder name, and only
the owner's own words (item text, tablet notes, measurement answers) are read back."""
from __future__ import annotations

import re
from datetime import date, datetime

CAP = 200  # characters per line; longer ones end with "… and more"

_ONES = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
         "nineteen")
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")

# The tablet's unit chips, spoken (singular, plural). Anything else is read as written.
UNITS: dict[str, tuple[str, str]] = {
    "mm": ("millimetre", "millimetres"), "cm": ("centimetre", "centimetres"), "m": ("metre", "metres"),
    "km": ("kilometre", "kilometres"), "°": ("degree", "degrees"), "deg": ("degree", "degrees"),
    "g": ("gram", "grams"), "kg": ("kilogram", "kilograms"), "mg": ("milligram", "milligrams"),
    "V": ("volt", "volts"), "mV": ("millivolt", "millivolts"), "A": ("amp", "amps"),
    "mA": ("milliamp", "milliamps"), "Ω": ("ohm", "ohms"), "kΩ": ("kilo-ohm", "kilo-ohms"),
    "s": ("second", "seconds"), "ms": ("millisecond", "milliseconds"), "min": ("minute", "minutes"),
    "h": ("hour", "hours"), "N": ("newton", "newtons"), "Nm": ("newton metre", "newton metres"),
    "W": ("watt", "watts"), "Hz": ("hertz", "hertz"), "%": ("percent", "percent"),
    "in": ("inch", "inches"), "ml": ("millilitre", "millilitres"), "l": ("litre", "litres"),
}
_VALUE_UNIT = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*(\S+)\s*$")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December")
_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def number_words(n: int) -> str:
    """0–999 999 in British words ("four hundred and twelve"); larger numbers stay as digits."""
    if n < 0:
        return "minus " + number_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else "")
    if n < 1000:
        rest = n % 100
        return _ONES[n // 100] + " hundred" + (" and " + number_words(rest) if rest else "")
    if n < 1_000_000:
        rest = n % 1000
        head = number_words(n // 1000) + " thousand"
        if not rest:
            return head
        return head + (" and " if rest < 100 else " ") + number_words(rest)
    return str(n)


def _count(n: int, one: str, many: str) -> str:
    if n == 1:
        return ("an " if one[0] in "aeiou" or one == "hour" else "a ") + one
    return f"{number_words(n)} {many}"


def duration_words(seconds: float) -> str:
    """'forty-two minutes', 'an hour and ten minutes', 'two hours', 'less than a minute'."""
    minutes = int(seconds // 60)
    if minutes < 1:
        return "less than a minute"
    hours, minutes = divmod(minutes, 60)
    if not hours:
        return _count(minutes, "minute", "minutes")
    out = _count(hours, "hour", "hours")
    return out + (" and " + _count(minutes, "minute", "minutes") if minutes else "")


def spoken_project(name: str | None) -> str:
    """A project's name as spoken: split at case changes and at -/_/., trimmed, with only letters,
    digits, spaces and apostrophes kept ("TraceGreen" -> "Trace Green", "6-dof-arm" -> "6 dof arm")."""
    s = name or ""
    s = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", s)
    s = re.sub(r"[-_.]+", " ", s)
    s = re.sub(r"[^\w' ]+", " ", s)
    s = " ".join(s.split())[:40].strip()
    return s or "An agent"


def cap(text: str, limit: int = CAP) -> str:
    """At most `limit` characters, cut at a word, with "… and more" when anything was cut."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:.-")
    return cut + "… and more."


def sentence(text: str) -> str:
    """The owner's words as one spoken sentence: whitespace folded, a full stop at the end."""
    text = " ".join(text.split())
    if text and text[-1] not in ".!?…":
        text += "."
    return text


def unit_words(value: str) -> str:
    """'412 mm' -> '412 millimetres', '1 mm' -> '1 millimetre'. A value without a known unit, or
    with anything else around it, is read as written."""
    m = _VALUE_UNIT.match(value)
    if not m or m.group(2) not in UNITS:
        return " ".join(value.split())
    num, unit = m.group(1), m.group(2)
    one, many = UNITS[unit]
    return f"{num} {one if num in ('1', '-1') else many}"


def agent_waiting_line(project: str | None, kind: str | None) -> str:
    line = f"{spoken_project(project)} needs you."
    if kind == "permission_prompt":
        line += " It's asking for permission."
    return line


def agent_finished_line(project: str | None, seconds: float) -> str:
    return f"{spoken_project(project)} has finished. It took {duration_words(seconds)}."


def day_words(when: date, today: date) -> str:
    """'today', 'tomorrow', 'on Friday' (within a week), else 'on the 3rd of November'."""
    delta = (when - today).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    if delta == -1:
        return "yesterday"
    if 1 < delta < 7:
        return "on " + _DAYS[when.weekday()]
    d = when.day
    suffix = "th" if 11 <= d % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
    return f"on the {d}{suffix} of {_MONTHS[when.month - 1]}"


def time_words(t: datetime) -> str:
    """'at 2 pm', 'at 9 30 am', 'at noon', 'at midnight' (espeak reads these naturally)."""
    h, m = t.hour, t.minute
    if m == 0 and h in (0, 12):
        return "at midnight" if h == 0 else "at noon"
    hh = h % 12 or 12
    mm = "" if m == 0 else (f" oh {m}" if m < 10 else f" {m}")
    return f"at {hh}{mm} {'am' if h < 12 else 'pm'}"


def reminder_line(text: str, due: str | None, now: datetime) -> str:
    """'A reminder. Submit the lab report. It's due today at 2 pm.' The item text is the owner's
    own; the due part is left out when there's no due date or it can't be read."""
    line = "A reminder. " + cap(sentence(text), CAP - 40)
    if due:
        try:
            when = datetime.fromisoformat(due)
        except ValueError:
            return line
        part = day_words(when.date(), now.date())
        if len(due) > 10:
            part += " " + time_words(when)
        line += f" It's due {part}."
    return line


def catch_up_line(count: int) -> str:
    if count == 1:
        return "You have one reminder from while I was off."
    return f"You have {number_words(count)} reminders from while I was off."


def readback_note(text: str) -> str:
    return "Note saved. " + cap(sentence(text), CAP - 12)


_ORDINALS = ("First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth", "Tenth")


def readback_answer(answers: dict[int, str]) -> str:
    """'412 millimetres. Saved.', or 'First, 412 millimetres. Third, 98 millimetres. Saved.' Only
    the owner's values are read: the item labels were written by an agent (atlas-request)."""
    keys = sorted(answers)
    if len(keys) == 1:
        return cap(sentence(unit_words(answers[keys[0]])), CAP - 7) + " Saved."
    parts = [f"{_ORDINALS[i] if i < len(_ORDINALS) else 'Item ' + number_words(i + 1)}, "
             f"{sentence(unit_words(answers[i]))}" for i in keys]
    return cap(" ".join(parts), CAP - 7) + " Saved."


def merge(event: str, lines: list[dict]) -> str:
    """Several lines of one event type that arrived together, as one line. Each dict carries the
    fields its line was built from (`project`, `text`, `seconds`)."""
    if len(lines) == 1:
        return lines[0]["line"]
    n = number_words(len(lines)).capitalize()
    names = _and_list([spoken_project(x.get("project")) for x in lines])
    if event == "agent_finished":
        return f"{n} agents have finished. {names}."
    if event == "agent_waiting":
        return f"{n} agents need you. {names}."
    if event == "reminder":
        return cap(f"{n} reminders. " + " ".join(sentence(x["text"]) for x in lines))
    return cap(" ".join(x["line"] for x in lines))


def _and_list(names: list[str]) -> str:
    seen: list[str] = []
    for n in names:
        if n not in seen:
            seen.append(n)
    if len(seen) == 1:
        return seen[0]
    return ", ".join(seen[:-1]) + " and " + seen[-1]
