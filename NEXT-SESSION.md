# Next session: start here

A short pointer, updated 2026-10-22. It is no longer the only thing carried between the OSes: once claude-sync is rolled out, Claude Code memory is shared through `D:\ClaudeShared` = `/mnt/data/ClaudeShared`.

## Where things stand
- **Phases 1 and 2 and the dual-boot port** (plan Tasks 1–12, PR #3) are merged into `master` (2026-10-22). CI runs on Ubuntu and Windows.
- **What remains:**
  1. **The owner's feedback from the Linux test drive** (below): fix the usage meter, look into the slowness.
  2. The Windows half of the Claude Code memory spike (ruling D2), at a reboot into Windows. Work in a fresh Windows clone, never in `D:\BrainHub`.
  3. The Windows-side checks: the widget, built hidden and then shown, behaves as before (D1); claude-sync's junctions, drive-letter keys and `D:\` defaults work for real (so far only CI ran them).
  4. The rollout, with the owner: plan → Rollout (`docs/superpowers/plans/2026-10-22-brainhub-dual-boot.md`). Kubuntu (live copy to master, `runtime init`, the shell, the integration and its entrypoint checks, `claude-sync` and labelling one-OS notes, the setup wizard on the real root), then Windows, then design §10's verification (`app/README.md` → Verification), which also covers D1's open desktop checks.
- **No real brain exists yet**: the setup wizard has never run on `/mnt/data/BrainHub` = `D:\BrainHub`.

## Owner feedback from the Linux test drive (2026-10-22, evening)
A throwaway test drive on Kubuntu: the hub and widget from the dev copy on a scratch brain, with the real Claude Code hooks installed. The owner's verdict: **all features generally work well**, but two things need attention.

1. **The usage meter lags behind reality.** It reads about 2 points low and stays behind: real 7 % shows 5 %; real 8 % then shows 6 %.
   - Likely cause, from reading `app/brainhub/usage.py` (not verified yet): `UsageStore.update` keeps whatever payload arrived **last** for each window. Every running Claude Code session posts its own status line's `rate_limits`, which reflect *that* session's last API response, so an idle session re-rendering its status line overwrites newer, higher numbers from the active one.
   - Fix idea: within one window (same `resets_at`), keep the **highest** `used_percentage` (usage only rises inside a window); a later `resets_at` replaces it. Also check how often Claude Code re-runs the status line, and compare with claude.ai's usage page (`fable_usage_url`) after the fix.
2. **Writing and recalling feel slow.** "Writing" is the capture being filed (the filing agent: `filing_model`, Sonnet by default, through `claude -p` with tools); recall is the quick cited answer (`recall_model`, Haiku, streamed).
   - Measure each stage first: claude CLI start-up with the isolation flags, model time, the hub's own work. Compare with phase 2's numbers in `app/README.md` → Measured.
   - Then pick fixes: visible progress while filing; Haiku for filing (already a Setting); smaller `recall_top_n`; cutting CLI start-up cost if that dominates.
3. Confirmed working in the same session:
   - **The "D: went missing" drill passed.** The banner showed, the folder was never created, and a capture was held. Recovery took about 7 s after the folder came back (exit 75, then a restart). The capture landed with its original timestamp.
   - A VS Code session showed **Where = IDE**, so that entrypoint value is right.

**Test-drive leftovers to clean up first (Kubuntu):**
- BrainHub's hooks and status line are still in the real `~/.claude/settings.json`. They point at `~/dev/BrainHub/app/.venv/bin/python` and the dev copy's `hook_client.py`. With no hub running they stay silent, but they should go. To remove them: `cd ~/dev/BrainHub/app && uv run --no-sync python -m brainhub integration uninstall --root "$(mktemp -d)"`. There was no status line before the test drive. BrainHub's backup of the file from before: `~/.claude/settings.json.brainhub-backup-20261022-232741`.
- The scratch brain and its state lived in `/tmp` (gone after a reboot). The widget and hub run until tray → Quit or a reboot.
- Delete the Tauri data folders the test run made: `~/.local/share/dev.brainhub.desktop` and `~/.config/dev.brainhub.desktop` (the real install recreates them).

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
