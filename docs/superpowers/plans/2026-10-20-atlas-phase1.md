# ATLAS Phase 1 ("The Brain") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local web app on the laptop. The owner writes freely; a headless Claude Code agent files each entry into a git-versioned Markdown brain and proposes tasks and dates on a confirm card; recall answers questions instantly from the owner's own words. It includes a setup wizard, a settings editor with a hierarchy view, a Today view and a journal.

**Architecture:**
- One Python process (aiohttp) on `127.0.0.1` serves a no-build vanilla-JS web UI, a JSON API, SSE (for recall) and a WebSocket (live events).
- `brain/` is plain Markdown in its own git repo. The verbatim raw log there is the source of truth.
- SQLite in `data/hub.db` holds entries, filings, proposals, confirmed items and an FTS5 index, all rebuildable from `brain/`.
- Claude is invoked only through the `claude` CLI (`-p`), behind a runner interface that tests replace with a fake.

**Tech Stack:**
- Python 3.14 (installed at `C:\Users\Tudor\AppData\Local\Programs\Python\Python314`);
- aiohttp, the stdlib `sqlite3` (FTS5 is available, SQLite 3.50.4), pytest, pytest-aiohttp;
- git 2.52, Claude Code CLI 2.1.304 at `C:\Users\Tudor\.local\bin\claude.exe`;
- vanilla ES-module JS and CSS (no bundler).

**Spec:** `D:\ATLAS\docs\superpowers\specs\2026-10-20-atlas-design.md` (approved 2026-10-20). Read it before starting any task.

## Global Constraints

**Runtime and secrets**
- Everything runs locally. In phase 1 the server binds **`127.0.0.1` only**; LAN access and pairing are phase 3.
- Claude runs only via the `claude` CLI on the owner's subscription. **`ANTHROPIC_API_KEY` is removed from the child environment** before every `claude` call, and `--bare` is never passed.

**The brain and its log**
- The raw log is written **verbatim, before any agent runs, append-only, never rewritten** (spec §4).
- **Nothing extracted becomes an item until the owner confirms it** (spec §5.2, §5.3). Unconfirmed proposals stay in To review.
- `brain/` is its own git repo:
  - every filing, confirm and item edit is a commit;
  - outside (owner) edits are committed as they are, never overwritten (spec §4).
- The filing agent must never modify `brain/log/` or `brain/_items/`.
- Filing writes `[[slug]]` wiki-links for projects and people (spec §13.2). It never creates folders or files for unconfirmed new projects or people (spec §4, "New projects/people").

**Recall**
- **Every answer cites its sources. With no matches, the answer is exactly "Nothing on that."** It never guesses (spec §5.4).

**Settings**
- Every tunable default lives in Settings, not in code (spec §8 last paragraph). The phase 1 settings keys are fixed in Task 1.

**Paths and ports**
- Code lives in `D:\ATLAS\app\`, data in `D:\ATLAS\brain\` (a separate git repo) and `D:\ATLAS\data\`. The repo root's `.gitignore` already ignores `brain/` and must also ignore `data/` and `app/.venv/`.
- Port **8090** (the arm server uses 8080).

**Files and UI**
- All file I/O is UTF-8. The raw log is read and written with `newline=""` so the text stays byte-for-byte as typed.
- The UI renders all user and agent text with `textContent` and never with `innerHTML` (see Review Focus).
- The visual design comes later (spec §15): phase 1 UI is plain and functional, with readable defaults. No animations.

## Review Focus

1. **Odd entry text:** Markdown headings, HTML/`<script>`, the literal entry-marker string, Romanian diacritics (ă î ș ț), emoji, CRLF line endings, or 10,000+ characters must round-trip through the raw log exactly and show as plain text in the UI. *Pinned:* Task 4 `test_roundtrip_hostile_text`, Task 12 manual check.
2. **The Claude CLI is missing, logged out, times out, or returns no structured output:** the entry stays saved, the filing shows "failed" with Retry in To review, and partial agent edits are discarded. *Pinned:* Task 7 `test_failure_marks_failed_and_discards_partial_edits`.
3. **Recall questions with FTS syntax characters** (`"`, `(`, `*`, `-`, `ID-3`, `AND`, `NEAR`, a lone `:`) must never raise. *Pinned:* Task 8 `test_fts_query_never_raises`.
4. **Relative dates** ("next Friday", "by the 14th") must resolve against the **entry's** timestamp, not the filing time, so an entry filed after a restart the next day is still correct. *Pinned:* Task 7 `test_prompt_anchors_dates_to_entry_timestamp`.
5. **Restart or shutdown mid-filing:** unfinished filings resume on start without duplicating proposals for the same entry. *Pinned:* Task 7 `test_refile_replaces_stale_proposals` and `test_requeue_unfinished`.

---

## File Structure

```
D:\ATLAS\
  .gitignore                     (modify) + data/ + app/.venv/
  app\
    pyproject.toml               deps + pytest config
    README.md                    how to run (Task 15)
    atlas\
      __init__.py
      __main__.py                CLI entry: python -m atlas --root D:\ATLAS
      config.py                  Paths, DEFAULT_SETTINGS, load/save settings
      models.py                  dataclasses: Entry, Item, Proposal, Hit, Filing
      brainmap.py                map.json model, validation, folders, hierarchy
      gitrepo.py                 git operations on brain/
      rawlog.py                  append/read verbatim log
      store.py                   SQLite: entries, filings, proposals, items, FTS; items export
      claude_runner.py           subprocess wrapper around `claude -p`
      filing.py                  filing prompt, result parsing, worker queue, corrections
      review.py                  confirm/dismiss/edit proposals and items
      recall.py                  FTS query building, quick answer, dig deeper
      today.py                   Today sections
      backup.py                  debounced git push
      server.py                  aiohttp app: routes, SSE, WebSocket
      prompts\filing.md          system prompt for filing
      prompts\recall.md          system prompt for the quick answer
      prompts\deeper.md          system prompt for dig deeper
      schemas\filing.schema.json JSON schema passed via --json-schema
    web\
      index.html  style.css
      js\api.js  js\app.js
      js\components\capture.js  js\components\confirm-card.js
      js\views\today.js  ask.js  journal.js  browse.js  settings.js  setup.js
    tests\
      conftest.py  fakes.py
      test_config.py  test_brainmap.py  test_gitrepo.py  test_rawlog.py  test_store.py
      test_claude_runner.py  test_filing.py  test_review.py  test_recall.py  test_today.py
      test_backup.py  test_server.py
  data\                          (gitignored) hub.db, settings.json
  brain\                         (separate git repo, created by the setup wizard)
    map.json  log\YYYY\MM\YYYY-MM-DD.md  general\  projects\<slug>\  uni\<course>\  personal\  people\<slug>.md  _items\
```

All commands below run from `D:\ATLAS\app` unless stated otherwise. The test command is `.venv/Scripts/python -m pytest` (it works in both PowerShell and Git Bash).

---

### Task 1: Scaffold, models, settings

**Files:**
- Create: `app/pyproject.toml`, `app/atlas/__init__.py`, `app/atlas/models.py`, `app/atlas/config.py`, `app/tests/conftest.py`, `app/tests/test_config.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces:
  - `Paths(root: Path)`, a frozen dataclass with properties `.app`, `.data`, `.brain`, `.db` (`data/hub.db`), `.settings_file` (`data/settings.json`), `.web` (`app/web`), `.prompts` (`app/atlas/prompts`), `.schemas`.
  - `DEFAULT_SETTINGS: dict`.
  - `load_settings(paths) -> dict` returns the defaults merged with the file.
  - `save_settings(paths, patch: dict) -> dict` merges, validates and writes, then returns the full settings. It raises `ValueError` on unknown keys or wrong types.
  - In `models.py`, the dataclasses `Entry`, `Item`, `Proposal`, `Hit`, `Filing` (fields below).
  - Fixture `paths` in `conftest.py`: a `Paths` over `tmp_path`, with `data/` and `brain/` created.

`models.py` fields (exact):
```python
@dataclass
class Entry:    id: str; ts: datetime; source: str; text: str          # source in {"pc","widget","tablet"}
@dataclass
class Filing:   entry_id: str; status: str; synopsis: str = ""; files: list[str] = field(default_factory=list)
                commit: str | None = None; error: str | None = None; attempts: int = 0   # status: queued|running|done|failed
@dataclass
class Proposal: id: str; entry_id: str; ptype: str; payload: dict; status: str; warnings: list[str] = field(default_factory=list)
                # ptype: item|update|node ; status: proposed|confirmed|dismissed
@dataclass
class Item:     id: str; kind: str; text: str; area: str; project: str | None; person: str | None
                due: str | None; remind_at: str | None; status: str; snooze_until: str | None
                source_entry: str; created: str; updated: str
                # kind: task|deadline|reminder|waiting|next_time ; status: open|to_read|done|dismissed|snoozed
                # due/remind_at: local ISO "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM"
@dataclass
class Hit:      kind: str; ref: str; date: str | None; snippet: str; text: str   # kind: entry|note ; ref: entry id or "path#heading"
```

`DEFAULT_SETTINGS` (exact keys and defaults):
```python
{"port": 8090, "claude_path": "claude", "filing_model": "sonnet", "recall_model": "haiku",
 "deeper_model": "sonnet", "filing_timeout_s": 300, "recall_timeout_s": 90, "deeper_timeout_s": 300,
 "recall_top_n": 12, "default_lead_days": 2, "nudge_days": 3,
 "backup_remote": None, "backup_debounce_s": 300, "quiet_hours": ["23:00", "08:00"]}
```

- [ ] **Step 1: Write `pyproject.toml`**
  - Project `atlas`, `requires-python = ">=3.12"`, dependency `aiohttp>=3.12`, and an optional `test` group with `pytest`, `pytest-aiohttp`.
  - `[tool.pytest.ini_options]` sets `asyncio_mode = "auto"` and `testpaths = ["tests"]`.
  - Add `data/` and `app/.venv/` to `D:\ATLAS\.gitignore`.
- [ ] **Step 2: Create the venv and install**

  Run: `py -3 -m venv .venv && .venv/Scripts/python -m pip install -e ".[test]"`
  Expected: it ends with `Successfully installed ... aiohttp ... pytest-aiohttp ...`.
- [ ] **Step 3: Write the failing tests** in `tests/test_config.py`

```python
def test_defaults_when_no_file(paths):
    s = load_settings(paths)
    assert s["port"] == 8090 and s["recall_model"] == "haiku" and s["default_lead_days"] == 2

def test_save_merges_and_persists(paths):
    save_settings(paths, {"nudge_days": 5})
    assert load_settings(paths)["nudge_days"] == 5
    assert load_settings(paths)["filing_model"] == "sonnet"

def test_save_rejects_unknown_key(paths):
    with pytest.raises(ValueError): save_settings(paths, {"nope": 1})

def test_save_rejects_wrong_type(paths):
    with pytest.raises(ValueError): save_settings(paths, {"nudge_days": "three"})
```
- [ ] **Step 4: Run them to verify they fail**

  Run: `.venv/Scripts/python -m pytest tests/test_config.py -v`
  Expected: FAIL with an ImportError.
- [ ] **Step 5: Implement `config.py` and `models.py`**
  - Type validation compares against the type of the default.
  - Keys whose default is `None` accept `str | None`.
  - `quiet_hours` must be a list of two `"HH:MM"` strings.
- [ ] **Step 6: Run the tests to verify they pass**

  Run: `.venv/Scripts/python -m pytest tests/test_config.py -v`
  Expected: 4 passed.
- [ ] **Step 7: Commit**

  `git add -A app .gitignore && git commit -m "feat: scaffold app, models, settings"`

---

### Task 2: Brain map

**Files:**
- Create: `app/atlas/brainmap.py`
- Test: `app/tests/test_brainmap.py`

**Interfaces:**
- Produces:
  - `@dataclass BrainMap(areas: list[dict], projects: list[dict], courses: list[dict], people: list[dict])`. Each node is `{"slug","name"}`. Projects also have `"is_company": bool` and `"folders": list[str]`; people also have `"projects": list[str]`.
  - `default_map() -> BrainMap`: areas `general`, `projects`, `uni`, `personal`, with nothing else.
  - `slugify(name: str) -> str`: lowercase, ASCII-folded (ș becomes s), with non-`[a-z0-9]` runs collapsed to `-`.
  - `validate_map(m) -> list[str]` returns human-readable errors; an empty list means valid.
  - `load_map(brain: Path) -> BrainMap | None` reads `brain/map.json`.
  - `save_map(brain, m)`: validates (raising `ValueError(errors)`), then writes pretty JSON with sorted keys.
  - `ensure_folders(brain, m)` creates `general/`, `personal/`, `uni/<course>/`, `projects/<slug>/`, `people/`, `log/` and `_items/`.
  - `add_node(m, ntype: str, name: str, parent: str | None) -> BrainMap`, where `ntype` is one of project|course|person|area. It returns a new map and raises `ValueError` on a duplicate slug.
  - `hierarchy(brain, m, item_counts: dict[str, int]) -> dict`, a tree `{"name","slug","kind","files": int,"items": int,"children": [...]}` rooted at "brain". `files` is the `.md` count under the node's folder.

- [ ] **Step 1: Write the failing tests**

```python
def test_slugify():
    assert slugify("Robot Arm") == "robot-arm"
    assert slugify("Proiectare Ș.I.") == "proiectare-s-i"

def test_validate_rejects_duplicate_and_bad_slugs():
    m = default_map(); m.projects = [{"slug":"x","name":"X","is_company":False,"folders":[]}]*2
    assert any("duplicate" in e for e in validate_map(m))
    m.projects = [{"slug":"Bad Slug","name":"B","is_company":False,"folders":[]}]
    assert validate_map(m)

def test_validate_at_most_one_company():
    m = default_map()
    m.projects = [{"slug":s,"name":s,"is_company":True,"folders":[]} for s in ("a","b")]
    assert any("company" in e for e in validate_map(m))

def test_person_must_reference_known_projects():
    m = default_map(); m.people = [{"slug":"ana","name":"Ana","projects":["ghost"]}]
    assert validate_map(m)

def test_save_load_roundtrip_and_folders(tmp_path):
    m = add_node(default_map(), "project", "Robot Arm", None)
    save_map(tmp_path, m); ensure_folders(tmp_path, m)
    assert load_map(tmp_path) == m
    assert (tmp_path/"projects"/"robot-arm").is_dir() and (tmp_path/"_items").is_dir()

def test_add_node_duplicate_raises():
    m = add_node(default_map(), "course", "Fizica", None)
    with pytest.raises(ValueError): add_node(m, "course", "Fizica", None)

def test_hierarchy_counts(tmp_path):
    m = add_node(default_map(), "project", "robotarm", None); ensure_folders(tmp_path, m)
    (tmp_path/"projects"/"robotarm"/"notes.md").write_text("x", encoding="utf-8")
    tree = hierarchy(tmp_path, m, {"robotarm": 3})
    node = next(c for c in tree["children"] if c["slug"]=="projects")["children"][0]
    assert node["files"] == 1 and node["items"] == 3
```
- [ ] **Step 2: Run them to verify they fail**

  Run: `.venv/Scripts/python -m pytest tests/test_brainmap.py -v`
  Expected: FAIL with an ImportError.
- [ ] **Step 3: Implement `brainmap.py`**

  `slugify` uses `unicodedata.normalize("NFKD")` and drops combining marks.
- [ ] **Step 4: Run the tests to verify they pass**

  Run: `.venv/Scripts/python -m pytest tests/test_brainmap.py -v`
  Expected: 7 passed.
- [ ] **Step 5: Commit**

  `git commit -am "feat: brain map model, validation, hierarchy"` (add the new files first).

---

### Task 3: Brain git repo

**Files:**
- Create: `app/atlas/gitrepo.py`
- Test: `app/tests/test_gitrepo.py`

**Interfaces:**
- Produces: `BrainRepo(path: Path)` with these methods:
  - `init()`: `git init` if `.git` is absent. It sets the repo-local `user.name=ATLAS`, `user.email=atlas@localhost` and `core.autocrlf=false`.
  - `is_dirty() -> bool`.
  - `changed_paths() -> list[str]`: porcelain paths, including untracked ones, with forward slashes.
  - `commit_all(message: str) -> str | None`: runs `add -A` and commit, and returns the new HEAD sha, or `None` if there was nothing to commit.
  - `head() -> str | None`.
  - `revert(sha: str) -> bool`: `git revert --no-edit`, returning False after `revert --abort` on conflict.
  - `restore_paths(paths: list[str])`: `git checkout HEAD -- <paths>`, then removes untracked files under those paths.
  - `discard_changes()`: `git reset --hard HEAD && git clean -fd`.
  - `push(remote_url: str) -> tuple[bool, str]`: `git push <url> HEAD:refs/heads/main`, returning (ok, stderr tail).
- All git calls run through one helper using `subprocess.run([...], cwd=path, capture_output=True, text=True, encoding="utf-8")`.

- [ ] **Step 1: Write the failing tests**

```python
def test_init_commit_head(tmp_path):
    r = BrainRepo(tmp_path); r.init()
    (tmp_path/"a.md").write_text("x", encoding="utf-8")
    assert r.is_dirty() and r.changed_paths() == ["a.md"]
    sha = r.commit_all("first"); assert sha and r.head() == sha
    assert r.commit_all("nothing") is None

def test_revert_head(tmp_path):
    r = BrainRepo(tmp_path); r.init()
    (tmp_path/"a.md").write_text("1", encoding="utf-8"); r.commit_all("one")
    (tmp_path/"a.md").write_text("2", encoding="utf-8"); sha = r.commit_all("two")
    assert r.revert(sha) and (tmp_path/"a.md").read_text(encoding="utf-8") == "1"

def test_restore_paths_and_discard(tmp_path):
    r = BrainRepo(tmp_path); r.init()
    (tmp_path/"log").mkdir(); (tmp_path/"log"/"x.md").write_text("orig", encoding="utf-8"); r.commit_all("base")
    (tmp_path/"log"/"x.md").write_text("tampered", encoding="utf-8"); (tmp_path/"log"/"new.md").write_text("n", encoding="utf-8")
    r.restore_paths(["log"])
    assert (tmp_path/"log"/"x.md").read_text(encoding="utf-8") == "orig" and not (tmp_path/"log"/"new.md").exists()
    (tmp_path/"b.md").write_text("junk", encoding="utf-8"); r.discard_changes(); assert not r.is_dirty()

def test_push_to_bare_remote(tmp_path):
    remote = tmp_path/"remote.git"; subprocess.run(["git","init","--bare",str(remote)], check=True)
    r = BrainRepo(tmp_path/"b"); (tmp_path/"b").mkdir(); r.init()
    (tmp_path/"b"/"a.md").write_text("x", encoding="utf-8"); r.commit_all("c")
    ok, _ = r.push(str(remote)); assert ok
```
- [ ] **Step 2: Run them to verify they fail**

  Run: `.venv/Scripts/python -m pytest tests/test_gitrepo.py -v`
  Expected: FAIL with an ImportError.
- [ ] **Step 3: Implement `gitrepo.py`**
- [ ] **Step 4: Run the tests to verify they pass**

  Run: `.venv/Scripts/python -m pytest tests/test_gitrepo.py -v`
  Expected: 4 passed.
- [ ] **Step 5: Commit**

  `git add -A app && git commit -m "feat: brain git repo operations"`

---

### Task 4: Raw log

**Files:**
- Create: `app/atlas/rawlog.py`
- Test: `app/tests/test_rawlog.py`

**Interfaces:**
- Consumes: `Entry` (Task 1).
- Produces:
  - `new_entry_id(ts: datetime) -> str`, formatted `e-YYYYMMDD-HHMMSS-xxxx` (4 lowercase hex from `secrets`).
  - `append_entry(brain: Path, text: str, source: str, now: datetime | None = None) -> Entry`. It raises `ValueError` on blank text or an unknown source.
  - `read_log(brain: Path) -> list[Entry]`: every entry, in file order, sorted by file date.
  - `log_path(brain, ts) -> Path` returns `brain/log/YYYY/MM/YYYY-MM-DD.md`.
- Timestamps are local naive, `datetime.now().replace(microsecond=0)`.

On-disk format (exact; `len` is `len(text)` in Python characters, so the parser never has to guess where the text ends):
```
<!-- entry id=e-20261020-140322-ab12 ts=2026-10-20T14:03:22 source=pc len=57 -->
<text exactly as given>
<!-- /entry -->

```
Read and write with `open(..., encoding="utf-8", newline="")` in append mode.

- [ ] **Step 1: Write the failing tests**

```python
HOSTILE = ("## Heading\r\n<script>alert(1)</script>\n<!-- /entry -->\n"
           "<!-- entry id=e-x ts=x source=pc len=1 -->\nșțăî 🤖\n" + "z" * 10000)

def test_append_and_read(tmp_path):
    e = append_entry(tmp_path, "buy present before the 14th", "pc", datetime(2026,10,20,14,3,22))
    assert re.fullmatch(r"e-20261020-140322-[0-9a-f]{4}", e.id)
    assert log_path(tmp_path, e.ts).exists()
    assert read_log(tmp_path) == [e]

def test_roundtrip_hostile_text(tmp_path):
    e = append_entry(tmp_path, HOSTILE, "pc")
    append_entry(tmp_path, "second", "widget")
    got = read_log(tmp_path)
    assert got[0].text == HOSTILE and got[1].text == "second"

def test_rejects_blank_and_bad_source(tmp_path):
    with pytest.raises(ValueError): append_entry(tmp_path, "   \n", "pc")
    with pytest.raises(ValueError): append_entry(tmp_path, "x", "fax")

def test_append_only(tmp_path):
    a = append_entry(tmp_path, "one", "pc", datetime(2026,10,20,9,0,0))
    before = log_path(tmp_path, a.ts).read_bytes()
    append_entry(tmp_path, "two", "pc", datetime(2026,10,20,10,0,0))
    assert log_path(tmp_path, a.ts).read_bytes().startswith(before)
```
- [ ] **Step 2: Run them to verify they fail**

  Run: `.venv/Scripts/python -m pytest tests/test_rawlog.py -v`
  Expected: FAIL with an ImportError.
- [ ] **Step 3: Implement `rawlog.py`**

  The parser reads the header with a regex anchored at the start of the line, then takes exactly `len` characters after the header's newline, then expects `\n<!-- /entry -->\n`.
- [ ] **Step 4: Run the tests to verify they pass**

  Run: `.venv/Scripts/python -m pytest tests/test_rawlog.py -v`
  Expected: 4 passed.
- [ ] **Step 5: Commit**

  `git add -A app && git commit -m "feat: verbatim append-only raw log"`

---

### Task 5: Store (SQLite, FTS, items export)

**Files:**
- Create: `app/atlas/store.py`
- Test: `app/tests/test_store.py`

**Interfaces:**
- Consumes: the models (Task 1) and `read_log` (Task 4).
- Produces: `Store(db_path: Path)`. It opens with `check_same_thread=False` and WAL mode, and `init_schema()` is idempotent. Methods:
  - **Entries:** `add_entry(e: Entry)`, `get_entry(id) -> Entry | None`, `list_entries(day: str | None = None, before: str | None = None, limit: int = 200) -> list[Entry]` (newest first).
  - **Filings:** `upsert_filing(f: Filing)`, `get_filing(entry_id) -> Filing | None`, `unfinished_filings() -> list[str]`. The last returns entry ids whose filing is `queued` or `running`, plus non-tablet entries with no filing row.
  - **Proposals:**
    - `add_proposals(entry_id, ptype_payloads: list[tuple[str, dict, list[str]]]) -> list[Proposal]`;
    - `list_proposals(status="proposed", entry_id=None) -> list[Proposal]`;
    - `update_proposal_payload(id, patch: dict) -> Proposal`;
    - `set_proposal_status(ids: list[str], status: str)`;
    - `delete_proposals(entry_id, status="proposed")`.
  - **Items:**
    - `create_item(**fields) -> Item`: the id is `i-` plus 8 hex, `created`/`updated` are ISO now, and the status defaults to `open`;
    - `update_item(id, patch: dict) -> Item`: raises `KeyError` if missing, and only Item fields are allowed;
    - `get_item(id)`;
    - `list_items(status: str | list[str] | None = None, kind=None, project=None, person=None, area=None) -> list[Item]`.
  - **FTS:** one FTS5 table, `fts(kind UNINDEXED, ref UNINDEXED, date UNINDEXED, body, tokenize='unicode61 remove_diacritics 2')`.
    - `index_entry(e)`;
    - `index_note_file(brain: Path, rel: str)`: replaces that file's rows, with one row per `#`-heading section and ref `rel#Heading`, or `rel` for text before the first heading;
    - `reindex_all(brain: Path)`: rebuilds from `read_log` plus every `.md` outside `log/` and `_items/`;
    - `search(fts_query: str, limit: int) -> list[Hit]`: bm25 order, with a `snippet()` of 12 tokens wrapped in `[` `]`.
  - **Export and restore:**
    - `export_items(brain: Path)` writes `brain/_items/items.json` (all items, sorted by id, `indent=2`, `ensure_ascii=False`) and one `brain/_items/<group>.md` per group. The group is the project slug if set, otherwise the area. Each is a Markdown checklist of open/to_read items with due dates and `(from e-…)`.
    - `restore_items_if_empty(brain) -> int` imports `items.json` if the items table is empty.

- [ ] **Step 1: Write the failing tests**

```python
def test_entries_and_unfinished(store):
    e = Entry("e-20261020-140322-ab12", datetime(2026,10,20,14,3,22), "pc", "hello")
    t = Entry("e-20261020-140400-cd34", datetime(2026,10,20,14,4,0), "tablet", "412 mm")
    store.add_entry(e); store.add_entry(t)
    assert store.unfinished_filings() == [e.id]          # tablet entries are never filed
    store.upsert_filing(Filing(e.id, "done")); assert store.unfinished_filings() == []

def test_proposals_lifecycle(store):
    [p] = store.add_proposals("e-1", [("item", {"kind":"task","text":"x","area":"general"}, [])])
    store.update_proposal_payload(p.id, {"text":"y"})
    assert store.list_proposals()[0].payload["text"] == "y"
    store.set_proposal_status([p.id], "confirmed"); assert store.list_proposals() == []
    store.add_proposals("e-1", [("item", {"kind":"task","text":"z","area":"general"}, [])])
    store.delete_proposals("e-1"); assert store.list_proposals() == []
    assert store.list_proposals(status="confirmed")[0].id == p.id

def test_items_crud_and_filters(store):
    i = store.create_item(kind="task", text="check backlash", area="projects", project="robotarm",
                          person=None, due=None, remind_at=None, source_entry="e-1")
    assert i.status == "open" and i.id.startswith("i-")
    store.update_item(i.id, {"status": "done"})
    assert store.list_items(status="open") == [] and store.list_items(project="robotarm")[0].status == "done"
    with pytest.raises(KeyError): store.update_item("i-missing", {"status": "done"})

def test_fts_search_entry_and_note(store, tmp_path):
    store.add_entry(Entry("e-20261020-140322-ab12", datetime(2026,10,20,14,3,22), "pc", "the wrist servo buzzes"))
    store.index_entry(store.get_entry("e-20261020-140322-ab12"))
    (tmp_path/"projects").mkdir(); (tmp_path/"projects"/"n.md").write_text("# Wrist\nservo backlash 2mm\n", encoding="utf-8")
    store.index_note_file(tmp_path, "projects/n.md")
    hits = store.search('"servo"', 10)
    assert {h.kind for h in hits} == {"entry", "note"} and any(h.ref == "projects/n.md#Wrist" for h in hits)

def test_diacritics_insensitive(store):
    e = Entry("e-20261020-150000-aaaa", datetime(2026,10,20,15,0,0), "pc", "temă la fizică")
    store.add_entry(e); store.index_entry(e)
    assert store.search('"fizica"', 5)

def test_export_and_restore(store, tmp_path):
    store.create_item(kind="deadline", text="homework", area="uni", project=None, person=None,
                      due="2026-11-14", remind_at="2026-11-12", source_entry="e-1")
    store.export_items(tmp_path)
    assert "homework" in (tmp_path/"_items"/"uni.md").read_text(encoding="utf-8")
    fresh = Store(tmp_path/"fresh.db"); fresh.init_schema()
    assert fresh.restore_items_if_empty(tmp_path) == 1
```
  Add a `store` fixture to `conftest.py`: `Store(paths.db)` with `init_schema()` run.
- [ ] **Step 2: Run them to verify they fail**

  Run: `.venv/Scripts/python -m pytest tests/test_store.py -v`
  Expected: FAIL.
- [ ] **Step 3: Implement `store.py`**
  - Storage: `proposals.payload` and `warnings`, and `filings.files`, are JSON text.
  - Locking: every method takes a `threading.Lock`, because the server calls the store from executor threads.
- [ ] **Step 4: Run the tests to verify they pass**

  Run: `.venv/Scripts/python -m pytest tests/test_store.py -v`
  Expected: 6 passed.
- [ ] **Step 5: Commit**

  `git add -A app && git commit -m "feat: sqlite store with fts and items export"`

---

### Task 6: Claude runner

**Files:**
- Create: `app/atlas/claude_runner.py`, `app/tests/fakes.py`
- Test: `app/tests/test_claude_runner.py`

**Interfaces:**
- Produces:
  - `class ClaudeError(Exception)`.
  - `class ClaudeRunner(claude_path: str)`, where `claude_path` is resolved with `shutil.which`. If resolution fails, calls raise `ClaudeError("claude CLI not found")`.
  - `async run_json(prompt, *, cwd: Path, model: str, system_prompt_file: Path, allowed_tools: list[str], json_schema: dict, max_turns: int, timeout_s: int) -> dict`. It returns the structured output object and raises `ClaudeError` on non-zero exit, timeout (after killing the process), unparsable output or missing structured output.
  - `async stream_text(prompt, *, cwd, model, system_prompt_file, allowed_tools: list[str], max_turns: int, timeout_s: int) -> AsyncIterator[str]`. It yields assistant text deltas from `--output-format stream-json --verbose --include-partial-messages`.
  - `build_args(...) -> list[str]` is exposed for testing.
  - `child_env() -> dict` returns a copy of `os.environ` without `ANTHROPIC_API_KEY`.
  - In `tests/fakes.py`: `FakeRunner(json_result=None, stream_chunks=(), on_run=None, raise_error=None)`. It implements both methods and records calls in `.calls`, each as a dict of `prompt` plus every keyword argument. It calls `on_run(cwd)` first, so tests can simulate agent file edits, and then raises `raise_error` if one is set. Otherwise it returns `json_result` or yields `stream_chunks`.
- Arguments: `-p`, `--model`, `--append-system-prompt-file`, `--max-turns`, `--allowedTools` (one value per tool; omitted when the list is empty), and `--permission-mode acceptEdits` when any write tool is allowed. Plus `--output-format json --json-schema <compact json>` for `run_json`. **The prompt is written to stdin**, never to argv, because of the Windows command-line length limit.

- [ ] **Step 1: Write the failing tests**

```python
def test_child_env_strips_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x"); assert "ANTHROPIC_API_KEY" not in child_env()

def test_build_args_json():
    a = build_args(model="sonnet", system_prompt_file=Path("p.md"), allowed_tools=["Read","Edit"],
                   max_turns=20, json_schema={"type":"object"}, stream=False)
    assert a[:1] == ["-p"] and "--json-schema" in a and "--bare" not in a
    assert a[a.index("--permission-mode")+1] == "acceptEdits" and a[a.index("--model")+1] == "sonnet"

def test_build_args_no_tools_no_permission_mode():
    a = build_args(model="haiku", system_prompt_file=Path("p.md"), allowed_tools=[], max_turns=1,
                   json_schema=None, stream=True)
    assert "--allowedTools" not in a and "--permission-mode" not in a and "stream-json" in a

async def test_missing_cli_raises(tmp_path):
    r = ClaudeRunner("definitely-not-claude-xyz")
    with pytest.raises(ClaudeError): await r.run_json("hi", cwd=tmp_path, model="haiku",
        system_prompt_file=tmp_path/"p.md", allowed_tools=[], json_schema={}, max_turns=1, timeout_s=5)
```
- [ ] **Step 2: Run them to verify they fail**

  Run: `.venv/Scripts/python -m pytest tests/test_claude_runner.py -v`
  Expected: FAIL.
- [ ] **Step 3: Pin the real output shape with a smoke check** (manual, no test file)

  Run it from `D:\ATLAS\app`:
  ```
  echo "Return the number 7." | claude -p --model haiku --max-turns 2 --output-format json --json-schema "{\"type\":\"object\",\"properties\":{\"n\":{\"type\":\"integer\"}},\"required\":[\"n\"]}"
  ```
  Note the top-level key that holds `{"n": 7}` (expected: `structured_output`). Then run the same prompt with `--output-format stream-json --verbose --include-partial-messages` and note which event type carries the text deltas.
  - Write both findings in a module docstring in `claude_runner.py`.
  - If a flag is rejected, adapt `build_args` to what `claude --help` documents and record the change.
- [ ] **Step 4: Implement `claude_runner.py`** using `asyncio.create_subprocess_exec` with `env=child_env()` and `cwd=cwd`.
- [ ] **Step 5: Run the tests to verify they pass**

  Run: `.venv/Scripts/python -m pytest tests/test_claude_runner.py -v`
  Expected: 4 passed.
- [ ] **Step 6: Commit**

  `git add -A app && git commit -m "feat: claude CLI runner"`

---

### Task 7: Filing (prompt, parsing, worker, corrections)

**Files:**
- Create: `app/atlas/filing.py`, `app/atlas/prompts/filing.md`, `app/atlas/schemas/filing.schema.json`
- Test: `app/tests/test_filing.py`

**Interfaces:**
- Consumes:
  - `Store` (Task 5);
  - `BrainRepo` (Task 3);
  - `ClaudeRunner`/`FakeRunner` (Task 6);
  - `load_map` (Task 2);
  - `read_log`/`Entry` (Task 4);
  - `load_settings` (Task 1).
- Produces:
  - `FILING_SCHEMA: dict`, loaded from `schemas/filing.schema.json` (below).
  - `build_filing_prompt(entry: Entry, m: BrainMap, open_items: list[Item], correction: str | None = None, previous: dict | None = None) -> str`.
  - `parse_filing_result(raw: dict, m: BrainMap, open_item_ids: set[str]) -> tuple[str, list[str], list[tuple[str, dict, list[str]]]]`. It returns `(synopsis, files, proposals)`, where proposals are `(ptype, payload, warnings)`.
  - `class FilingWorker(paths, store, repo, runner, broadcast: Callable[[str, dict], Awaitable[None]], on_commit: Callable[[], None])`, with these methods:
    - `queue: asyncio.Queue` (a public attribute);
    - `enqueue(entry_id)`: sets the filing to `queued` and puts the id on the queue;
    - `async run_forever()`: one filing at a time, from an `asyncio.Queue`;
    - `async file_entry(entry_id, correction: str | None = None)`;
    - `async correct(entry_id, text)`;
    - `requeue_unfinished()`: sets `running` back to `queued` and enqueues everything from `store.unfinished_filings()`.

`schemas/filing.schema.json` (exact):
```json
{"type":"object","additionalProperties":false,"required":["synopsis","files","items","updates","new_nodes"],
 "properties":{
  "synopsis":{"type":"string"},
  "files":{"type":"array","items":{"type":"string"}},
  "items":{"type":"array","items":{"type":"object","additionalProperties":false,"required":["kind","text","area"],
    "properties":{"kind":{"enum":["task","deadline","reminder","waiting","next_time"]},"text":{"type":"string"},
      "area":{"type":"string"},"project":{"type":["string","null"]},"person":{"type":["string","null"]},
      "due":{"type":["string","null"]},"remind_at":{"type":["string","null"]}}}},
  "updates":{"type":"array","items":{"type":"object","additionalProperties":false,"required":["item_id","status"],
    "properties":{"item_id":{"type":"string"},"status":{"enum":["to_read","done"]},"note":{"type":"string"}}}},
  "new_nodes":{"type":"array","items":{"type":"object","additionalProperties":false,"required":["type","name"],
    "properties":{"type":{"enum":["project","course","person","area"]},"name":{"type":"string"},
      "parent":{"type":["string","null"]}}}}}}
```

**Prompt content.** `build_filing_prompt` produces a user prompt containing, in order:
- the entry id;
- the entry timestamp, as `Entry written: 2026-10-20 14:03 (Tuesday)`;
- the line `Resolve all relative dates against the entry timestamp above, not today.`;
- the brain map as JSON;
- the open items as `- <id> [<kind>] <text> (project, person, due)`;
- the correction and the previous result, when given;
- the verbatim entry text, between `<<<ENTRY` and `ENTRY>>>`.

`prompts/filing.md` (the system prompt) must state these rules, in the author's words:
1. Only edit files inside the brain's existing structure (`general/`, `projects/<known slug>/`, `uni/<known course>/`, `personal/`, `people/<known slug>.md`). Never touch `log/`, `_items/` or `map.json`.
2. Append or merge. Never delete or rewrite the owner's existing text.
3. Every addition ends with `(from <entry id>)`.
4. Write `[[slug]]` wiki-links for every project and person mentioned.
5. Content about an unknown project, course or person goes in `general/` and gets proposed in `new_nodes`. Never create its folder.
6. Propose items only for things that are actionable or dated. `next_time` is for "check X next time on <project>"; `waiting` is for something delegated to a person.
7. Use `updates` only with item ids from the open-items list. A teammate's update that arrived makes `to_read`.
8. Dates are local ISO: `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM`. Leave `remind_at` null unless the owner said when to be reminded.
9. `files` lists every file you changed. `synopsis` is 1–2 plain sentences.

**`parse_filing_result` rules.** It never silently drops anything; problems become warnings:
- An item whose `project` or `person` isn't in the map **and** isn't in `new_nodes` keeps the text, sets that field to `None`, and adds the warning `unknown project 'x'` (or person).
- A bad date sets the field to `None` with the warning `could not read date 'x'`.
- An unknown area becomes `general` with a warning.
- An update whose `item_id` isn't open is turned into an item-less warning proposal, `("update", {...}, ["unknown item id"])`.

**`file_entry` sequence:**
1. If `repo.is_dirty()`, run `commit_all("owner edits")` and call `on_commit()`.
2. `store.delete_proposals(entry_id)`, so a re-run never duplicates proposals.
3. Set the filing to `running` and increment `attempts`.
4. Call `runner.run_json`:
   - `cwd = brain`;
   - `allowed_tools = ["Read","Write","Edit","Glob","Grep"]`;
   - `max_turns = 25`;
   - model and timeout from settings.
5. If `changed_paths()` touch `log/`, `_items/` or `map.json`, call `restore_paths` on them and add the warning `agent tried to edit protected files` to the first proposal.
6. Run `commit_all(f"file {entry_id}: {synopsis[:60]}")`, then `on_commit()`, then reindex the changed `.md` files.
7. Add the proposals and set the filing to `done` with `files` and `commit`.
8. Broadcast `filing.done {entry_id}`.

On any exception: `discard_changes()`, set the filing to `failed` with `error=str(exc)[:500]`, and broadcast `filing.failed`.

**`correct`:**
- If the filing's commit is `repo.head()`, call `repo.revert(commit)` and then `on_commit()`.
- Otherwise, pass the correction with the instruction `Fix the files in place; earlier text from this entry may already exist.`
- Then call `file_entry(entry_id, correction=text)` with `previous` set to the previous synopsis and proposals.

- [ ] **Step 1: Write the failing tests** (use `FakeRunner`, `tmp_path` brain with `default_map()` + project `robotarm` + person `ana`, real `BrainRepo`, real `Store`)

```python
def test_prompt_anchors_dates_to_entry_timestamp(m):
    e = Entry("e-20261020-140322-ab12", datetime(2026,10,20,14,3,22), "pc", "buy present by next friday")
    p = build_filing_prompt(e, m, [])
    assert "Entry written: 2026-10-20 14:03 (Tuesday)" in p and "not today" in p
    assert "<<<ENTRY\nbuy present by next friday\nENTRY>>>" in p

def test_parse_flags_unknown_project_and_bad_date(m):
    raw = {"synopsis":"s","files":["general/notes.md"],"new_nodes":[],"updates":[],
           "items":[{"kind":"task","text":"t","area":"projects","project":"ghost","person":None,"due":"soonish","remind_at":None}]}
    _, _, props = parse_filing_result(raw, m, set())
    ptype, payload, warnings = props[0]
    assert payload["project"] is None and payload["due"] is None
    assert any("ghost" in w for w in warnings) and any("soonish" in w for w in warnings)

def test_parse_accepts_project_proposed_in_new_nodes(m):
    raw = {"synopsis":"s","files":[],"updates":[],"new_nodes":[{"type":"project","name":"Vibedine","parent":None}],
           "items":[{"kind":"task","text":"t","area":"projects","project":"vibedine","person":None,"due":None,"remind_at":None}]}
    _, _, props = parse_filing_result(raw, m, set())
    assert ("node" in [p[0] for p in props]) and props[0][1]["project"] == "vibedine"

async def test_file_entry_happy_path(env):           # env: paths, store, repo, worker factory, saved entry
    def edit(cwd): (cwd/"projects"/"robotarm"/"notes.md").write_text("wrist check (from e-1) [[robotarm]]", encoding="utf-8")
    worker = env.worker(FakeRunner(json_result=GOOD_RESULT, on_run=edit))
    await worker.file_entry(env.entry.id)
    f = env.store.get_filing(env.entry.id)
    assert f.status == "done" and f.commit == env.repo.head() and not env.repo.is_dirty()
    assert [p.ptype for p in env.store.list_proposals(entry_id=env.entry.id)] == ["item"]
    assert env.store.search('"wrist"', 5)

async def test_protected_files_restored(env):
    def tamper(cwd): (cwd/"log").mkdir(exist_ok=True); (cwd/"log"/"evil.md").write_text("x", encoding="utf-8")
    await env.worker(FakeRunner(json_result=GOOD_RESULT, on_run=tamper)).file_entry(env.entry.id)
    assert not (env.paths.brain/"log"/"evil.md").exists()
    assert any("protected" in w for p in env.store.list_proposals() for w in p.warnings)

async def test_failure_marks_failed_and_discards_partial_edits(env):
    def partial(cwd): (cwd/"general"/"half.md").write_text("half", encoding="utf-8")
    await env.worker(FakeRunner(raise_error=ClaudeError("timeout"), on_run=partial)).file_entry(env.entry.id)
    f = env.store.get_filing(env.entry.id)
    assert f.status == "failed" and "timeout" in f.error
    assert not (env.paths.brain/"general"/"half.md").exists()
    assert env.store.get_entry(env.entry.id).text == env.entry.text

async def test_owner_edits_committed_first(env):
    (env.paths.brain/"personal"/"mine.md").write_text("my own words", encoding="utf-8")
    await env.worker(FakeRunner(json_result=GOOD_RESULT)).file_entry(env.entry.id)
    assert (env.paths.brain/"personal"/"mine.md").read_text(encoding="utf-8") == "my own words"
    assert "owner edits" in subprocess.run(["git","log","--format=%s"], cwd=env.paths.brain, capture_output=True, text=True).stdout

async def test_refile_replaces_stale_proposals(env):
    w = env.worker(FakeRunner(json_result=GOOD_RESULT))
    await w.file_entry(env.entry.id); await w.file_entry(env.entry.id)
    assert len(env.store.list_proposals(entry_id=env.entry.id)) == 1

def test_requeue_unfinished(env):
    env.store.upsert_filing(Filing(env.entry.id, "running"))
    w = env.worker(FakeRunner()); w.requeue_unfinished()
    assert env.store.get_filing(env.entry.id).status == "queued" and w.queue.qsize() == 1

async def test_correct_reverts_head_and_refiles(env):
    def edit(cwd): (cwd/"personal"/"gift.md").write_text("present by 14th (from e-1)", encoding="utf-8")
    runner = FakeRunner(json_result=GOOD_RESULT, on_run=edit); w = env.worker(runner)
    await w.file_entry(env.entry.id); first = env.repo.head()
    await w.correct(env.entry.id, "the present is for the 12th")
    assert env.repo.head() != first and "the 12th" in runner.calls[-1]["prompt"]
```
  Put the `m` and `env` fixtures and `GOOD_RESULT` at the top of `test_filing.py`. `GOOD_RESULT` contains one `task` item for project `robotarm`.
- [ ] **Step 2: Run them to verify they fail**

  Run: `.venv/Scripts/python -m pytest tests/test_filing.py -v`
  Expected: FAIL.
- [ ] **Step 3: Write `schemas/filing.schema.json` (exact, above) and `prompts/filing.md` (rules 1–9)**
- [ ] **Step 4: Implement `filing.py`**
  - Store and git calls are synchronous. Wrap them in `asyncio.to_thread` inside the worker.
  - Date validation uses `date.fromisoformat` / `datetime.fromisoformat` on the two allowed shapes only.
- [ ] **Step 5: Run the tests to verify they pass**

  Run: `.venv/Scripts/python -m pytest tests/test_filing.py -v`
  Expected: 10 passed.
- [ ] **Step 6: Commit**

  `git add -A app && git commit -m "feat: filing agent worker with corrections"`

---

### Task 8: Recall

**Files:**
- Create: `app/atlas/recall.py`, `app/atlas/prompts/recall.md`, `app/atlas/prompts/deeper.md`
- Test: `app/tests/test_recall.py`

**Interfaces:**
- Consumes: `Store.search` (Task 5) and `ClaudeRunner.stream_text` (Task 6).
- Produces:
  - `NOTHING = "Nothing on that."`
  - `build_fts_query(question: str) -> str | None`:
    - extracts tokens with `re.findall(r"\w+", q.lower())`;
    - drops tokens shorter than 2 characters and those in a small English stopword set (what, did, i, say, about, the, a, an, of, on, to, is, was, for, in, my, me, we, it, and, or, when, where, how, why, which, who);
    - double-quotes each token, so FTS operators become plain words;
    - joins with ` OR `;
    - returns `None` if nothing is left.
  - `search_hits(store, question, limit) -> list[Hit]`.
  - `build_quick_prompt(question: str, hits: list[Hit]) -> str`: each hit as `[<ref>] (<date>) <text>`, then the question.
  - `async quick_answer(runner, question, hits, settings, prompts_dir: Path, cwd: Path) -> AsyncIterator[str]`:
    - `stream_text` with `allowed_tools=[]`, `max_turns=1`, model `recall_model`, timeout `recall_timeout_s`;
    - if `hits` is empty, it yields `NOTHING` without calling the runner.
  - `async deeper_answer(runner, question, settings, prompts_dir, brain: Path) -> AsyncIterator[str]`:
    - tools `["Read","Glob","Grep"]`, `max_turns=15`, model `deeper_model`, `cwd=brain`.

`prompts/recall.md` and `prompts/deeper.md` must say:
- answer only from the provided sources (or, for deeper, the brain files);
- quote the owner's words;
- cite every claim with its bracketed ref exactly as given, e.g. `[e-20261020-140322-ab12]` or `[projects/robotarm/notes.md#Wrist]`;
- if the sources don't answer it, reply exactly `Nothing on that.`;
- never infer beyond what is written.

`deeper.md` additionally says: search `log/` and the notes, ignore `_items/`, and cite raw log entries by their entry id.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("q", ['what about "ID-3 (base)?', "AND OR NEAR", ":", "*", "-", 'x"y', "ID-3 → ID-7"])
def test_fts_query_never_raises(store, q):
    fq = build_fts_query(q)
    if fq: store.search(fq, 5)            # must not raise sqlite3.OperationalError

def test_query_drops_stopwords():
    assert build_fts_query("what did I say about the wrist servo?") == '"wrist" OR "servo"'
    assert build_fts_query("what is it?") is None

async def test_no_hits_yields_nothing_without_calling_claude(tmp_path):
    r = FakeRunner(stream_chunks=["should not appear"])
    out = [c async for c in quick_answer(r, "q", [], DEFAULT_SETTINGS, PROMPTS, tmp_path)]
    assert out == [NOTHING] and r.calls == []

async def test_quick_answer_uses_no_tools_one_turn(tmp_path):
    r = FakeRunner(stream_chunks=["Oct 20: ", "wrist buzzes [e-20261020-140322-ab12]"])
    hit = Hit("entry", "e-20261020-140322-ab12", "2026-10-20", "...", "the wrist servo buzzes")
    out = "".join([c async for c in quick_answer(r, "wrist?", [hit], DEFAULT_SETTINGS, PROMPTS, tmp_path)])
    assert "[e-20261020-140322-ab12]" in out
    call = r.calls[0]; assert call["allowed_tools"] == [] and call["max_turns"] == 1 and call["model"] == "haiku"
    assert "[e-20261020-140322-ab12] (2026-10-20) the wrist servo buzzes" in call["prompt"]
```
  At the top of the test file: `PROMPTS = Path(atlas.__file__).parent / "prompts"`, plus a `store` fixture from conftest.
- [ ] **Step 2: Run them to verify they fail**

  Run: `.venv/Scripts/python -m pytest tests/test_recall.py -v`
  Expected: FAIL.
- [ ] **Step 3: Implement `recall.py` and the two prompt files**
- [ ] **Step 4: Run the tests to verify they pass**

  Run: `.venv/Scripts/python -m pytest tests/test_recall.py -v`
  Expected: 10 passed (7 parametrized plus 3).
- [ ] **Step 5: Commit**

  `git add -A app && git commit -m "feat: recall with instant fts and cited answers"`

---

### Task 9: Review actions and Today

**Files:**
- Create: `app/atlas/review.py`, `app/atlas/today.py`
- Test: `app/tests/test_review.py`, `app/tests/test_today.py`

**Interfaces:**
- Consumes: `Store`, `BrainRepo`, `load_map`/`save_map`/`add_node`/`ensure_folders`, `load_settings`.
- Produces:
  - In `review.py`, `class Reviewer(paths, store, repo, on_commit)`, with these methods:
    - `confirm(ids: list[str]) -> list[Item]`:
      - `item` creates an item. If `due` is set and `remind_at` is null, `remind_at = due − default_lead_days`.
      - `update` sets the item's status.
      - `node` calls `add_node`, `save_map` and `ensure_folders`.
      - It then marks the proposals `confirmed`, runs `export_items`, then `commit_all("confirm: <n> items")`, then `on_commit()`.
      - Proposals carrying the warning `unknown item id` cannot be confirmed: they raise `ValueError`.
    - `dismiss(ids)`: marks proposals `dismissed`. They're kept, not deleted.
    - `edit_proposal(id, patch) -> Proposal`: only payload keys valid for that `ptype` are accepted, and dates are re-validated.
    - `edit_item(id, patch) -> Item`: also handles `{"status": "snoozed", "snooze_until": ...}`. It runs `export_items`, then a commit, then `on_commit()`.
  - In `today.py`, `compute_today(items: list[Item], proposals: list[Proposal], failed_entry_ids: list[str], now: datetime, nudge_days: int) -> dict` with keys:
    - `overdue`: due before today, status open;
    - `due_today`;
    - `upcoming`: `remind_at <= now < due`;
    - `next_time`: grouped `{project: [items]}`;
    - `waiting`: open waiting items, each `{"item":..., "nudge": bool}`, where nudge means created more than `nudge_days` ago;
    - `to_read`;
    - `open_tasks`: open tasks with no due date;
    - `review`: `{"proposals": n, "failed": n}`.

    Snoozed items with `snooze_until > now` are excluded everywhere. Items are serialized with `dataclasses.asdict`.

- [ ] **Step 1: Write the failing tests**

```python
# test_review.py  (fixture rv: Reviewer over a brain with default_map + project robotarm, committed)
def test_confirm_item_sets_default_remind_and_commits(rv):
    [p] = rv.store.add_proposals("e-1", [("item", {"kind":"deadline","text":"homework","area":"uni",
        "project":None,"person":None,"due":"2026-11-14","remind_at":None}, [])])
    [item] = rv.confirm([p.id])
    assert item.remind_at == "2026-11-12" and item.status == "open"
    assert (rv.paths.brain/"_items"/"items.json").exists() and not rv.repo.is_dirty()

def test_confirm_node_updates_map(rv):
    [p] = rv.store.add_proposals("e-1", [("node", {"type":"project","name":"Vibedine","parent":None}, [])])
    rv.confirm([p.id])
    assert any(x["slug"] == "vibedine" for x in load_map(rv.paths.brain).projects)
    assert (rv.paths.brain/"projects"/"vibedine").is_dir()

def test_confirm_update_moves_waiting_to_read(rv):
    w = rv.store.create_item(kind="waiting", text="API endpoints", area="projects", project="robotarm",
                             person=None, due=None, remind_at=None, source_entry="e-0")
    [p] = rv.store.add_proposals("e-1", [("update", {"item_id": w.id, "status": "to_read"}, [])])
    rv.confirm([p.id]); assert rv.store.get_item(w.id).status == "to_read"

def test_unknown_item_update_cannot_be_confirmed(rv):
    [p] = rv.store.add_proposals("e-1", [("update", {"item_id":"i-ghost","status":"done"}, ["unknown item id"])])
    with pytest.raises(ValueError): rv.confirm([p.id])

def test_dismiss_keeps_record(rv):
    [p] = rv.store.add_proposals("e-1", [("item", {"kind":"task","text":"x","area":"general"}, [])])
    rv.dismiss([p.id]); assert rv.store.list_proposals(status="dismissed")[0].id == p.id

# test_today.py
NOW = datetime(2026, 10, 20, 12, 0)
def test_sections():
    items = [mk(kind="deadline", due="2026-10-19"), mk(kind="deadline", due="2026-10-20"),
             mk(kind="deadline", due="2026-10-25", remind_at="2026-10-20"), mk(kind="next_time", project="robotarm"),
             mk(kind="waiting", created="2026-10-10T09:00:00"), mk(kind="waiting", status="to_read"),
             mk(kind="task"), mk(kind="task", status="snoozed", snooze_until="2026-10-21T09:00")]
    t = compute_today(items, [object()], ["e-9"], NOW, nudge_days=3)
    assert len(t["overdue"]) == 1 and len(t["due_today"]) == 1 and len(t["upcoming"]) == 1
    assert list(t["next_time"]) == ["robotarm"] and t["waiting"][0]["nudge"] is True
    assert len(t["to_read"]) == 1 and len(t["open_tasks"]) == 1
    assert t["review"] == {"proposals": 1, "failed": 1}
```
  Add `mk(**overrides) -> Item`, a helper with sane defaults (`status="open"`, `created="2026-10-20T09:00:00"`), in `test_today.py`.
- [ ] **Step 2: Run them to verify they fail**

  Run: `.venv/Scripts/python -m pytest tests/test_review.py tests/test_today.py -v`
  Expected: FAIL.
- [ ] **Step 3: Implement `review.py` and `today.py`**
- [ ] **Step 4: Run the tests to verify they pass**

  Run: `.venv/Scripts/python -m pytest tests/test_review.py tests/test_today.py -v`
  Expected: 6 passed.
- [ ] **Step 5: Commit**

  `git add -A app && git commit -m "feat: confirm/dismiss/edit and today view logic"`

---

### Task 10: Backup push

**Files:**
- Create: `app/atlas/backup.py`
- Test: `app/tests/test_backup.py`

**Interfaces:**
- Consumes: `BrainRepo.push`, `load_settings`.
- Produces: `class BackupPusher(repo, settings_getter: Callable[[], dict], broadcast, clock=time.monotonic)`, with:
  - `notify_commit()`: marks a push as pending;
  - `async tick() -> None`: pushes when something is pending, `backup_remote` is set and `backup_debounce_s` has passed since the first pending commit. On failure it keeps the push pending and waits 60 seconds before the next try;
  - `async run_forever()`: calls `tick()` every 15 seconds;
  - `status() -> {"remote_set": bool, "pending": bool, "last_ok": str | None, "last_error": str | None}`.

  Failures are only shown through `status()` and a `backup.status` broadcast, never as a popup (spec §14).

- [ ] **Step 1: Write the failing tests** (fake repo that records pushes and returns a scripted ok/fail; injectable clock)

```python
async def test_debounce_then_push():
    clk = Clock(0); repo = FakeRepo([True]); b = BackupPusher(repo, lambda: {"backup_remote":"r","backup_debounce_s":300}, noop, clk)
    b.notify_commit(); await b.tick(); assert repo.pushes == []
    clk.t = 301; await b.tick(); assert repo.pushes == ["r"] and not b.status()["pending"]

async def test_failure_stays_pending_and_backs_off():
    clk = Clock(0); repo = FakeRepo([False, True]); b = BackupPusher(repo, lambda: {"backup_remote":"r","backup_debounce_s":0}, noop, clk)
    b.notify_commit(); await b.tick(); assert b.status()["pending"] and b.status()["last_error"]
    clk.t = 30; await b.tick(); assert len(repo.pushes) == 1
    clk.t = 61; await b.tick(); assert len(repo.pushes) == 2 and not b.status()["pending"]

async def test_no_remote_no_push():
    repo = FakeRepo([True]); b = BackupPusher(repo, lambda: {"backup_remote":None,"backup_debounce_s":0}, noop, Clock(0))
    b.notify_commit(); await b.tick(); assert repo.pushes == []
```
- [ ] **Step 2: Run them to verify they fail**

  Run: `.venv/Scripts/python -m pytest tests/test_backup.py -v`
  Expected: FAIL.
- [ ] **Step 3: Implement `backup.py`**
- [ ] **Step 4: Run the tests to verify they pass**

  Run: `.venv/Scripts/python -m pytest tests/test_backup.py -v`
  Expected: 3 passed.
- [ ] **Step 5: Commit**

  `git add -A app && git commit -m "feat: debounced private-repo backup"`

---

### Task 11: Server (API, SSE, WebSocket, startup)

**Files:**
- Create: `app/atlas/server.py`, `app/atlas/__main__.py`
- Test: `app/tests/test_server.py`

**Interfaces:**
- Consumes everything above.
- Produces:
  - `DEFAULT_HOST = "127.0.0.1"` in `server.py`.
  - `create_app(paths: Paths, *, runner=None, start_workers: bool = True) -> web.Application`. The `runner` defaults to `ClaudeRunner(settings["claude_path"])`.
  - App keys available to handlers and tests: `app["paths"]`, `app["store"]`, `app["repo"]`, `app["runner"]`, `app["worker"]` (FilingWorker), `app["reviewer"]`, `app["backup"]` (BackupPusher).
- `__main__`: `python -m atlas --root D:\ATLAS`. It binds `127.0.0.1:<settings.port>` and prints `ATLAS on http://127.0.0.1:8090`.

**Startup** (an `on_startup` hook):
1. Open the store and `init_schema`.
2. If `brain/map.json` exists:
   - `repo.init()`;
   - commit outside edits as `owner edits`;
   - `restore_items_if_empty`;
   - `reindex_all` if the FTS table is empty;
   - add any log entries missing from the store;
   - `requeue_unfinished()`.
3. If `start_workers`, start `FilingWorker.run_forever` and `BackupPusher.run_forever` as tasks, and cancel them on cleanup.

**Routes** (JSON unless noted; errors return `{"error": msg}` with 400 or 404):

| Method and path | Body / query | Returns |
|---|---|---|
| `GET /` | | `web/index.html`; static `/static/*` serves `web/` |
| `GET /api/state` | | `{"setup_done": bool, "settings": {...}, "backup": status}` |
| `POST /api/setup` | `{"map": {...}, "backup_remote": str \| null}` | 200. Runs validate, `save_map`, `ensure_folders`, `repo.init`, initial commit `setup`, and saves `backup_remote`. 409 if setup was already done. |
| `GET /api/map`, `PUT /api/map` | map JSON | PUT validates, saves, runs `ensure_folders` and commits `map edit` |
| `GET /api/hierarchy` | | the `hierarchy()` tree with item counts per project/course/area |
| `GET /api/settings`, `PUT /api/settings` | patch | the full settings |
| `POST /api/entries` | `{"text", "source": "pc" \| "widget"}` | 201 `{"entry_id"}`. Runs `append_entry`, then `store.add_entry`, then `index_entry`, then `enqueue`, then broadcast `entry.saved`. **The log write happens before anything else.** |
| `GET /api/entries` | `?day=YYYY-MM-DD&before=<id>&limit=` | the journal list, each entry with its filing status |
| `GET /api/filings/{entry_id}` | | `{"filing", "proposals"}` |
| `POST /api/filings/{entry_id}/retry` | | 202. Re-enqueues. |
| `POST /api/filings/{entry_id}/correct` | `{"text"}` | 202. Runs `worker.correct` in the background. |
| `GET /api/proposals` | | all proposals with status `proposed`, grouped by entry |
| `PATCH /api/proposals/{id}` | payload patch | the proposal |
| `POST /api/proposals/confirm`, `POST /api/proposals/dismiss` | `{"ids": [...]}` | the confirmed items / 200 |
| `GET /api/items` | `?status=&kind=&project=&person=&area=` | items |
| `PATCH /api/items/{id}` | patch | the item |
| `GET /api/today` | | `compute_today(...)` |
| `GET /api/ask` | `?q=` | **SSE**: one `event: hits` with JSON hits, then `event: delta` with text chunks, then `event: done`. On `ClaudeError`, `event: error` with the message. The hits event is sent **before** Claude starts. |
| `GET /api/ask/deeper` | `?q=` | SSE, `delta`/`done`/`error` |
| `GET /ws` | | WebSocket; the server pushes `{"type", "data"}` for `entry.saved`, `filing.started`, `filing.done`, `filing.failed`, `items.changed`, `map.changed`, `backup.status` |

- [ ] **Step 1: Write the failing tests** (`aiohttp_client`, `create_app(paths, runner=FakeRunner(...), start_workers=False)`; drive the worker directly with `await app["worker"].file_entry(id)`)

```python
async def test_setup_then_state(client):
    assert (await (await client.get("/api/state")).json())["setup_done"] is False
    r = await client.post("/api/setup", json={"map": MAP, "backup_remote": None}); assert r.status == 200
    assert (await (await client.get("/api/state")).json())["setup_done"] is True
    assert (await client.post("/api/setup", json={"map": MAP})).status == 409

async def test_capture_logs_before_filing(ready_client, app_paths):
    r = await ready_client.post("/api/entries", json={"text": "check wrist next time", "source": "pc"})
    eid = (await r.json())["entry_id"]
    assert read_log(app_paths.brain)[-1].id == eid
    assert (await ready_client.post("/api/entries", json={"text": " ", "source": "pc"})).status == 400

async def test_file_confirm_today_flow(ready_client, app):
    eid = (await (await ready_client.post("/api/entries", json={"text":"x","source":"pc"})).json())["entry_id"]
    await app["worker"].file_entry(eid)
    card = await (await ready_client.get(f"/api/filings/{eid}")).json()
    ids = [p["id"] for p in card["proposals"]]
    r = await ready_client.post("/api/proposals/confirm", json={"ids": ids}); assert r.status == 200
    today = await (await ready_client.get("/api/today")).json()
    assert today["review"]["proposals"] == 0

async def test_ask_sends_hits_before_answer(ready_client):
    await ready_client.post("/api/entries", json={"text": "the wrist servo buzzes", "source": "pc"})
    body = await (await ready_client.get("/api/ask", params={"q": "wrist servo?"})).text()
    assert body.index("event: hits") < body.index("event: delta") and "event: done" in body

async def test_ask_no_match_says_nothing(ready_client):
    body = await (await ready_client.get("/api/ask", params={"q": "zebra"})).text()
    assert "Nothing on that." in body

async def test_binds_localhost_only():
    assert DEFAULT_HOST == "127.0.0.1"
```
  The fixtures `client` (fresh paths) and `ready_client` (after setup, with a FakeRunner returning `GOOD_RESULT` from `test_filing` and streaming `["ok [e-…]"]`) live in `test_server.py`.
- [ ] **Step 2: Run them to verify they fail**

  Run: `.venv/Scripts/python -m pytest tests/test_server.py -v`
  Expected: FAIL.
- [ ] **Step 3: Implement `server.py` and `__main__.py`**
  - Blocking store and git calls go through `asyncio.to_thread`.
  - `broadcast` sends to every open WebSocket and drops dead sockets.
- [ ] **Step 4: Run the full suite**

  Run: `.venv/Scripts/python -m pytest -v`
  Expected: all tests pass (6 in this file).
- [ ] **Step 5: Commit**

  `git add -A app && git commit -m "feat: aiohttp server, api, sse, websocket"`

---

### Task 12: Web shell, capture box, confirm card, Today

**Files:**
- Create: `app/web/index.html`, `app/web/style.css`, `app/web/js/api.js`, `app/web/js/app.js`, `app/web/js/components/capture.js`, `app/web/js/components/confirm-card.js`, `app/web/js/views/today.js`

**Interfaces:**
- Consumes the Task 11 routes.
- Produces:
  - `api.js` exports:
    - `get(path)`, `post(path, body)`, `patch(path, body)`, `put(path, body)`: each resolves JSON or throws `Error(msg)`;
    - `onEvent(type, fn)`, backed by one auto-reconnecting WebSocket (retry every 2 s);
    - `sse(path, handlers)`.
  - `app.js` is a hash router (`#/today` default, `#/ask`, `#/journal`, `#/project/<slug>`, `#/people`, `#/deadlines`, `#/settings`, `#/setup`). It redirects to `#/setup` when `state.setup_done` is false. It renders the nav, then the view, and keeps the capture box fixed at the top of every view.
  - `el(tag, attrs, ...children)` is a DOM helper in `app.js`. **It sets text only via `textContent`.**

**Behaviour:**
- **Capture box:**
  - A textarea. `Ctrl+Enter` or the Save button posts to `/api/entries`; the textarea clears only on 201.
  - On failure the text stays and an inline error shows.
  - After saving it shows "Saved · filing…".
- **Confirm card** (spec §5.3). It renders when `filing.done` arrives for an entry captured in this tab, and it's always available from Today's "To review":
  - "Filed to" lists the files plus the synopsis.
  - One row per proposal, stacked: kind badge, text, project/person, due/remind or "next session"/"waiting since", and any warnings in amber.
  - **✓** confirms, **✎** turns the row into inline inputs (text, kind select, project select from the map, date) that save via PATCH, and **✕** dismisses.
  - "Confirm all", and a "Tell it what's wrong" textarea that posts to `/correct`.
  - Failed filings show the error and a Retry button.
- **Today** has sections in this order: To review (count, expandable into cards), Overdue, Due today, Upcoming reminders, Next time (by project), Waiting (with "nudge?" marker), To read, Open tasks. Each item has a done ✓ and a snooze menu (1 day, 3 days, 1 week). It re-renders on `items.changed`, `filing.done` and `filing.failed`.

- [ ] **Step 1: Implement the files above.** Plain CSS: system font, a max width of 900 px, no animations.
- [ ] **Step 2: Manual verification.** Run `.venv/Scripts/python -m atlas --root D:\ATLAS` with a test brain (`--root` pointed at a scratch copy). Then:
  - The capture box saves; the card appears after filing; ✓, ✎ and ✕ work; Today updates without a reload.
  - Capture the Review Focus #1 string (from `test_rawlog.HOSTILE`): it shows as literal text, no alert fires, and the layout doesn't break.
  - Stop the hub mid-filing and restart it: the entry files again without duplicate rows.
  - Rename `claude.exe` temporarily (or set `claude_path` to a bad value in Settings): the filing shows failed with Retry, and the text isn't lost.
- [ ] **Step 3: Commit**

  `git add -A app && git commit -m "feat: web shell, capture, confirm card, today"`

---

### Task 13: Ask, Journal, browse views

**Files:**
- Create: `app/web/js/views/ask.js`, `app/web/js/views/journal.js`, `app/web/js/views/browse.js`

**Behaviour:**
- **Ask (spec §5.4):**
  - A question box. Submitting opens the SSE stream.
  - The `hits` event renders the matching entries and notes immediately (date, snippet with `[` `]` highlights shown in bold, built via text nodes, not HTML).
  - `delta` appends to the answer.
  - Bracketed refs in the answer (`[e-…]`, `[path#heading]`) become links: entries go to `#/journal?entry=<id>`, notes show the matching hit.
  - "Dig deeper" calls `/api/ask/deeper` and is shown only after the quick answer is done.
  - `error` shows the message with a Retry.
- **Journal:**
  - Entries newest first, grouped by day, showing source, filing status and the verbatim text in a `white-space: pre-wrap` block, with "Load older" paging.
  - A day picker, and a text filter that runs the FTS search through `/api/ask`'s hits only, without calling Claude. To support that, add `GET /api/search?q=` to `server.py`, returning `search_hits` as JSON, plus one test, `test_search_endpoint_returns_hits_without_claude`, asserting `runner.calls == []`.
  - `?entry=<id>` scrolls to and highlights that entry.
- **Browse:**
  - `#/project/<slug>` shows the project's open items (including next_time), its notes (list of `.md` files under the project folder; clicking one shows the raw text) and its journal entries. Add `GET /api/notes?prefix=projects/<slug>/` returning `[{path, text}]`, with a test that a `../` prefix returns 400.
  - `#/people` shows waiting and to_read items grouped by person.
  - `#/deadlines` shows dated items sorted by due date.

- [ ] **Step 1: Write the failing tests** for the two new endpoints in `tests/test_server.py`:

```python
async def test_search_endpoint_returns_hits_without_claude(ready_client, app):
    await ready_client.post("/api/entries", json={"text": "wrist servo", "source": "pc"})
    hits = await (await ready_client.get("/api/search", params={"q": "wrist"})).json()
    assert hits and app["runner"].calls == []

async def test_notes_rejects_path_escape(ready_client):
    assert (await ready_client.get("/api/notes", params={"prefix": "../"})).status == 400
```
- [ ] **Step 2: Run them to verify they fail**

  Run: `.venv/Scripts/python -m pytest tests/test_server.py -v -k "search_endpoint or notes_rejects"`
  Expected: FAIL (404).
- [ ] **Step 3: Implement the two endpoints and the three views**

  `/api/notes` resolves the prefix under `brain/` and rejects anything whose resolved path is outside `brain/`, or inside `log/` or `_items/`.
- [ ] **Step 4: Run the tests to verify they pass**

  Run: `.venv/Scripts/python -m pytest -v`
  Expected: all pass.
- [ ] **Step 5: Manual verification**
  - Ask a question that matches: hits appear instantly, then the answer streams with working citation links.
  - Ask "zebra": "Nothing on that." appears with no wait.
  - Dig deeper works.
  - The journal shows hostile text verbatim.
- [ ] **Step 6: Commit**

  `git add -A app && git commit -m "feat: ask, journal, project/people/deadline views"`

---

### Task 14: Setup wizard and Settings (map editor, hierarchy, preferences)

**Files:**
- Create: `app/web/js/views/setup.js`, `app/web/js/views/settings.js`

**Behaviour (spec §13.1):**
- **Setup wizard** at `#/setup`, shown on first run. Steps, with Back and Next on each:
  1. Areas: prefilled general, projects, uni, personal; add, rename or remove custom ones.
  2. Projects: name, auto-slug (editable), "this is my company" (one at most), optional folder paths.
  3. Uni courses.
  4. People: name, and their projects (multi-select).
  5. Quiet hours (stored now, used in phase 4), and an optional private GitHub backup URL with a hint: create an empty **private** repo first.
  6. Review, then Finish, which posts to `/api/setup`.
  - Validation errors from the server show on the relevant step.
  - The wizard state survives a page reload (kept in `localStorage`, wrapped in try/catch).
- **Settings** at `#/settings`:
  - **Brain map:** the same editors as the wizard, saved with `PUT /api/map`.
  - **Hierarchy:** an expandable tree from `/api/hierarchy`, showing file and item counts per node. Clicking a project opens `#/project/<slug>`.
  - **Preferences:** every key in `DEFAULT_SETTINGS` with a typed input (number, text, model select with `haiku`/`sonnet`/`opus`/custom, time pickers for quiet hours), saved with `PUT /api/settings`. Errors show inline.
  - **Backup:** remote URL, the status from `/api/state` (last ok, last error, pending), and a "Push now" button. That button needs `POST /api/backup/push`, which forces a `tick` with the debounce ignored. Add it, with a test asserting a FakeRepo push happens.
  - A hint line: "Open `D:\ATLAS\brain` in Obsidian for a graph view."

- [ ] **Step 1: Write the failing test** for `POST /api/backup/push` in `tests/test_server.py`:

```python
async def test_backup_push_now_ignores_debounce(ready_client, app):
    app["backup"].repo = FakeRepo([True]); app["backup"].notify_commit()
    await ready_client.put("/api/settings", json={"backup_remote": "r", "backup_debounce_s": 9999})
    assert (await ready_client.post("/api/backup/push")).status == 200 and app["backup"].repo.pushes == ["r"]
```
  This adds `force: bool = False` to `BackupPusher.tick`.
- [ ] **Step 2: Run it to verify it fails**

  Run: `.venv/Scripts/python -m pytest tests/test_server.py -v -k backup_push_now`
  Expected: FAIL.
- [ ] **Step 3: Implement the endpoint and the two views**
- [ ] **Step 4: Run the tests to verify they pass**

  Run: `.venv/Scripts/python -m pytest -v`
  Expected: all pass.
- [ ] **Step 5: Manual verification**
  - Run the fresh-root wizard end to end. `brain/` is created with folders, `map.json` and a git repo with a `setup` commit.
  - Editing the map in Settings creates folders and commits.
  - The hierarchy counts match the files.
  - A bad slug shows an error.
  - A preference change persists across a restart.
- [ ] **Step 6: Commit**

  `git add -A app && git commit -m "feat: setup wizard and settings"`

---

### Task 15: Real end-to-end check and README

**Files:**
- Create: `app/README.md`

- [ ] **Step 1: Write `app/README.md`.** It covers:
  - install: the venv command from Task 1;
  - run: `.venv/Scripts/python -m atlas --root D:\ATLAS`, then open `http://127.0.0.1:8090`;
  - first-run wizard;
  - where the data lives (`brain/`, `data/`);
  - backup setup;
  - how to run the tests;
  - the fact that `ANTHROPIC_API_KEY` is ignored on purpose.
- [ ] **Step 2: Run the real end-to-end check with the real Claude CLI** against the real `D:\ATLAS` root. Using the owner's projects from the wizard, capture:
  > "Worked on robotarm today, the wrist servo buzzes at 90°. Next time on robotarm check the wrist backlash. Need to buy a present for my girlfriend before the 14th. Teammate A owes me the API endpoints for the hackathon."

  Expected:
  - The card shows a `next_time` (robotarm), a `deadline`/`reminder` dated the 14th of the coming month relative to the entry, and a `waiting`. A teammate unknown to the map is proposed as a node.
  - The files contain `[[robotarm]]` and `(from e-…)`.
  - `log/` is untouched.
  - Confirm all, and Today shows them.
  - Ask "what did I say about the wrist servo?": the hits appear at once, and the answer quotes "buzzes at 90°" with a citation.

  Record the filing and recall wall-clock times in the README under "Measured".
- [ ] **Step 3: Run the full test suite one last time**

  Run: `.venv/Scripts/python -m pytest -v`
  Expected: all pass.
- [ ] **Step 4: Commit**

  `git add -A app && git commit -m "docs: run instructions and measured e2e timings"`

---

## Not in phase 1 (by design)

The spec puts these in later phases:
- **Phase 2:** widget, usage meter, Agents panel and hooks, autostart and tray, folder→project flags for next-time items.
- **Phase 3:** tablet, LAN binding and pairing, measurement requests.
- **Phase 4:** voice.
- **Phase 5:** Google Calendar.
- **Phase 6:** phone.
- **Any time after phase 1:** semantic search.

Phase 1 stores `quiet_hours` and project `folders` so those phases need no data migration.
