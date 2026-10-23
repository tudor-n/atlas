# Next session: start here

A short pointer, updated 2026-10-23. It is no longer the only thing carried between the OSes: once claude-sync is rolled out, Claude Code memory is shared through `D:\ClaudeShared` = `/mnt/data/ClaudeShared`.

## Where things stand
- **Phases 1 and 2 and the dual-boot port** (plan Tasks 1–12, PR #3) are merged into `master` (2026-10-22). CI runs on Ubuntu and Windows.
- **What remains:**
  1. **The owner's feedback from the Linux test drive** (below): fixed on branch `test-drive-fixes` (usage meter, speed). The owner merges it; then the next test drive checks the meter against claude.ai and the filing card's live progress.
  2. The Windows half of the Claude Code memory spike (ruling D2), at a reboot into Windows. Work in a fresh Windows clone, never in `D:\BrainHub`.
  3. The Windows-side checks: the widget, built hidden and then shown, behaves as before (D1); claude-sync's junctions, drive-letter keys and `D:\` defaults work for real (so far only CI ran them).
  4. The rollout, with the owner: plan → Rollout (`docs/superpowers/plans/2026-10-22-brainhub-dual-boot.md`). Kubuntu (live copy to master, `runtime init`, the shell, the integration and its entrypoint checks, `claude-sync` and labelling one-OS notes, the setup wizard on the real root), then Windows, then design §10's verification (`app/README.md` → Verification), which also covers D1's open desktop checks.
- **No real brain exists yet**: the setup wizard has never run on `/mnt/data/BrainHub` = `D:\BrainHub`.

## Owner feedback from the Linux test drive (2026-10-22, evening)
A throwaway test drive on Kubuntu: the hub and widget from the dev copy on a scratch brain, with the real Claude Code hooks installed. The owner's verdict: **all features generally work well**, but two things need attention.

1. **The usage meter lagged behind reality**: it read about 2 points low (real 7 % showed 5 %; real 8 % then 6 %). **Fixed on branch `test-drive-fixes`** (2026-10-23; `phase2-decisions.md` rulings 20–21):
   - Every Claude Code session posts its own last response's numbers, and the store kept whichever arrived last. Now the highest reading inside a window wins; a later window replaces it, and an earlier one is ignored.
   - The hub's own `claude -p` runs (filings, answers) used the plan without reporting it. Each run's `rate_limit_event` now feeds the meter; filing moved to `stream-json` output for it.
   - Still to do with the owner: compare the meter with claude.ai's usage page (`fable_usage_url`) in the next test drive.
2. **Writing and recalling feel slow.** Measured 2026-10-23 (`app/README.md` → Measured → Test-drive follow-up):
   - Capture → filed takes 10–21 s with Sonnet. Almost all of it is the model taking 4–6 turns; the hub's own work is about 0.2 s and the CLI starts in 0.6 s. Each run also waits 0.5–0.9 s for the CLI to exit after it has printed its result.
   - Recall: first text after 1.4–3.2 s, done after 2.5–4.2 s. That's faster than phase 1's numbers.
   - Haiku for filing is no fix: not reliably faster, and worse filings.
   - The filing card shows a static "Filing…" for the whole wait.
   - **The owner picked (2026-10-23), built on `test-drive-fixes`:** live progress on the filing card ("Filing… writing personal/errands.md · 5 s", ruling 23), and the CLI's slow exit and start-up are gone (`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, ruling 22: about 1.2–1.6 s off every run; recall is done in 2.3–2.7 s).
   - Not picked, still open: fewer agent turns (e.g. a listing of the brain's existing notes in the filing prompt, so the agent needn't search first). That's calibration territory (`prompts/filing.md`): measure before and after.
3. Confirmed working in the same session:
   - **The "D: went missing" drill passed.** The banner showed, the folder was never created, and a capture was held. Recovery took about 7 s after the folder came back (exit 75, then a restart). The capture landed with its original timestamp.
   - A VS Code session showed **Where = IDE**, so that entrypoint value is right.

**Test-drive leftovers: cleaned up 2026-10-23.** BrainHub's hooks and status line are out of the real `~/.claude/settings.json` (it is back to its four keys; backups `~/.claude/settings.json.brainhub-backup-*`), and the Tauri data folders are deleted.

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
