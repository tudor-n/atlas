from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SETTINGS: dict = {
    "port": 8090, "claude_path": "claude", "filing_model": "sonnet", "recall_model": "haiku",
    "deeper_model": "sonnet", "filing_timeout_s": 300, "recall_timeout_s": 90, "deeper_timeout_s": 300,
    "recall_top_n": 12, "default_lead_days": 2, "nudge_days": 3,
    "backup_remote": None, "backup_debounce_s": 300, "quiet_hours": ["23:00", "08:00"],
}

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class Paths:
    root: Path

    def __post_init__(self):
        object.__setattr__(self, "root", Path(self.root))

    @property
    def app(self) -> Path:
        return self.root / "app"

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
        return self.app / "web"

    @property
    def prompts(self) -> Path:
        return self.app / "brainhub" / "prompts"

    @property
    def schemas(self) -> Path:
        return self.prompts / "schemas"


def _check(key: str, value) -> None:
    default = DEFAULT_SETTINGS[key]
    if key == "quiet_hours":
        ok = (isinstance(value, list) and len(value) == 2
              and all(isinstance(v, str) and _HHMM.match(v) for v in value))
        if not ok:
            raise ValueError("quiet_hours must be a list of two 'HH:MM' strings")
        return
    if default is None:
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{key} must be a string or null")
        return
    # bool is an int subclass; reject it for non-bool defaults
    if type(value) is not type(default):
        raise ValueError(f"{key} must be {type(default).__name__}")


def load_settings(paths: Paths) -> dict:
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    if paths.settings_file.exists():
        stored = json.loads(paths.settings_file.read_text(encoding="utf-8"))
        settings.update({k: v for k, v in stored.items() if k in DEFAULT_SETTINGS})
    return settings


def save_settings(paths: Paths, patch: dict) -> dict:
    for key, value in patch.items():
        if key not in DEFAULT_SETTINGS:
            raise ValueError(f"unknown setting: {key}")
        _check(key, value)
    settings = load_settings(paths)
    settings.update(patch)
    paths.settings_file.parent.mkdir(parents=True, exist_ok=True)
    paths.settings_file.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return settings
