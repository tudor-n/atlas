# ATLAS Phase 2 (Widget, Usage meter, Agents panel) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One glance shows what's due, who's being waited on, plan usage and which Claude Code agents are running or waiting. It's shown in a floating Tauri desktop widget and in two new pages of the hub, all fed by silent user-level Claude Code hooks and the status line.

**Architecture:**
- The phase 1 aiohttp hub gains four modules: `usage.py`, `agents.py`, `widget.py` and `claude_integration.py`.
- A stdlib-only `hook_client.py` is run by Claude Code hooks and the status line. It POSTs a filtered event to the hub, swallows every error and prints nothing.
- The widget is a hub-served page (`/widget`) shown by a thin Tauri v2 shell, `app/desktop/`. The shell also supervises the hub process and owns the tray and autostart.

**Tech Stack:**
- Python 3.14 venv at `D:\ATLAS\app\.venv`, with aiohttp, pytest and pytest-aiohttp;
- vanilla ES modules with `node --test` (Node 24);
- Rust 1.99.1 MSVC (`%USERPROFILE%\.cargo\bin`, not on the default PATH: prefix commands with `$env:Path = "$env:USERPROFILE\.cargo\bin;" + $env:Path` in PowerShell or `export PATH="$HOME/.cargo/bin:$PATH"` in bash);
- Tauri 2.x crates: `tauri` (feature `tray-icon`), `tauri-plugin-single-instance`, `tauri-plugin-autostart`, `tauri-plugin-window-state`;
- `@tauri-apps/cli` 2.12.x via `npx`, only to generate icons;
- Claude Code CLI 2.1.306.

**Spec:**
- `D:\ATLAS\docs\superpowers\specs\2026-10-21-atlas-phase2-design.md`: the phase 2 design. Its §2 has the verified facts.
- Its parent, `D:\ATLAS\docs\superpowers\specs\2026-10-20-atlas-design.md`.
- Read both, plus `D:\ATLAS\app\CLAUDE.md` (hard rules) and `docs/superpowers/phase1-decisions.md`, before any task.

## Global Constraints

**Repo and commits**
- Work on branch `phase-2` in `D:\ATLAS`. Commit after each task with a plain message (`feat: …`, `fix: …`, `docs: …`). **Never add `Co-Authored-By` or `Claude-Session` trailers.**

**Never touch the real data or settings**
- Never run the hub against `D:\ATLAS` and never touch `D:\ATLAS\brain` or `D:\ATLAS\data`. Manual runs use a temp root (`$env:TEMP\bh-test`), deleted afterwards.
- **Never write the owner's real `%USERPROFILE%\.claude\settings.json`.** Tests and manual checks pass an explicit temp settings path. Task 1 adds an autouse pytest fixture that points `CLAUDE_CONFIG_DIR` and `LOCALAPPDATA` at a temp dir as a safety net.

**Server and UI rules**
- Phase 1 hard rules stay as they are. That means rules 1–10 in `app/CLAUDE.md`:
  - bind `127.0.0.1` only, behind the `local_only` middleware;
  - JSON bodies require `Content-Type: application/json`;
  - text reaches the DOM via `textContent`/`el()` only;
  - no web build step; the CSP in `index.html` is unchanged and copied to `widget.html`, so no inline `<script>` or `style=""` attributes. Setting `node.style.x` from JS is allowed.
- New settings go through `DEFAULT_SETTINGS`, `validate_setting` and the Settings view. No hardcoded tunables.

**Hooks and status line**
- **Hook rule:** `hook_client.py` must **always exit 0, write nothing to stdout or stderr**, never block longer than its timeout (1 s for events, 0.5 s for usage), import only the stdlib, and send only allow-listed fields. Prompts and tool data never leave it.
- Hook events from `sdk-*` entrypoints, or with a `cwd` inside the hub's `data/` or `brain/`, never create agent rows (design §5.1).

**Times and tests**
- Times stored by the hub are local ISO strings at seconds precision (`datetime.now().replace(microsecond=0).isoformat()`), as phase 1 does. Unix seconds appear only in raw usage payloads.
- Commands are run from `D:\ATLAS\app`.
  - Python tests: `.venv\Scripts\python -m pytest -q`. All 331 phase 1 tests keep passing.
  - JS tests: `node --test web/js/components/*.test.mjs web/js/views/*.test.mjs web/js/*.test.mjs`.

## Review Focus

1. **Hub down or slow while a Claude Code session runs:** the hook and status line client must exit 0 within ~1.5 s with empty stdout and stderr, so the session shows no error and gets no context. *Pinned:* Task 5 `test_event_hub_down_is_silent_and_fast`, `test_event_hub_slow_times_out_silently`.
2. **The owner's real `settings.json` is unusual** (comments or invalid JSON, existing hooks on the same events, an existing `statusLine`, a missing `.claude` folder): install refuses invalid JSON untouched and otherwise preserves everything else. Uninstall restores the old status line exactly. *Pinned:* Task 6 `test_install_refuses_invalid_json`, `test_install_preserves_foreign_hooks_and_keys`, `test_statusline_passthrough_roundtrip`.
3. **Windows path variants:** `d:\robotarmcontrol\src` must match a map folder `D:/RobotArmControl/`, but `D:\RobotArmControlOld` must not. *Pinned:* Task 3 `test_project_from_map_is_case_and_separator_insensitive`.
4. **The hub starts mid-session, or events arrive out of order:** the first event of any kind for an unknown session creates the row (except `SessionEnd`), and a `Stop` without a prior prompt still ends as `finished`. *Pinned:* Task 3 `test_unknown_session_any_event_creates_row`.
5. **Usage edges:** a corrupt `usage.json` at start, a `resets_at` already past, one window missing from a payload, `used_percentage` of 100.0 or a string. None may crash or show a stale % as current. *Pinned:* Task 2 `test_corrupt_file_starts_empty`, `test_reset_passed_hides_used`, `test_missing_window_keeps_last`, `test_bad_values_rejected`.

---

## File Structure

| Path | Responsibility | Task |
|---|---|---|
| `atlas/config.py` (modify) | 6 new settings and their validation | 1 |
| `tests/conftest.py` (modify) | autouse safety fixture (`CLAUDE_CONFIG_DIR`, `LOCALAPPDATA` → tmp) | 1 |
| `web/js/views/settings.js` (modify) | `widget_tiles` checkbox field, hints | 1 |
| `atlas/usage.py` (create) | `UsageStore`: validate, merge, persist, view | 2 |
| `atlas/agents.py` (create) | path/branch/project helpers, `AgentTracker` state machine | 3 |
| `atlas/store.py` (modify) | `agents` table + 4 methods | 3 |
| `atlas/routes.py` (modify) | new endpoints: usage, hooks, agents, integration, widget, open | 2, 4, 6, 7 |
| `atlas/server.py` (modify) | wire `usage`, `agents`, `opener`, `arm`, prune task | 2, 4, 7 |
| `atlas/hook_client.py` (create) | stdlib-only client for hooks and the status line | 5 |
| `atlas/claude_integration.py` (create) | install, uninstall and status in Claude settings | 6 |
| `atlas/__main__.py` (modify) | subcommands `integration`, `desktop-config` | 6, 10 |
| `atlas/widget.py` (create) | `build_widget`, `synopsis`, `ArmProbe` | 7 |
| `web/js/views/usage.js`, `web/js/views/agents.js`, `web/js/views/agents-model.js` (create) | app pages | 8 |
| `web/js/app.js`, `web/style.css` (modify) | nav and registry, styles | 8 |
| `web/widget.html`, `web/widget.css`, `web/js/widget.js`, `web/js/widget-model.js` (create) | widget page | 9 |
| `atlas/desktop_config.py` (create) | writes `%LOCALAPPDATA%\ATLAS\desktop.json` | 10 |
| `app/desktop/**` (create) | Tauri shell | 10 |
| `README.md`, `app/README.md`, `app/CLAUDE.md`, `docs/superpowers/phase2-decisions.md` | docs | 11 |

Tests go in `tests/test_<module>.py` (Python), next to the module as `*.test.mjs` (JS), and in `app/desktop/src-tauri/src/*.rs` `#[cfg(test)]` (Rust).

---

### Task 1: Phase 2 settings

**Files:**
- Modify: `atlas/config.py` (`DEFAULT_SETTINGS`, `RANGES`, `validate_setting`)
- Modify: `tests/conftest.py`
- Modify: `web/js/views/settings.js` (`buildField`, `HINTS`)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces settings keys:
  - `usage_warn_pct: int = 80` (1–100);
  - `fable_usage_url: str = "https://claude.ai/settings/usage"` (must match `^https://\S+$`);
  - `agents_end_linger_s: int = 60` (0–3600);
  - `agents_stale_h: int = 12` (1–168);
  - `arm_url: str = "http://127.0.0.1:8080"` (must match `^https?://[\w.-]+(:\d{1,5})?(/\S*)?$`);
  - `widget_tiles: list[str] = ["today","usage","agents","people","deadlines","review","projects","arm"]`.
- Produces `config.WIDGET_TILES: tuple[str, ...]` (the 8 names above, in that order).

- [ ] **Step 1: Write the failing tests** in `tests/test_config.py`:
  - `test_phase2_defaults`: `load_settings(paths)` contains the 6 keys with the defaults above.
  - `test_widget_tiles_validation`: `save_settings(paths, {"widget_tiles": ["usage", "today"]})` keeps that order. Each of these raises `ValueError`: `[]`, `["today", "today"]`, `["bogus"]`, `"today"` and `[1]`.
  - `test_url_settings_validation`:
    - `arm_url` accepts `"http://192.168.1.5:8080"` and rejects `"ftp://x"` and `"javascript:alert(1)"`;
    - `fable_usage_url` rejects `"http://claude.ai"`;
    - `usage_warn_pct` rejects 0 and 101.
- [ ] **Step 2: Run** `.venv\Scripts\python -m pytest tests/test_config.py -q`. Expected: the 3 new tests FAIL.
- [ ] **Step 3: Implement** in `config.py`:
  - Add the keys to `DEFAULT_SETTINGS` and the int ranges to `RANGES`.
  - In `validate_setting`, special-case `widget_tiles` (a list of distinct members of `WIDGET_TILES`, length ≥ 1) and the two URL keys (the regexes above), before the generic type check.
- [ ] **Step 4: Safety fixture.** In `tests/conftest.py`, add an `autouse=True` fixture that `monkeypatch.setenv`s `CLAUDE_CONFIG_DIR` to `tmp_path / "claude-config"` and `LOCALAPPDATA` to `tmp_path / "localappdata"`.
- [ ] **Step 5: Settings UI.** In `settings.js` `buildField`:
  - `Array.isArray(value)` for `widget_tiles` renders one checkbox per tile in `WIDGET_TILES` order (the JS keeps its own copy of the 8 names). `read()` returns the checked names, keeping the order of `value` for previously selected tiles and appending newly checked ones, and throws `Error('Pick at least one widget tile.')` when none are checked.
  - Add `HINTS`:
    - `port`: `'Takes effect after ATLAS restarts. After changing it, install the Claude Code integration again.'`;
    - `usage_warn_pct`: `'Usage at or above this % shows as a warning.'`;
    - `agents_end_linger_s`: `'Seconds a closed session stays listed.'`;
    - `agents_stale_h`: `'Hours without any event before a session is dropped.'`;
    - `arm_url`: `'Robot arm server address (opened from the widget).'`;
    - `widget_tiles`: `'Tiles shown in the expanded widget.'`.
  - Run `node --check web/js/views/settings.js`.
- [ ] **Step 6: Run** the full Python suite. Expected: all pass.
- [ ] **Step 7: Commit** `feat: phase 2 settings (usage warning, agents timing, arm url, widget tiles)`.

---

### Task 2: Usage store and API

**Files:**
- Create: `atlas/usage.py`
- Modify: `atlas/routes.py`, `atlas/server.py`
- Test: `tests/test_usage.py`, `tests/test_server.py` (append)

**Interfaces:**
- Produces `class UsageStore` with:
  - `__init__(self, path: Path, now: Callable[[], float] = time.time)`. It loads `path` if present, and a corrupt file means empty (logged, not raised).
  - `update(self, payload: dict) -> bool`. It raises `ValueError` when `payload["rate_limits"]` is not a dict. It returns True when a stored value changed, and saves atomically (tmp + `os.replace`).
  - `view(self, warn_pct: int, fable_url: str) -> dict`, shaped as `{"five_hour": W|None, "seven_day": W|None, "updated_at": iso|None, "age_s": int|None, "warn_pct": int, "fable_url": str}`.
    - `W = {"used": float|None, "last_used": float, "resets_at": "YYYY-MM-DDTHH:MM" (local), "reset_passed": bool, "warn": bool}`.
  - `updated_at(self) -> str | None`.
- Stored file shape: `{"five_hour": {"used_percentage": float, "resets_at": int}|absent, "seven_day": …, "updated_at": iso}`.
- Produces:
  - `app["usage"]` (path `paths.data / "usage.json"`);
  - routes `POST /api/usage` → 204 and `GET /api/usage` → the view;
  - WebSocket `usage.changed` with the view as data, sent only when `update` returns True.
- `create_app(..., now=None)` is **not** added. Tests construct `UsageStore` directly with a fake clock.

- [ ] **Step 1: Write failing tests** in `tests/test_usage.py`, with `Clock` from `tests/fakes.py` and `T = 1_800_000_000`:
  - `test_update_and_view`:
    - Update with `{"rate_limits": {"five_hour": {"used_percentage": 62.4, "resets_at": T+3600}, "seven_day": {"used_percentage": 81, "resets_at": T+86400}}}` returns True.
    - `view(80, url)["five_hour"]` has `used == 62.4`, `warn is False`, `reset_passed is False`, and `resets_at == datetime.fromtimestamp(T+3600).strftime("%Y-%m-%dT%H:%M")`. `seven_day["warn"] is True`.
    - The same payload again returns False.
  - `test_missing_window_keeps_last`: a payload with only `seven_day` keeps the earlier `five_hour`.
  - `test_reset_passed_hides_used`: with the clock moved past `resets_at`, `used is None`, `last_used == 62.4` and `reset_passed is True`.
  - `test_bad_values_rejected`:
    - `used_percentage` of `"62"`, `-1`, `100.5` or `True`, or `resets_at` of `"x"` or `1.5`, ignores that window. Other windows still apply.
    - `{"rate_limits": "x"}` raises `ValueError`.
    - `100.0` is accepted.
  - `test_persists_and_corrupt_file_starts_empty` (also referenced as `test_corrupt_file_starts_empty`): a new `UsageStore` on the same path sees the values. Writing `"{nope"` to the file and constructing a store gives `view()["five_hour"] is None` with no exception.
  - `test_age`: `age_s` equals clock delta since the last update; `updated_at` is None before any update.
- [ ] **Step 2: Run** `pytest tests/test_usage.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement `usage.py`.** It accepts numbers with `type(v) in (int, float)` (rejecting bool), with 0 ≤ used ≤ 100 and an int `resets_at`.
- [ ] **Step 4: Wire the server.**
  - `server.create_app` sets `app["usage"] = UsageStore(paths.data / "usage.json")`.
  - Routes:
    - `post_usage`: `body_dict`, then `t(app["usage"].update, body)`. If changed, broadcast `usage.changed` with the view built from current settings. Return `web.Response(status=204)`.
    - `get_usage`: returns the view from `load_settings` values `usage_warn_pct` and `fable_usage_url`.
- [ ] **Step 5: Add server tests** to `tests/test_server.py`:
  - `test_usage_endpoints`: POST a payload, expect 204 then GET with `five_hour.used == 62.4`. POST `{"rate_limits": 3}` gives 400. POST with `Content-Type: text/plain` gives 415.
- [ ] **Step 6: Run** the full suite. Expected: PASS.
- [ ] **Step 7: Commit** `feat: usage meter store and API (status line rate limits)`.

---

### Task 3: Agent tracker core

**Files:**
- Create: `atlas/agents.py`
- Modify: `atlas/store.py` (schema + methods)
- Test: `tests/test_agents.py`

**Interfaces:**
- Store additions. The `_SCHEMA` gains a table:
  ```sql
  CREATE TABLE IF NOT EXISTS agents(
      session_id TEXT PRIMARY KEY, cwd TEXT NOT NULL, project TEXT NOT NULL, project_name TEXT NOT NULL,
      mapped INTEGER NOT NULL, branch TEXT, title TEXT, part_override TEXT, entrypoint TEXT,
      where_override TEXT, status TEXT NOT NULL, note TEXT, started_at TEXT NOT NULL,
      status_since TEXT NOT NULL, last_event_at TEXT NOT NULL, ended_at TEXT);
  ```
  Methods:
  - `upsert_agent(self, row: dict) -> None`: all columns; `mapped` is a bool stored as an int.
  - `get_agent(self, session_id: str) -> dict | None`.
  - `list_agents(self) -> list[dict]`.
  - `delete_agents(self, ids: list[str]) -> None`.
- `agents.py` module functions:
  - `norm_path(p: str) -> str`: `os.path.normcase(os.path.normpath(p))` with the trailing separator stripped. This is a lowercase backslash form on Windows.
  - `is_within(path: str, folder: str) -> bool`: equal to, or a prefix of, `folder + os.sep`, after `norm_path`.
  - `find_git_dir(cwd: str) -> tuple[Path, Path] | None`: returns `(worktree_top, git_dir)` by walking up from cwd. A `.git` directory, or a `.git` file with `gitdir: <path>` (relative to the file's folder or absolute), counts.
  - `read_branch(cwd: str) -> str | None`: `ref: refs/heads/X` gives `X`. A 40-hex HEAD gives `"detached"`. No git dir, or an unreadable HEAD, gives None.
  - `simplify_branch(b: str | None) -> str | None`: the part after the last `/`.
  - `resolve_project(cwd: str, m: BrainMap | None) -> tuple[str, str, bool]`, returning `(project, project_name, mapped)`:
    - the longest matching folder across `m.projects[*]["folders"]` gives `(slug, name, True)`;
    - else the git top folder name, else `Path(cwd).name` (or `cwd` itself if empty), as `(name, name, False)`.
  - `WHERE_BY_ENTRYPOINT = {"cli": "terminal", "claude-vscode": "IDE", "claude-desktop": "desktop app"}`.
  - `WHERE_CHOICES = ("terminal", "IDE", "desktop app", "unknown")`.
  - `STATUS_ORDER = ("waiting", "running", "finished", "idle")`.
- `class AgentTracker`:
  - `__init__(self, store: Store, paths: Paths, settings: Callable[[], dict], now: Callable[[], datetime] = datetime.now)`.
  - `handle(self, event: dict) -> bool`: raises `ValueError` if `session_id` or `hook_event_name` is missing or not a str. Returns True when a broadcast-worthy change happened.
  - `rows(self) -> list[dict]`: prunes, then returns public rows sorted by `STATUS_ORDER` then `last_event_at` desc. Each row is the stored columns plus:
    - `part`: override › title › simplified branch › `"—"`;
    - `where`: override › entrypoint map › `"unknown"`;
    - `next_time`: the count of open `next_time` items with `project == row.project` when mapped, else 0.
  - `prune(self) -> bool`: deletes rows ended more than `agents_end_linger_s` ago, and rows whose `last_event_at` is more than `agents_stale_h` hours old.
  - `edit(self, session_id: str, patch: dict) -> dict`: keys `part` (str ≤ 80 chars after strip, `""` or None clears) and `where` (in `WHERE_CHOICES` or None). Raises `KeyError` for an unknown id and `ValueError` for bad keys or values. Returns the public row.
  - `remove(self, session_id: str) -> None`.
  - `last_event_at(self) -> str | None`: the max over rows, for the integration status.

- [ ] **Step 1: Write failing tests** in `tests/test_agents.py`. Use the `store` and `paths` fixtures, a `now` closure over a mutable datetime, and real temp folders with handmade `.git/HEAD` files.
  - `test_read_branch_variants`:
    - `ref: refs/heads/feat/ui-reskin\n` gives `"feat/ui-reskin"`, which `simplify_branch` turns into `"ui-reskin"`;
    - a 40-hex HEAD gives `"detached"`;
    - a subfolder cwd finds the parent's `.git`;
    - a `.git` file `gitdir: ../real/.git/worktrees/w1` with a HEAD there is followed;
    - no git gives None.
  - `test_project_from_map_is_case_and_separator_insensitive`:
    - The map project `{"slug": "robotarm", "name": "RobotArm", "folders": ["D:/RobotArmControl/"]}`.
    - `resolve_project(r"d:\robotarmcontrol\src", m) == ("robotarm", "RobotArm", True)`.
    - `resolve_project(r"D:\RobotArmControlOld", m)[2] is False`.
    - The longest match wins between `D:/Work` and `D:/Work/robot`.
  - `test_project_fallback_git_top_then_basename`: unmapped cwd inside a temp repo `myrepo/sub` gives `("myrepo", "myrepo", False)`.
  - `test_lifecycle`:
    - SessionStart (`entrypoint: "cli"`) gives status `idle` and `where == "terminal"`.
    - UserPromptSubmit gives `running`.
    - Notification `permission_prompt` with message `"Claude needs your permission to use Bash"` gives `waiting` with that note.
    - PostToolUse gives `running` with note None.
    - Stop gives `finished`.
    - `idle_prompt` while `finished` stays `finished` and `handle` returns False.
    - SessionEnd sets `ended_at`.
    - At linger + 1 s, `rows()` is empty.
  - `test_idle_prompt_while_running_waits`.
  - `test_stop_failure_waits_with_error_note`: `{"hook_event_name": "StopFailure", "error_type": "rate_limit"}` gives `waiting` with note `"rate_limit"`. If only `error` is present, it is used.
  - `test_unknown_session_any_event_creates_row`: a first event `Stop` gives a row `finished`. A first event `SessionEnd` creates nothing.
  - `test_ignored_events`: `entrypoint: "sdk-cli"` creates no row. A cwd under `paths.data` or `paths.brain` creates no row. A missing `session_id` raises `ValueError`.
  - `test_part_priority_and_edit`:
    - The branch gives the part.
    - A `session_title` on UserPromptSubmit overrides it.
    - `edit(id, {"part": "wrist"})` overrides that.
    - `edit(id, {"part": ""})` clears it back to the title.
    - `edit(id, {"where": "IDE"})` works, and `{"where": "moon"}` raises `ValueError`.
    - The override survives a later UserPromptSubmit.
    - An unknown id raises `KeyError`.
  - `test_stale_rows_pruned`: no event for 12 h + 1 s means `rows()` is empty.
  - `test_next_time_flag`: an open `next_time` item with project `robotarm` (`store.create_item(kind="next_time", text="check J5", area="projects", project="robotarm", source_entry="e-1")`) and a session in a mapped folder (save a map with `save_map(paths.brain, ...)`) give `next_time == 1`.
  - `test_post_tool_use_is_cheap`: repeated PostToolUse while `running` returns False, and `last_event_at` is written at most every 30 s.
  - `test_note_is_capped`: a 10,000-char message is stored at ≤ 300 chars.
- [ ] **Step 2: Run** `pytest tests/test_agents.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement the store additions, then `agents.py`.**
  - `handle` loads the map with `load_map(paths.brain)` only when creating or reviving a row (SessionStart or an unknown session) and on UserPromptSubmit (branch refresh).
  - The status transitions are the table in design §5.1.
  - Revival: SessionStart on an existing row clears `ended_at`. Sources `resume`, `clear`, `compact` and `fork` keep the status; `startup` sets `idle`.
- [ ] **Step 4: Run** `pytest tests/test_agents.py tests/test_store.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: agent tracker (hook events to session rows, project/branch/where resolution)`.

---

### Task 4: Hooks and agents API

**Files:**
- Modify: `atlas/routes.py`, `atlas/server.py`
- Test: `tests/test_api_agents.py` (new; reuse fixtures by importing `MAP`, `make_runner` from `tests.test_server`)

**Interfaces:**
- Consumes: `AgentTracker` (Task 3).
- Produces:
  - `app["agents"] = AgentTracker(store, paths, lambda: load_settings(paths))`;
  - routes:
    - `POST /api/hooks/event` → 204, always with an empty body, even for ignored events. A body that isn't a JSON object, or a missing `session_id`/`hook_event_name`, gives 400. Content type rules as phase 1.
    - `GET /api/agents` → `rows()`.
    - `PATCH /api/agents/{session_id}` → the row (404 unknown, 400 bad).
    - `DELETE /api/agents/{session_id}` → 204.
  - WebSocket `agents.changed` (data `{}`) after a `handle` that returned True, after PATCH/DELETE, and from a background task that calls `prune()` every 30 s when `start_workers` is set, broadcasting when it returns True.

- [ ] **Step 1: Write failing tests:**
  - `test_hook_event_roundtrip`:
    - POST SessionStart `{"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(tmp), "source": "startup", "entrypoint": "cli"}` gives 204 with an empty body.
    - GET `/api/agents` shows one row with `status == "idle"`.
    - PATCH `{"part": "wrist"}` returns `part == "wrist"`.
    - DELETE gives 204 and the list is empty.
    - PATCH on an unknown id gives 404.
  - `test_hook_event_rejects_garbage`: `[]` gives 400, `{}` gives 400, a text/plain body gives 415.
  - `test_hook_event_ignored_is_204`: an `sdk-cli` entrypoint gives 204 and no row.
  - `test_agents_changed_broadcast`: replace `app["broadcast"]` with a recorder before the client starts (as phase 1 tests do, if they do; otherwise patch `app["agents"]` and call the route). UserPromptSubmit records `("agents.changed", {})` once, and a repeated PostToolUse records nothing new.
- [ ] **Step 2: Run** the new file. Expected: FAIL.
- [ ] **Step 3: Implement the routes and wiring.** The prune task joins `app["tasks"]` in `on_startup` when `start_workers`.
  - `KeyError` → 404 via `web.HTTPNotFound(reason="unknown session")`.
  - `ValueError` → 400 through the existing middleware.
- [ ] **Step 4: Run** the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat: hooks endpoint and agents API with live updates`.

---

### Task 5: Hook and status line client

**Files:**
- Create: `atlas/hook_client.py`
- Test: `tests/test_hook_client.py`

**Interfaces:**
- CLI, run as `python -I -S <path>/hook_client.py <mode> --port P [--passthrough-file F]`:
  - `event`: reads stdin JSON and POSTs `filter_event(...)` to `http://127.0.0.1:P/api/hooks/event`, timeout 1.0 s.
  - `statusline`: reads stdin JSON and POSTs `{"rate_limits": d.get("rate_limits"), "session_id": d.get("session_id")}` to `/api/usage` (timeout 0.5 s), **only when `rate_limits` is a dict**.
    - It then prints one line, UTF-8 via `sys.stdout.buffer`:
      - with `--passthrough-file F` (whose content is a shell command), the stdout of running that command with the same stdin (`subprocess.run(cmd, shell=True, input=raw, capture_output=True, timeout=2)`), first line only. On any failure it falls back to the own line;
      - otherwise `statusline_text(d)`.
- Functions (importable for unit tests, stdlib only):
  - `ALLOWED = ("hook_event_name", "session_id", "cwd", "source", "reason", "notification_type", "session_title")`.
  - `CAPPED = ("message", "title", "error", "error_type")`, each a string capped at 300 chars.
  - `filter_event(d: dict, env: Mapping[str, str]) -> dict`: allowed str fields, plus capped fields, plus `"entrypoint": env.get("CLAUDE_CODE_ENTRYPOINT")`.
  - `statusline_text(d: dict) -> str`:
    - `model.display_name`, then `5h {p:.0f}% ↻{HH:MM}` (local time of `resets_at`), then `7d {p:.0f}%`, joined with ` · `;
    - missing parts are skipped; nothing at all gives `"ATLAS"`.
  - `main(argv: list[str]) -> int`: wraps everything in `try/except BaseException` and always returns 0. The module ends with `if __name__ == "__main__": sys.exit(main(sys.argv[1:]))`. It must not import any `atlas` module (it runs with `-I -S`, outside the package).

- [ ] **Step 1: Write failing tests.** Subprocess tests run `[sys.executable, "-I", "-S", str(HOOK), ...]` with `input=` bytes, `capture_output=True` and `timeout=10`, and measure wall time.
  - `test_filter_event_allowlist`:
    - an input with `prompt`, `tool_input`, `tool_response`, `transcript_path` and a 1,000-char `message`;
    - the output has none of the first four, `len(message) == 300`, and `entrypoint == "cli"` from env `{"CLAUDE_CODE_ENTRYPOINT": "cli"}`.
  - `test_statusline_text`:
    - `{"model": {"display_name": "Sonnet"}, "rate_limits": {"five_hour": {"used_percentage": 62.4, "resets_at": T}, "seven_day": {"used_percentage": 31}}}` gives `f"Sonnet · 5h 62% ↻{datetime.fromtimestamp(T):%H:%M} · 7d 31%"`;
    - `{}` gives `"ATLAS"`.
  - `test_event_posts_to_hub`: start a stdlib `http.server` on a free port in a thread, recording bodies and replying 204. Run `event`. The recorded JSON has `hook_event_name` and no `prompt`. The return code is 0, and stdout and stderr are `b""`.
  - `test_event_hub_down_is_silent_and_fast`: use a free port with nothing listening. Return code 0, empty stdout and stderr, under 1.5 s (excluding interpreter start: measure against a baseline `python -I -S -c pass` run, allowing +1.5 s).
  - `test_event_hub_slow_times_out_silently`: the server sleeps 5 s before replying. The run finishes in under baseline + 2 s with return code 0 and empty output.
  - `test_event_garbage_stdin_is_silent`: stdin `b"not json"` and empty stdin both give return code 0 with empty output.
  - `test_statusline_posts_usage_and_prints`: the server records `/api/usage`. stdout equals `statusline_text(...)` plus `"\n"`, and the return code is 0.
  - `test_statusline_passthrough`: a passthrough file containing `echo hello` gives stdout `b"hello\n"`. A passthrough file whose command fails falls back to the own line.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** with `urllib.request` and `Request(url, data, headers={"Content-Type": "application/json"}, method="POST")`. Read stdin with `sys.stdin.buffer.read()`, and never print in `event` mode.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: silent hook and status line client`.

---

### Task 6: Claude Code integration installer

**Files:**
- Create: `atlas/claude_integration.py`
- Modify: `atlas/__main__.py`, `atlas/routes.py`, `atlas/server.py`
- Test: `tests/test_claude_integration.py`, `tests/test_api_integration.py`

**Interfaces:**
- `EVENTS = ("SessionStart", "UserPromptSubmit", "PostToolUse", "Notification", "Stop", "StopFailure", "SessionEnd")`. Only `PostToolUse` is async.
- `MARK = "hook_client.py"`.
- `class IntegrationError(Exception)`.
- `default_settings_path() -> Path`: `Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json"` if that env var is set, else `Path.home() / ".claude" / "settings.json"`.
- `hook_script() -> Path`: `Path(__file__).with_name("hook_client.py")`.
- `hook_entry(python: str, script: str, port: int, *, is_async: bool) -> dict`: returns `{"type": "command", "command": python, "args": ["-I", "-S", script, "event", "--port", str(port)], "timeout": 5}`, plus `"async": True` when `is_async`.
- `statusline_command(python: str, script: str, port: int, passthrough: Path | None) -> str`: forward slashes, each path quoted only if it contains a space.
- `install(settings_path: Path, *, python: str, script: str, port: int, data_dir: Path, now: Callable[[], datetime] = datetime.now) -> dict`.
- `uninstall(settings_path: Path, *, data_dir: Path) -> dict`.
- `status(settings_path: Path, *, port: int) -> dict`, shaped as `{"settings_path": str, "hooks_installed": bool, "events": [names with our hook], "statusline_installed": bool, "port_matches": bool, "previous_statusline": bool}`.
- Files in `data_dir`: `claude_integration.json` (`{"previous_statusline": <obj>|null, "installed_at": iso}`) and `statusline_passthrough.txt`.
- API:
  - `GET /api/integration` → `status(...)` plus `"last_hook_at": app["agents"].last_event_at()` and `"last_usage_at": app["usage"].updated_at()`;
  - `POST /api/integration/install` and `/uninstall` → status.
  - `IntegrationError` → 400.
  - `app["claude_settings_path"]` is set by `create_app(..., claude_settings_path: Path | None = None)`, defaulting to `default_settings_path()`. Install uses `sys.executable`, `str(hook_script())`, the settings `port` and `paths.data`.
- CLI: `python -m atlas integration {install,uninstall,status} --root R [--settings PATH]` prints the status JSON (indent 2) and exits 0, or prints `error: …` to stderr and exits 1. `python -m atlas --root R [--port N]` keeps working unchanged.

- [ ] **Step 1: Write failing tests:**
  - `test_install_into_missing_file`: the settings path's parent doesn't exist. Afterwards:
    - every event in `EVENTS` has exactly one group with our hook;
    - `PostToolUse`'s has `"async": True` and the others have no `async`;
    - `statusLine.command` contains `hook_client.py statusline --port 8090`;
    - `status()["hooks_installed"]` and `["port_matches"]` are True.
  - `test_install_is_idempotent_and_updates_port`: install twice with port 8090, then once with 8091. There is one group per event, and all args carry `8091`. `status(port=8090)["port_matches"]` is False.
  - `test_install_preserves_foreign_hooks_and_keys`:
    - the existing file has `{"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}, "permissions": {"allow": ["x"]}}`;
    - after install, `model` and `permissions` are unchanged and `Stop` has 2 groups including the foreign one;
    - after uninstall, the file equals the original object (`json.loads` comparison).
  - `test_install_backs_up_original`: a `settings.json.atlas-backup-*` file with the original bytes exists after install.
  - `test_install_refuses_invalid_json`: the file `{ // comment\n}` raises `IntegrationError`, the file bytes are unchanged, and no backup is created.
  - `test_statusline_passthrough_roundtrip`:
    - the existing `statusLine: {"type": "command", "command": "bash ~/x.sh", "padding": 1}`;
    - after install, the command has `--passthrough-file`, and the passthrough file contains `bash ~/x.sh`;
    - after uninstall, `statusLine` equals the original dict exactly.
  - `test_uninstall_when_not_installed_is_noop`: the file is unchanged.
  - `test_statusline_command_quoting`: `python="C:/Program Files/py/python.exe"` comes out quoted, and `D:/ATLAS/...` comes out unquoted.
  - API (`tests/test_api_integration.py`, `create_app(..., claude_settings_path=tmp_path/"s"/"settings.json")`): GET shows `hooks_installed` False; POST install then GET shows True; POST uninstall shows False.
  - `test_default_path_honours_env` (the autouse fixture sets `CLAUDE_CONFIG_DIR`): `default_settings_path()` is under `tmp_path`.
  - CLI: `subprocess.run([sys.executable, "-m", "atlas", "integration", "status", "--root", str(tmp), "--settings", str(f)], cwd=APP_DIR)` gives return code 0 and stdout JSON with `hooks_installed`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Write atomically: tmp in the same folder, then `os.replace`, keeping `json.dumps(obj, indent=2)` plus a newline.
  - A group is ours when any hook in it has `MARK` in `" ".join(h.get("args", []))` or in `h.get("command", "")`.
  - A status line is ours when `MARK` is in its `command`.
- [ ] **Step 4: Run** the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat: Claude Code integration installer (hooks + status line, CLI and API)`.

---

### Task 7: Widget summary, open, arm probe

**Files:**
- Create: `atlas/widget.py`
- Modify: `atlas/routes.py`, `atlas/server.py`
- Test: `tests/test_widget.py`, `tests/test_api_widget.py`

**Interfaces:**
- `class ArmProbe`:
  - `__init__(self, now: Callable[[], float] = time.monotonic, ttl: float = 15.0)`.
  - `async reachable(self, url: str) -> bool`: `asyncio.open_connection(host, port)` within 1 s, with the port defaulting to 80/443 by scheme. Cached per url for `ttl`.
- `synopsis(today: dict, review: dict, waiting: list[dict], usage: dict, agents: dict) -> str`. The rules, in order, joined with ` · `:
  1. `n = len(overdue) + len(due_today)`. When n > 0: `f"{n} due today"`, plus `f" ({k} overdue)"` when k = len(overdue) > 0.
  2. Waiting delegations, counted by `len(waiting)`. Exactly 1 gives `f"1 waiting on {name}"`; more gives `f"{n} waiting"`.
  3. Review: `p + f > 0` gives `f"{p+f} to review"`.
  4. Usage: when `five_hour.used` is not None, `f"5h {used:.0f}% (resets {HH:MM})"`.
  5. Agents: `running` gives `"1 agent running"` or `f"{n} agents running"`; `waiting` gives `"1 agent waiting"` or `f"{n} agents waiting"`.
  6. With nothing, `"All clear"`.
- `build_widget(app_state: dict) -> dict`. The input is `{"items": list[Item], "proposals": int, "failed": int, "map": BrainMap|None, "usage": dict, "agents": list[dict], "settings": dict, "arm_reachable": bool, "now": datetime}`. The output is the shape in design §7.2:
  - `attention = proposals+failed > 0 or agents.waiting > 0 or any usage window warn`;
  - `people.waiting[*].name` is the map name or slug; `nudge` uses `nudge_days` as Today does;
  - `deadlines` are open items with a `due`, sorted ascending, first 6;
  - `projects` is one entry per map project with open and next_time counts;
  - `today.top` is the first 5 of overdue + due_today.
  - It reuses `compute_today` for the sections.
- `POST /api/open` with `{target, view?}`:
  - `app["opener"]: Callable[[str], None]` defaults to `os.startfile` on win32, else `webbrowser.open`;
  - returns `{"ok": true, "url": url}`;
  - an unknown target or a bad view gives 400.
- `GET /api/widget` → `build_widget(...)` with `arm_reachable` from `app["arm"].reachable(settings["arm_url"])` and `synopsis` included.
- `GET /widget` → `web/widget.html` (FileResponse; 404 if missing).

- [ ] **Step 1: Write failing tests:**
  - `test_synopsis_example`: inputs reproducing the spec §3.1 example give exactly `"3 due today · 1 waiting on Teammate A · 5h 62% (resets 14:20) · 2 agents running"`. Use a `resets_at` local ISO `...T14:20`.
  - `test_synopsis_all_clear`, `test_synopsis_overdue_and_review_and_plural`.
  - `test_build_widget_shapes`: items of each kind, a map with person `teammate-a` named "Teammate A", 2 running agents and 1 waiting. Check the counts, `attention True`, deadlines sorted and capped at 6, and `projects` counts including next_time.
  - `test_arm_probe_reachable_and_cached`: open a listening socket with `asyncio.start_server` on a free port. It's True. Close the server: still True within the ttl (fake clock), and False after the ttl. `http://127.0.0.1:1` is False within 1.5 s.
  - API: `test_open_targets`:
    - with `app["opener"]` replaced by a list-append, `{"target": "app", "view": "agents"}` opens `http://127.0.0.1:<port>/#/agents`;
    - `{"target": "fable_usage"}` opens the settings URL; `{"target": "arm"}` opens the `arm_url`;
    - `{"target": "file:///c:/x"}` gives 400, and `{"target": "app", "view": "../x"}` gives 400.
  - `test_widget_endpoint_and_page`: GET `/api/widget` after setup gives 200 with a `synopsis` str. GET `/widget` gives 200 with `text/html` once Task 9's file exists. Until then, assert 404 (Task 9 updates the assertion).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** `widget.py` and the routes. `app["arm"] = ArmProbe()` and `app["opener"]` are set in `create_app`.
- [ ] **Step 4: Run** the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat: widget summary, open-in-browser and robot arm probe`.

---

### Task 8: Usage and Agents pages, Settings integration section

**Files:**
- Create: `web/js/views/usage.js`, `web/js/views/agents.js`, `web/js/views/agents-model.js`, `web/js/views/agents-model.test.mjs`
- Modify: `web/js/app.js` (views registry + `NAV`), `web/js/views/settings.js`, `web/style.css`

**Interfaces:**
- `agents-model.js` (pure, no DOM):
  - `STATUS_LABEL = {idle: 'ready', running: 'running', waiting: 'waiting for you', finished: 'finished'}`.
  - `sinceText(iso: string, now: Date) -> string`: `'just now'` under 60 s, `'5 min'`, `'2 h 5 min'`, `'3 d'`.
  - `nextTimeText(row) -> string|null`: `'2 next-time notes for robotarm'`, singular `'1 next-time note for robotarm'`, null for 0.
  - `resetText(w, now) -> string`: `'resets 14:20 (in 2 h 5 min)'`, or `'reset at 14:20 · no data since'` when `reset_passed`.
- Registry: `usage: () => import('./views/usage.js')` and `agents: () => import('./views/agents.js')`. `NAV` gains `['agents', 'Agents']` and `['usage', 'Usage']` before Settings.

- [ ] **Step 1: Write failing JS tests** `agents-model.test.mjs`. Cover each function with the exact strings above, including plural and singular forms and the `reset_passed` branch.
- [ ] **Step 2: Run** `node --test web/js/views/agents-model.test.mjs`. Expected: FAIL.
- [ ] **Step 3: Implement `agents-model.js`.** Run the tests. Expected: PASS.
- [ ] **Step 4: `usage.js` view.**
  - `h1` "Usage", then one block per window titled "5-hour window" or "Weekly limit":
    - big `NN%`;
    - `<meter min=0 max=100 low=warn_pct-1 high=warn_pct optimum=0 value=used>`, so no inline styles;
    - `resetText`;
    - the `.warn` class when `warn`.
  - "No data yet. It appears after your next Claude Code reply." when the window is null.
  - "updated N min ago" when `age_s > 300`.
  - A "Fable usage ↗" button that POSTs `/api/open {target: 'fable_usage'}`.
  - It refreshes on `usage.changed` and every 60 s, and returns a cleanup.
- [ ] **Step 5: `agents.js` view.**
  - A table with one row per agent: Project (with a "mapped" badge), Part, Where, Status (badge with `STATUS_LABEL`; amber `.warn` for waiting), Since (`sinceText(status_since)`), the note (muted, small), the next-time text, and ✕ (DELETE).
    - **Part:** click to edit. An inline input saves on Enter or blur, Esc cancels, and empty clears the override. PATCH, then redraw.
    - **Where:** a `<select>` of the 4 choices, plus "auto (…)" meaning null. The `title` attribute holds the raw `entrypoint`.
  - Empty state: "No Claude Code sessions seen. Install the integration in Settings, then start a session."
  - It refreshes on `agents.changed`, `ws.open` and every 30 s. It doesn't redraw while an input in the view has focus (copy the `typing()` guard pattern from `today.js`).
- [ ] **Step 6: Settings section "Claude Code integration"** (after Backup):
  - `GET /api/integration` shows a `dl`:
    - Hooks: installed/not installed (events);
    - Status line: installed/not;
    - Port matches: yes/no, with "install again" advice when no;
    - Last hook event: time or never;
    - Last usage update: time or never;
    - Settings file: path in `code`.
  - Buttons "Install" and "Remove" POST to the endpoints and redraw. Errors show inline in `.error`.
  - A muted line: "Adds ATLAS's hooks and status line to your Claude Code user settings. A backup of the file is made first."
- [ ] **Step 7: CSS.** Add `.agents-table` (full width, `border-collapse`, rows separated by `--line`, wraps on narrow screens via `@media (max-width: 760px) { display:block }`), `.meter-block`, `meter { width: 100%; height: 14px; }` and `.big-pct { font-size: 2rem; font-weight: 700; }`. Run `node --check` on each new or changed file.
- [ ] **Step 8: Manual check.**
  - Run the hub on `$env:TEMP\bh-test` (complete setup in the browser).
  - With curl/PowerShell, POST a SessionStart, UserPromptSubmit and Notification for session `s1`, with cwd `C:\Windows` and `session_title` `<script>alert(1)</script>`. Also POST a usage payload.
  - Check that the Agents page shows the row live, the script text shows literally, and rename and where work. Check that the Usage page shows the meters.
  - Stop the hub and delete the temp root.
- [ ] **Step 9: Commit** `feat: Usage and Agents pages, Claude Code integration in Settings`.

---

### Task 9: Widget page

**Files:**
- Create: `web/widget.html`, `web/widget.css`, `web/js/widget.js`, `web/js/widget-model.js`, `web/js/widget-model.test.mjs`
- Modify: `tests/test_api_widget.py` (`/widget` now 200)

**Interfaces:**
- Consumes: `GET /api/widget` (Task 7), `POST /api/entries` with `source: "widget"`, `POST /api/open`.
- `widget-model.js` (pure):
  - `TILE_LABEL = {today: 'Today', usage: 'Usage', agents: 'Agents', people: 'People', deadlines: 'Deadlines', review: 'To review', projects: 'Projects', arm: 'Robot arm'}`.
  - `TILE_VIEW = {today: 'today', usage: 'usage', agents: 'agents', people: 'people', deadlines: 'deadlines', review: 'today', projects: 'settings'}`. The arm tile opens target `arm` instead.
  - `tileSummary(name, w) -> {value: string, caption: string, warn: boolean}`:
    - today: value = overdue + due_today, caption `'due today'` (or `'due · 1 overdue'`), warn if overdue > 0;
    - usage: `'62%'` / `'5h · resets 14:20'`, warn from the window; `'—'` / `'no data yet'` when null;
    - agents: value = running, caption `'running · 1 waiting'`, warn if waiting;
    - people: value = waiting.length, caption `'waiting on others'`;
    - deadlines: value = length, caption = the first due date (`'next: 2026-10-25'`) or `'none'`;
    - review: proposals + failed, caption `'to confirm'`, warn if > 0;
    - projects: the count of projects with open items, caption `'with open items'`;
    - arm: `'on'`/`'off'`, caption `'reachable'`/`'offline'`.
  - `popupRows(name, w) -> Array<{text: string, meta: string, view?: string}>`: at most 6 rows. Projects rows carry `view: 'project/<slug>'`.
- `widget.js`:
  - Collapsed bar: `<div class="bar" data-tauri-drag-region>` holding `⋮⋮`, an attention dot, the synopsis `span` and an expand button `▾`.
  - Expanded: a capture input, the tile grid and a popup region.
  - `fitWindow()` resizes via `window.__TAURI__.window.getCurrentWindow().setSize(new window.__TAURI__.dpi.LogicalSize(520, Math.ceil(document.documentElement.scrollHeight)))` when `window.__TAURI__` exists, wrapped in try/catch.
  - Refresh rules are those of design §7.1; the WebSocket comes from `api.onEvent`.
  - Offline text: `'ATLAS offline · retrying'`.

- [ ] **Step 1: Write failing JS tests** for `tileSummary` (each tile, including null usage and the arm on/off) and `popupRows` (cap of 6, project view paths). Run them. Expected: FAIL.
- [ ] **Step 2: Implement `widget-model.js`.** Run the tests. Expected: PASS.
- [ ] **Step 3: `widget.html`.**
  - The same CSP and referrer meta as `index.html`. Links `/static/widget.css` and `/static/js/widget.js` (module).
  - `<body class="widget">` holds `<div id="w"></div>`.
  - Transparent page background (`html, body { background: transparent; margin: 0 }`), so the Tauri window's rounded panel is the only visible shape.
- [ ] **Step 4: `widget.css`.**
  - Panel `background: rgba(16,17,19,0.94); color: #f2f2f2; border: 1px solid rgba(255,255,255,0.14); border-radius: 10px; font: 13px/1.35 "Segoe UI", system-ui, sans-serif`.
  - Tile grid of 4 columns × 2 rows: tiles 116×64, value 20px bold, caption 11px `#b8bcc2`, warn tiles with an amber `#ffb020` left border.
  - Popup panel below the grid, rows 12px, "Open in ATLAS ↗" link button.
  - Focus outline `2px solid #6aa3ff`.
  - The only transition is `opacity 120ms` on expand. No blur and no shadow.
- [ ] **Step 5: `widget.js`.** Implement per the interfaces.
  - Capture: Enter saves (`source: 'widget'`), then the field clears and shows "Saved" for 2 s. On error the text stays and the error shows in red. Esc collapses.
  - Clicking a tile toggles its popup. Popup rows with `view` open that view. The footer link opens `TILE_VIEW[name]`, and the arm tile opens target `arm`.
  - Run `node --check web/js/widget.js web/js/widget-model.js`.
- [ ] **Step 6: Update `test_widget_endpoint_and_page`** to expect 200. Run the full Python suite.
- [ ] **Step 7: Manual check.**
  - Run the hub on a temp root, open `http://127.0.0.1:8090/widget` in a browser, and post a few hook and usage events.
  - Check that the collapsed synopsis updates live, the tiles and popups render, capture works and creates a `widget` entry, and offline shows when the hub is stopped.
  - Delete the temp root afterwards.
- [ ] **Step 8: Commit** `feat: desktop widget page (synopsis, tiles, popups, capture)`.

---

### Task 10: Tauri desktop shell and `desktop-config`

**Files:**
- Create: `atlas/desktop_config.py`, `tests/test_desktop_config.py`
- Modify: `atlas/__main__.py`
- Create:
  - `app/desktop/README.md`;
  - `app/desktop/dist/index.html` ("Starting ATLAS…" on the same dark panel, with no scripts);
  - `app/desktop/src-tauri/{Cargo.toml, build.rs, tauri.conf.json, capabilities/widget.json, icons/*, src/main.rs, src/lib.rs, src/supervisor.rs, src/config.rs}`;
  - `app/desktop/.gitignore` (`src-tauri/target/`, `src-tauri/gen/`).

**Interfaces:**
- Python:
  - `write_desktop_config(*, root: Path, port: int, python: Path, app_dir: Path, target: Path) -> Path` writes `{"port", "root", "python", "app_dir"}` as JSON (strings for paths).
  - `default_target() -> Path` = `Path(os.environ["LOCALAPPDATA"]) / "ATLAS" / "desktop.json"`.
  - `pythonw_for(executable: Path) -> Path` is `pythonw.exe` beside it if that exists, else the executable.
  - CLI `python -m atlas desktop-config --root R [--target PATH]` uses the settings port and `Paths(R).app` and prints the written path.
- Rust:
  - `config.rs`: `struct DesktopConfig { port: u16, root: Option<PathBuf>, python: Option<PathBuf>, app_dir: Option<PathBuf> }` and `fn load(path: &Path) -> DesktopConfig`. A missing or invalid file gives the defaults (port 8090, all None).
  - `supervisor.rs`:
    - `fn backoff(attempt: u32) -> Duration` is 1, 2, 4, 8, 16, 32 s, then 60 s, capped at 60;
    - `fn hub_up(port: u16) -> bool` is a TCP connect to `127.0.0.1:port` with a 500 ms timeout;
    - `struct Supervisor` owns `Option<std::process::Child>` and spawns `python -m atlas --root <root>` with `current_dir(app_dir)`, `creation_flags(0x08000000)` (`CREATE_NO_WINDOW`), and stdout/stderr appended to `<root>/data/hub.log`.
    - Its methods are `tick(&mut self)` (called every 2 s on a thread), `restart(&mut self)` and `stop(&mut self)` (kills only its own child).
    - The backoff attempt counter resets after 300 s of healthy uptime.
  - `lib.rs`:
    - plugins: single-instance (a second launch shows and focuses `widget`), autostart (`MacosLauncher::LaunchAgent`, no args), window-state (`StateFlags::POSITION` only);
    - it builds the window `widget` per design §8 from `WebviewUrl::App("index.html")`, and calls `window.navigate(format!("http://127.0.0.1:{port}/widget").parse()?)` the first time `hub_up` turns true, and again after the hub was down and came back;
    - the tray menu is Open ATLAS, Show/Hide widget, Start at login (`CheckMenuItem` reflecting `autolaunch().is_enabled()`), Restart hub (enabled only if the supervisor owns a child) and Quit (`supervisor.stop()` then `app.exit(0)`).
    - Opening the browser uses `std::process::Command::new("cmd").args(["/C", "start", "", url])` with `CREATE_NO_WINDOW`.
  - `tauri.conf.json`: `identifier` `"dev.atlas.desktop"`, `productName` `"ATLAS"`, `app.withGlobalTauri: true`, `app.windows: []` (built in code), `bundle.active: false`, `build.frontendDist: "../dist"`, `app.security.csp: null`.
  - `capabilities/widget.json`: `{"identifier": "widget", "windows": ["widget"], "remote": {"urls": ["http://127.0.0.1:*/*"]}, "permissions": ["core:window:allow-set-size", "core:window:allow-start-dragging"]}`.
  - Package name `atlas-desktop`, so the binary is `target/release/atlas-desktop.exe`.

- [ ] **Step 1: Python first (TDD):**
  - `test_write_desktop_config(tmp_path)`: the written JSON round-trips.
  - `test_pythonw_for`: given a temp dir with both exes, it returns pythonw; with only python.exe, it returns python.exe.
  - `test_cli_desktop_config`: a subprocess with `--target tmp/x.json` exits 0 and the file exists.
  - Run them. Expected: FAIL. Implement. Expected: PASS.
- [ ] **Step 2: Scaffold the Tauri project.**
  - Generate icons by running `npx -y @tauri-apps/cli@2 init --ci --app-name atlas-desktop --window-title ATLAS --frontend-dist ../dist --dev-url http://127.0.0.1:8090 --before-dev-command "" --before-build-command ""` in a temp dir, then copy its `src-tauri/icons/` into `app/desktop/src-tauri/icons/`.
  - Write the files above. `Cargo.toml` uses edition 2021, `tauri = { version = "2", features = ["tray-icon"] }`, the three plugins at `"2"`, and `serde`/`serde_json`, with `[lib] name = "atlas_desktop_lib"`.
  - `main.rs` keeps `#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]`.
- [ ] **Step 3: Rust unit tests** (`#[cfg(test)]`):
  - `backoff_sequence`: 0→1 s, 1→2 s, 5→32 s, 6→60 s, 20→60 s;
  - `config_missing_file_defaults`: port 8090;
  - `config_parses_file`;
  - `hub_up_false_on_closed_port`: bind a `TcpListener` to port 0, read the port, drop it, then expect false;
  - `hub_up_true_on_listener`.
  - Run `cargo test` in `app/desktop/src-tauri`. Expected: PASS.
- [ ] **Step 4: Build.** Run `cargo build --release` in `app/desktop/src-tauri`. Expected: success, and `target/release/atlas-desktop.exe` exists.
  - A spike on 2026-10-21 built a release exe of about 11 MB in 4 m 18 s from cold. It used these exact crates, an external-URL `WebviewWindowBuilder` (transparent, frameless, always-on-top), a tray menu and this capability file. Run the build in the background with a long timeout.
- [ ] **Step 5: Manual smoke** (temp root; **do not** write the real `%LOCALAPPDATA%\ATLAS\desktop.json`: pass `--target $env:TEMP\bh-desktop\desktop.json` and launch the exe with env `LOCALAPPDATA=$env:TEMP\bh-desktop`, which `config.rs` reads via `std::env::var("LOCALAPPDATA")`):
  - With no hub running, launching the exe makes the supervisor start the hub. `hub.log` appears in the temp root's `data/`, and the widget switches to `/widget`.
  - Collapse and expand resize the window, and dragging moves it.
  - Kill the hub `pythonw` process: it restarts within a few seconds.
  - Tray Quit stops both.
  - Take a screenshot (PowerShell `System.Windows.Forms` screen capture into the scratchpad) and check legibility.
  - Leave "Start at login" **unticked**; the owner ticks it. Delete the temp folders.
- [ ] **Step 6: `app/desktop/README.md`.** Cover prerequisites (Rust via rustup, VS C++ build tools, WebView2), build commands, `desktop-config`, run, autostart, logs and troubleshooting.
- [ ] **Step 7: Commit** `feat: Tauri desktop shell (widget window, tray, autostart, hub supervisor)`. `target/` must not be committed.

---

### Task 11: Real end-to-end check and docs

**Files:**
- Modify: `README.md`, `app/README.md`, `app/CLAUDE.md`
- Create: `docs/superpowers/phase2-decisions.md`

- [ ] **Step 1: E2E with the real CLI.** Use a scratch root `$env:TEMP\bh-e2e`, never `D:\ATLAS`.
  - Start the hub on port 8095, complete setup via `POST /api/setup` with a map whose project `robotarm` has folder `<scratch>\robotarm`, and create that folder with `git init` on branch `feat/wrist-servo`.
  - Install the integration into `<scratch>\claude\settings.json` via `python -m atlas integration install --root <scratch> --settings <that path>`, after setting port 8095 in the scratch settings first.
  - In `<scratch>\robotarm`, run `claude -p --model haiku --settings <that path> "Reply with just: ok"`, with env `CLAUDE_CODE_ENTRYPOINT=cli` so the `sdk-` filter doesn't drop it.
  - While it runs and after, `GET /api/agents`: a row with project `robotarm` (mapped), part `wrist-servo`, where `terminal`, status progressing to `finished`, then gone after the linger.
  - Pipe a realistic status line JSON into the status line command from the installed `statusLine.command` (with `rate_limits`). `GET /api/usage` shows it.
  - Record the timings and results in `app/README.md` "Measured".
- [ ] **Step 2: Widget e2e.** Launch `atlas-desktop.exe` against the scratch hub (temp `LOCALAPPDATA`), screenshot the collapsed and expanded widget, and save them to the scratchpad. Report the paths.
- [ ] **Step 3: Docs.**
  - `app/README.md`: a phase 2 section covering the integration install/remove (CLI and Settings), the widget and desktop shell, the new settings, the verification checklist (design §12) and updated Known limitations. Move phase 2 out of "Not in phase 1".
  - Root `README.md`: phase 2 line and doc links.
  - `app/CLAUDE.md`: phase status, a code-map rows for the new modules, the new hard rule (hook client silent and stdlib-only; never write the real Claude settings in tests), run and test commands for Rust, and where things stand.
  - `docs/superpowers/phase2-decisions.md`: every ruling taken during the build, in phase 1's format (ruling, why, cost if wrong), plus the parked residuals.
- [ ] **Step 4: Full verification:** pytest, node tests, `cargo test` and `cargo build --release` all pass. `git status` is clean after commit.
- [ ] **Step 5: Commit** `docs: phase 2 run, integration, widget and decisions`.

## Not in phase 2 (by design)
Voice and the tray's "pause notifications" (phase 4); the tablet and its AGENTS tab, LAN pairing and measurement requests (phase 3); Google Calendar (phase 5); phone (phase 6); semantic search (any time); a file watcher for owner edits (phase 1 residual).
