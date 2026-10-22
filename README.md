# BrainHub

A personal, local second brain for one owner. Free-text entries are written verbatim to an append-only log, then filed by a headless Claude Code agent into a git-versioned Markdown `brain/`. The agent proposes next-time notes, deadlines, reminders and waiting-on items; nothing becomes a task until you confirm it.

- Runs as a small aiohttp hub on `127.0.0.1` (Python 3.12+ via uv, Claude Code CLI on your subscription).
- Recall gives instant full-text hits plus a cited answer, or `Nothing on that.`
- `brain/` is its own git repo, with optional push to a private GitHub backup.
- Phase 1 is the capture, filing, review, Today and recall core.
- Phase 2 adds a Claude usage meter, an Agents panel fed by Claude Code hooks, a desktop widget, and a Tauri desktop shell (tray, start at login, hub self-healing).
- It runs on Windows and Kubuntu, on a dual-boot laptop: both OSes use one shared data root and the same code, and share Claude Code memory and settings. Setup on each OS: [app/README.md](app/README.md#setup-on-each-os).

Next session: [NEXT-SESSION.md](NEXT-SESSION.md) says where things stand.

Docs:
- [Setup, run and install instructions](app/README.md) (both OSes); [desktop shell](app/desktop/README.md)
- [Design spec](docs/superpowers/specs/2026-10-20-brainhub-design.md), the authority for all phases
- Phase 1: [plan](docs/superpowers/plans/2026-10-20-brainhub-phase1.md), [build decisions](docs/superpowers/phase1-decisions.md)
- Phase 2: [design](docs/superpowers/specs/2026-10-21-brainhub-phase2-design.md), [plan](docs/superpowers/plans/2026-10-21-brainhub-phase2.md), [build decisions](docs/superpowers/phase2-decisions.md)
- Dual boot: [design](docs/superpowers/specs/2026-10-21-brainhub-dual-boot-design.md), [plan](docs/superpowers/plans/2026-10-22-brainhub-dual-boot.md), [build decisions](docs/superpowers/dual-boot-decisions.md)
