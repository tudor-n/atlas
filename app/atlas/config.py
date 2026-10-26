from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

WIDGET_TILES: tuple[str, ...] = ("today", "usage", "agents", "people", "deadlines", "review", "projects", "arm")

DEFAULT_SETTINGS: dict = {
    "port": 8090, "claude_path": "claude", "filing_model": "sonnet", "recall_model": "haiku",
    "deeper_model": "sonnet", "filing_timeout_s": 300, "recall_timeout_s": 90, "deeper_timeout_s": 300,
    "recall_top_n": 12, "default_lead_days": 2, "nudge_days": 3,
    "backup_remote": None, "backup_debounce_s": 300, "quiet_hours": ["23:00", "08:00"],
    "usage_warn_pct": 80, "fable_usage_url": "https://claude.ai/settings/usage",
    "agents_end_linger_s": 60, "agents_stale_h": 12, "arm_url": "http://127.0.0.1:8080",
    "widget_tiles": list(WIDGET_TILES),
    # Phase 3: the workbench tablet. Off until the owner switches it on; the hub then also listens
    # at tablet_address (the laptop's fixed home address), only while the laptop holds it.
    "tablet_lan": False, "tablet_address": "",
    "tablet_units": ["mm", "cm", "m", "°", "g", "kg", "V", "A", "mA", "Ω", "s"],
    # Phase 4: the voice. Inert until the voice files are downloaded (Settings → Voice). The blend,
    # speed and quote styles are the owner's: Atlas's voice (`atlas_jv2`), British English.
    "voice_enabled": True, "voice_sass": True, "voice_sass_readback": False,
    # `reply` (voice-input decisions VI4): what ATLAS says after the one input files a note or answers.
    "voice_events": {"agent_waiting": True, "agent_finished": True, "reminder": True, "readback": True,
                     "reply": True,
                     # Chatter (owner, 2026-10-26): briefings, self-awareness, nudges, reactions to what the
                     # owner does, things due today, and teasing about ATLAS's own to-dos.
                     "briefing": True, "self": True, "nudge": True, "react": True, "due_today": True,
                     "tease": True,
                     # Hello when ATLAS starts, goodbye when the owner quits it (owner, 2026-10-26).
                     "greet": True},
    "voice_finish_min_s": 120, "voice_blend": {"bm_george": 0.7, "bm_fable": 0.3},
    "voice_speed": 0.95, "voice_volume": 80, "voice_model": "int8",
    # Reminders whose text has one of these (whole words, any case) are workouts: "Workout time."
    # and a quip from the workout pool (owner, 2026-10-24). No bare "run" or "training": "run the
    # tests" and "model training" aren't workouts.
    "voice_workout_words": ["workout", "work out", "gym", "leg day", "push day", "pull day", "arm day",
                            "upper body", "lower body", "cardio", "hiit", "lifting", "lift weights",
                            "deadlift", "deadlifts", "squats", "bench press", "push ups", "pushups",
                            "pull ups", "pullups", "plank", "abs", "stretching", "yoga", "pilates",
                            "running", "go for a run", "morning run", "evening run", "jog", "jogging",
                            "swim", "swimming", "cycling", "bike ride", "spin class", "hike", "climbing",
                            "bouldering", "boxing", "strength training", "weight training", "exercise",
                            "exercises", "physio"],
    # Owner, 2026-10-24: the voice was lost under music. Boost evens every line out to a steady
    # loudness; duck_to is the share (%) of their volume other apps keep while ATLAS speaks (100 = off).
    "voice_boost": True, "voice_duck_to": 30,
    # Chatter (chatter spec §2, owner 2026-10-26). How talkative ATLAS is unprompted: mute, low, medium,
    # high, or debug (Very high: no waiting anywhere, drops back by itself). per_hour overrides the
    # level's cap (null = the level's). The subscription switch turns off every claude -p run chatter
    # makes (filing and recall aren't chatter); A quips, B briefings, C the weekly self-review.
    "voice_chattiness": "medium", "voice_chatter_per_hour": None,
    "voice_subscription": True, "voice_sub_quips": True, "voice_sub_briefings": True, "voice_sub_review": True,
    "voice_sub_max_pct": 80, "voice_last_call": "18:00", "voice_atlas_project": "",
}

CHATTINESS: tuple[str, ...] = ("mute", "low", "medium", "high", "debug")

VOICE_EVENTS: tuple[str, ...] = ("agent_waiting", "agent_finished", "reminder", "readback", "reply", "briefing",
                                 "self", "nudge", "react", "due_today", "tease", "greet")
VOICE_MODELS: tuple[str, ...] = ("int8", "fp32")
# The English voices in voices-v1.0.bin (a = American, b = British; f/m): the blend editor's list.
VOICE_IDS: tuple[str, ...] = (
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore", "af_nicole", "af_nova", "af_river",
    "af_sarah", "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael", "am_onyx",
    "am_puck", "am_santa", "bf_alice", "bf_emma", "bf_isabella", "bf_lily", "bm_daniel", "bm_fable",
    "bm_george", "bm_lewis")

# Code locations come from the installed package, never from the data root (--root may be any folder).
_PKG = Path(__file__).resolve().parent

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_SLUG = re.compile(r"^[\w][\w.-]*$")
_FABLE_URL = re.compile(r"^https://\S+$")
_ARM_URL = re.compile(r"^https?://[\w.-]+(:\d{1,5})?(/\S*)?$")
_REMOTE = re.compile(r"^(https://\S+|ssh://\S+|[\w.-]+@[\w.-]+:\S+)$")
_IPV4 = re.compile(r"^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$")
# Inclusive bounds for the numeric settings.
RANGES: dict[str, tuple[int, int]] = {
    "port": (1024, 65535),
    "filing_timeout_s": (10, 3600), "recall_timeout_s": (10, 3600), "deeper_timeout_s": (10, 3600),
    "recall_top_n": (1, 50), "default_lead_days": (0, 60), "nudge_days": (1, 60),
    "backup_debounce_s": (0, 86400),
    "usage_warn_pct": (1, 100), "agents_end_linger_s": (0, 3600), "agents_stale_h": (1, 168),
    "voice_finish_min_s": (0, 3600), "voice_volume": (0, 100), "voice_duck_to": (0, 100),
    "voice_sub_max_pct": (1, 100),
}


@dataclass(frozen=True)
class Paths:
    root: Path

    def __post_init__(self):
        object.__setattr__(self, "root", Path(self.root))

    @property
    def app(self) -> Path:
        """The code checkout (the folder holding the package and web/)."""
        return _PKG.parent

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def brain(self) -> Path:
        return self.root / "brain"

    @property
    def db(self) -> Path:
        return self.data / "hub.db"

    @property
    def settings_file(self) -> Path:
        return self.data / "settings.json"

    @property
    def web(self) -> Path:
        return _PKG.parent / "web"

    @property
    def prompts(self) -> Path:
        return _PKG / "prompts"

    @property
    def schemas(self) -> Path:
        return _PKG / "schemas"


def _private_ipv4(addr: str) -> bool:
    a, b = (int(x) for x in addr.split(".")[:2])
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)


def _finite_number(v) -> bool:
    return type(v) in (int, float) and math.isfinite(v)


def validate_setting(key: str, value) -> None:
    """Raise ValueError unless `value` is acceptable for setting `key`."""
    default = DEFAULT_SETTINGS[key]
    if key == "voice_chattiness":
        if value not in CHATTINESS:
            raise ValueError("voice_chattiness must be one of " + ", ".join(CHATTINESS))
        return
    if key == "voice_chatter_per_hour":
        if value is not None and (type(value) is not int or not 0 <= value <= 60):
            raise ValueError("voice_chatter_per_hour must be a whole number from 0 to 60, or null for the level's")
        return
    if key == "voice_last_call":
        if not isinstance(value, str) or not _HHMM.match(value):
            raise ValueError("voice_last_call must be a time like 18:00")
        return
    if key == "voice_atlas_project":
        if not isinstance(value, str) or len(value) > 80 or (value and not _SLUG.match(value)):
            raise ValueError("voice_atlas_project must be a project's slug from the map, or empty to find it")
        return
    if key == "quiet_hours":
        ok = (isinstance(value, list) and len(value) == 2
              and all(isinstance(v, str) and _HHMM.match(v) for v in value))
        if not ok:
            raise ValueError("quiet_hours must be a list of two 'HH:MM' strings")
        return
    if key == "widget_tiles":
        ok = (isinstance(value, list) and len(value) >= 1 and len(set(map(str, value))) == len(value)
              and all(isinstance(v, str) and v in WIDGET_TILES for v in value))
        if not ok:
            raise ValueError("widget_tiles must be a non-empty list of distinct tile names")
        return
    if key == "tablet_address":
        if value == "":
            return
        if not isinstance(value, str) or not _IPV4.match(value) or not _private_ipv4(value):
            raise ValueError("tablet_address must be this laptop's home address: a private IPv4 like "
                             "192.168.0.200 (10.x, 172.16-31.x or 192.168.x), or empty")
        return
    if key == "voice_workout_words":
        ok = (isinstance(value, list) and len(value) <= 100 and len({v.strip().lower() for v in value if isinstance(v, str)}) == len(value)
              and all(isinstance(v, str) and 2 <= len(v.strip()) <= 40 for v in value))
        if not ok:
            raise ValueError("voice_workout_words must be up to 100 distinct words or short phrases of 2-40 characters")
        return
    if key == "tablet_units":
        ok = (isinstance(value, list) and 1 <= len(value) <= 20 and len(set(map(str, value))) == len(value)
              and all(isinstance(v, str) and 1 <= len(v) <= 6 and not any(c.isspace() for c in v)
                      for v in value))
        if not ok:
            raise ValueError("tablet_units must be 1-20 distinct units of 1-6 characters, no spaces")
        return
    if key == "voice_events":
        ok = (isinstance(value, dict) and set(value) == set(VOICE_EVENTS)
              and all(type(v) is bool for v in value.values()))
        if not ok:
            raise ValueError("voice_events must say true or false for each of " + ", ".join(VOICE_EVENTS))
        return
    if key == "voice_blend":
        ok = (isinstance(value, dict) and 1 <= len(value) <= 3
              and all(k in VOICE_IDS for k in value)
              and all(_finite_number(v) and v > 0 for v in value.values()))
        if not ok:
            raise ValueError("voice_blend must be 1-3 of the English Kokoro voices (like bm_george) with weights above 0")
        return
    if key == "voice_speed":
        if not (_finite_number(value) and 0.5 <= value <= 2.0):
            raise ValueError("voice_speed must be a number from 0.5 to 2")
        return
    if key == "voice_model":
        if value not in VOICE_MODELS:
            raise ValueError("voice_model must be " + " or ".join(VOICE_MODELS))
        return
    if key in ("fable_usage_url", "arm_url"):
        rx = _FABLE_URL if key == "fable_usage_url" else _ARM_URL
        if not isinstance(value, str) or not rx.match(value):
            raise ValueError(f"{key} must be a valid {'https' if key == 'fable_usage_url' else 'http(s)'} URL")
        return
    if default is None:
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{key} must be a string or null")
        if key == "backup_remote" and value is not None and (
                value.startswith("-") or "::" in value or not _REMOTE.match(value)):
            raise ValueError("backup_remote must be an https://, ssh:// or user@host:path URL")
        return
    # bool is an int subclass; reject it for non-bool defaults
    if type(value) is not type(default):
        raise ValueError(f"{key} must be {type(default).__name__}")
    if key in RANGES:
        lo, hi = RANGES[key]
        if not lo <= value <= hi:
            raise ValueError(f"{key} must be between {lo} and {hi}")
    if isinstance(value, str) and not value.strip() and (key == "claude_path" or key.endswith("_model")):
        raise ValueError(f"{key} must not be empty")
    # settings.json is shared by both OSes, and a path only works on one of them (P6).
    if key == "claude_path" and ("/" in value or "\\" in value):
        raise ValueError("claude_path must be a command name (like claude), not a path; "
                         "each OS finds it on its own PATH")


def load_settings(paths: Paths, problems: list | None = None) -> dict:
    """Defaults overlaid with the stored file. A missing, unreadable or corrupt file, or a bad
    value in it, falls back to the defaults (described in `problems` when given). The file is
    never rewritten here: the next save from Settings writes a fresh valid one."""
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    f = paths.settings_file
    if not f.exists():
        return settings
    try:
        stored = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(stored, dict):
            raise ValueError("not a JSON object")
    except (OSError, ValueError) as e:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
        msg = f"{f} is unreadable ({e}); using the default settings until you save them again"
        log.warning(msg)
        if problems is not None:
            problems.append(msg)
        return settings
    for key, value in stored.items():
        if key not in DEFAULT_SETTINGS:
            continue
        if key == "voice_events" and isinstance(value, dict):
            # Saved by another build (VI5): one from before an event existed lacks its switch, which
            # starts at its default; one from after has a switch this build doesn't know, which is left
            # out. Either way the owner's other switches stand, instead of the whole dict failing.
            value = {**DEFAULT_SETTINGS["voice_events"], **{k: v for k, v in value.items() if k in VOICE_EVENTS}}
        try:
            validate_setting(key, value)
        except ValueError as e:
            msg = f"{f}: ignoring {key} ({e}); using the default"
            log.warning(msg)
            if problems is not None:
                problems.append(msg)
            continue
        settings[key] = value
    return settings


def save_settings(paths: Paths, patch: dict) -> dict:
    for key, value in patch.items():
        if key not in DEFAULT_SETTINGS:
            raise ValueError(f"unknown setting: {key}")
        validate_setting(key, value)
    settings = load_settings(paths)
    settings.update(patch)
    target = paths.settings_file
    target.parent.mkdir(exist_ok=True)  # data/ only, never the root (it may be gone)
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(settings, indent=2))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)  # atomic: a crash leaves the old file or the new one, never half
    return settings
