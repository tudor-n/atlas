# Next session: start here

A short pointer, updated 2026-10-22. It is no longer the only thing carried between the OSes: once claude-sync is rolled out, Claude Code memory is shared through `D:\ClaudeShared` = `/mnt/data/ClaudeShared`.

## Where things stand
- **Phases 1 and 2** are merged into `master`.
- **The dual-boot port is built** on branch `dual-boot` (plan Tasks 1–12), draft PR #3. CI runs on Ubuntu and Windows. The owner merges it.
- **What remains:**
  1. The Windows half of the Claude Code memory spike (ruling D2), at a scheduled reboot into Windows.
  2. The Windows-side checks: the widget, built hidden and then shown, behaves as before (D1); claude-sync's junctions, drive-letter keys and `D:\` defaults work for real (so far only CI ran them).
  3. The rollout, with the owner: plan → Rollout (`docs/superpowers/plans/2026-10-22-brainhub-dual-boot.md`). Merge, then Kubuntu (live copy to master, `runtime init`, the shell, the integration and its entrypoint checks, `claude-sync` and labelling one-OS notes, the setup wizard on the real root), then Windows, then design §10's verification (`app/README.md` → Verification), which also covers D1's open desktop checks.
- **No real brain exists yet**: the setup wizard has never run on `/mnt/data/BrainHub` = `D:\BrainHub`.

## Where things live
| What | Where |
|---|---|
| Project rules, code map, run and test commands (both OSes) | `app/CLAUDE.md` |
| Setup on each OS, where data lives, drills, Measured | `app/README.md`, `app/desktop/README.md` |
| The dual-boot design (approved) and plan | `docs/superpowers/specs/2026-10-21-brainhub-dual-boot-design.md`, `docs/superpowers/plans/2026-10-22-brainhub-dual-boot.md` |
| Build-time rulings D1–D12 and the residuals | `docs/superpowers/dual-boot-decisions.md` (phases 1 and 2: `phase1-decisions.md`, `phase2-decisions.md`) |

## Standing rules
- **Commits:** plain messages, never `Co-Authored-By` or `Claude-Session` trailers.
- **Nothing is pushed or published without asking.** The owner merges PRs on GitHub.
- **Never the real root in tests or manual checks** (`/mnt/data/BrainHub` = `D:\BrainHub`), nor the real Claude settings, state or config folders, or the shared Claude store: temp folders only (`app/CLAUDE.md` rules 9 and 12).
- **No `CLAUDE.md` at the repo root.** Start dev sessions in `~/dev/BrainHub/app`.
