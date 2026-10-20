# BrainHub

A personal, local second brain for one owner. Free-text entries are written verbatim to an append-only log, then filed by a headless Claude Code agent into a git-versioned Markdown `brain/`. The agent proposes next-time notes, deadlines, reminders and waiting-on items; nothing becomes a task until you confirm it.

- Runs as a small aiohttp hub on `127.0.0.1` (Windows, Python 3.12+, Claude Code CLI on your subscription).
- Recall gives instant full-text hits plus a cited answer, or `Nothing on that.`
- `brain/` is its own git repo, with optional push to a private GitHub backup.
- Phase 1 is the capture, filing, review, Today and recall core.

Docs:
- [Run and install instructions](app/README.md)
- [Design spec](docs/superpowers/specs/2026-10-20-brainhub-design.md)
- [Phase 1 plan](docs/superpowers/plans/2026-10-20-brainhub-phase1.md)
