from __future__ import annotations

import json
import logging
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
}

# Code locations come from the installed package, never from the data root (--root may be any folder).
_PKG = Path(__file__).resolve().parent

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_FABLE_URL = re.compile(r"^https://\S+$")
_ARM_URL = re.compile(r"^https?://[\w.-]+(:\d{1,5})?(/\S*)?$")
_REMOTE = re.compile(r"^(https://\S+|ssh://\S+|[\w.-]+@[\w.-]+:\S+)$")
# Inclusive bounds for the numeric settings.
RANGES: dict[str, tuple[int, int]] = {
    "port": (1024, 65535),
    "filing_timeout_s": (10, 3600), "recall_timeout_s": (10, 3600), "deeper_timeout_s": (10, 3600),
    "recall_top_n": (1, 50), "default_lead_days": (0, 60), "nudge_days": (1, 60),
    "backup_debounce_s": (0, 86400),
    "usage_warn_pct": (1, 100), "agents_end_linger_s": (0, 3600), "agents_stale_h": (1, 168),
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


def validate_setting(key: str, value) -> None:
    """Raise ValueError unless `value` is acceptable for setting `key`."""
    default = DEFAULT_SETTINGS[key]
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
