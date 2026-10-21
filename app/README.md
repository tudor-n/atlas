# BrainHub (phase 1)

BrainHub is a local second brain. You type free text; a headless Claude Code agent files it into a git-versioned Markdown `brain/` and *proposes* tasks, which you confirm. Recall is instant local search plus a cited answer.

- **Capture:** raw entries go into an append-only log first, then the agent files them into notes and proposes items (next-time, deadline, reminder, waiting, to-read, task, new people/projects).
- **Review:** nothing becomes an item until you confirm it. Today, project, people and deadline views show the confirmed items.
- **Recall:** search hits appear at once; Claude answers from them with citations, or says `Nothing on that.`

## Requirements
- Windows, Python >= 3.12, git
- The Claude Code CLI (`claude`) installed and **logged in** with your subscription

## Install
From `D:\BrainHub\app`:

```
py -3 -m venv .venv
.venv/Scripts/python -m pip install -e ".[test]"
```

## Run
```
.venv/Scripts/python -m brainhub --root D:\BrainHub
```
Then open <http://127.0.0.1:8090>. `--port N` overrides the port (default 8090, setting `port`).

## First run
The setup wizard asks for your map: areas, projects (and which are companies, with their folders), courses and people, plus quiet hours and an optional backup remote. Finishing it creates the `brain/` git repo (commit `setup`). You can edit the map later in Settings.

## Where data lives
- `brain/`: your Markdown notes, its own git repo. `log/` holds the verbatim raw entries, `_items/` the confirmed items (`items.json`) and the proposals still waiting for you (`proposals.json`), `map.json` the map. Every filing, confirmation and item edit is a commit (a filing is `file <entry id>: <synopsis>`); your own edits are committed as they are ("owner edits"). The agent never modifies `log/`, `_items/`, `map.json` or any dot-file.
- `data/hub.db`: the search index and filing state. It is a rebuildable index: if it is lost or damaged, the next start rebuilds it from `brain/` (entries filed before are recognised from their `file <entry id>:` commits and are not filed again; pending proposals come back from `_items/proposals.json`; if more than 10 entries turn out never to have been filed, they wait in To review for you to press Retry instead of all being filed at once).
- `data/settings.json`: your preferences and the backup URL. Keep it; it is not in `brain/`.
- `data/worktrees/`: temporary agent checkouts, removed after each filing.

Both are git-ignored by the code repo.

## Backup
Create an **empty, private** GitHub repository, then paste its URL in Settings (backup remote). The hub pushes `brain/` shortly after commits (debounced, 5 minutes by default). Push status shows in the Settings view; "Push now" forces it. A push never asks for a password: git runs without a terminal prompt or credential-manager window and gives up after 120 s, so sign in to GitHub once with git (or use an SSH key without a passphrase prompt) before relying on it.

`brain/.gitattributes` (`* -text`) keeps line endings exactly as written, so a restore with `git clone` gives a readable raw log even where git converts line endings by default.

## Claude and `ANTHROPIC_API_KEY`
All Claude calls run `claude -p` on your subscription. `ANTHROPIC_API_KEY` is removed from the environment of every child process **on purpose**, and `--bare` is never used, so you are never billed per token by accident. Models are settings: filing `sonnet`, quick recall `haiku`, deeper recall `sonnet`.

The hub's agents run isolated from your own Claude Code setup: they load no settings files (`--setting-sources ""`), so none of your plugins, hooks or permission rules apply; no MCP servers (`--strict-mcp-config`); no skills or slash commands; no saved sessions; and only the built-in tools each job needs (`--tools`: none for quick recall, `Read Glob Grep` for dig deeper, `Read Write Edit Glob Grep` for filing). Your login still works, since it is not a setting.

## Security
The server binds `127.0.0.1` only. Writes require `Content-Type: application/json` and a same-origin `Origin`/`Host`, so cross-site requests from other web pages are blocked. The UI renders text with `textContent` only.

## Tests
From `D:\BrainHub\app`:
```
.venv/Scripts/python -m pytest -v
node --test web/js/components/map-editor.test.mjs web/js/components/text.test.mjs web/js/views/journal.test.mjs
```
The Python tests use a fake Claude runner; none of them call the real CLI.

## Known limitations
Deferred on purpose; none of them loses data.
- Owner edits to notes are committed (and re-indexed for search) at the next hub commit or restart, not the moment the file is saved: there is no file watcher yet.
- `_items/proposals.json` and dismissals reach the backup with the next hub commit, not on their own.
- `map.json` is written in place, not via a temporary file; a crash mid-write needs a `git checkout map.json` in `brain/`.
- `slugify` drops letters that have no ASCII decomposition (`ł`, `ø`): "Łódź" becomes `odz`.
- A `#` line inside a fenced code block in a note counts as a heading for search sections.
- `_items/<group>.md` files for groups that no longer have items are left in place (stale, read-only views).
- `build_fts_query` neither caps nor de-duplicates question words.
- WebSocket broadcasts are awaited inline, so a very slow browser tab can delay an API answer.
- A setup that fails after validation (e.g. git missing) leaves `map.json` behind and the wizard then sees "setup already done".
- The Journal area filter and the project page's journal list only see the entries loaded so far (newest 50/500); load older entries to filter further back.
- A tolerant raw-log read resynchronises at the next record header; text inside a damaged record that itself looks like a complete record would be read as one (it is still reported).
- Folders named `log` or `_items` nested inside other folders are hidden from the notes viewer.
- If `claude_path` resolves to an npm `.cmd` shim, arguments pass through `cmd.exe` quoting.
- Whether the agents still read a user-level `CLAUDE.md` memory file under `--setting-sources ""` is not verified; keep yours free of instructions that would clash with filing.

## Measured
Real end-to-end run on a scratch root with the real Claude CLI 2.1.304 (2026-10-21), using the sample entry about the robot arm wrist servo, a present deadline and a teammate owing endpoints:

| Step | Model | Wall clock |
|---|---|---|
| Filing (capture to `done`) | sonnet | 18.1 s |
| Recall `what did I say about the wrist servo?`: search hits | local | instant (0.01 s) |
| Recall: first answer text | haiku | 4.4 s |
| Recall: done | haiku | 5.7 s |
| Recall `zebra` (no hits, no Claude call) | none | instant, `Nothing on that.` |

The filing time is the successful run (a retry of the same entry: the first attempt failed only because the scratch root had no `app/` folder; the hub now finds its prompts, schemas and UI next to the installed package, so `--root` can be any data folder). Times depend on load and model latency.

## Not in phase 1
- **Phase 2:** widget, usage meter, Agents panel and hooks, autostart and tray, folder-to-project flags for next-time items.
- **Phase 3:** tablet, LAN binding and pairing, measurement requests.
- **Phase 4:** voice.
- **Phase 5:** Google Calendar.
- **Phase 6:** phone.
- **Any time after phase 1:** semantic search.

Phase 1 already stores `quiet_hours` and project `folders` so those phases need no data migration.
