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
- `brain/`: your Markdown notes, its own git repo. `log/` holds the verbatim raw entries, `_items/` the confirmed items, `map.json` the map. Every filing, confirmation and item edit is a commit; your own edits are committed as they are ("owner edits"). The agent never modifies `log/`, `_items/` or `map.json`.
- `data/`: the SQLite index (`hub.db`), settings, and agent worktrees. Rebuildable from `brain/`, safe to delete.

Both are git-ignored by the code repo.

## Backup
Create an **empty, private** GitHub repository, then paste its URL in Settings (backup remote). The hub pushes `brain/` shortly after commits (debounced). Push status shows in the Settings view; "Push now" forces it.

## Claude and `ANTHROPIC_API_KEY`
All Claude calls run `claude -p` on your subscription. `ANTHROPIC_API_KEY` is removed from the environment of every child process **on purpose**, and `--bare` is never used, so you are never billed per token by accident. Models are settings: filing `sonnet`, quick recall `haiku`, deeper recall `sonnet`.

## Security
The server binds `127.0.0.1` only. Writes require `Content-Type: application/json` and a same-origin `Origin`/`Host`, so cross-site requests from other web pages are blocked. The UI renders text with `textContent` only.

## Tests
From `D:\BrainHub\app`:
```
.venv/Scripts/python -m pytest -v
node --test web/js/components/map-editor.test.mjs
```
The Python tests use a fake Claude runner; none of them call the real CLI.

## Measured
Real end-to-end run on a scratch root with the real Claude CLI 2.1.304 (2026-10-21), using the sample entry about the robot arm wrist servo, a present deadline and a teammate owing endpoints:

| Step | Model | Wall clock |
|---|---|---|
| Filing (capture to `done`) | sonnet | 18.1 s |
| Recall `what did I say about the wrist servo?`: search hits | local | instant (0.01 s) |
| Recall: first answer text | haiku | 4.4 s |
| Recall: done | haiku | 5.7 s |
| Recall `zebra` (no hits, no Claude call) | none | instant, `Nothing on that.` |

The filing time is the successful run (a retry of the same entry: the first attempt failed only because the scratch root had no `app/` folder, and the hub expects `<root>/app`). Times depend on load and model latency.

## Not in phase 1
- **Phase 2:** widget, usage meter, Agents panel and hooks, autostart and tray, folder-to-project flags for next-time items.
- **Phase 3:** tablet, LAN binding and pairing, measurement requests.
- **Phase 4:** voice.
- **Phase 5:** Google Calendar.
- **Phase 6:** phone.
- **Any time after phase 1:** semantic search.

Phase 1 already stores `quiet_hours` and project `folders` so those phases need no data migration.
