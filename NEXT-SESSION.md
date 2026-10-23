# Next session: start here

A short pointer, updated 2026-10-23. It is no longer the only thing carried between the OSes: once claude-sync is rolled out, Claude Code memory is shared through `D:\ClaudeShared` = `/mnt/data/ClaudeShared`.

## Where things stand
- **Phases 1 and 2 and the dual-boot port** (plan Tasks 1–12, PR #3) are merged into `master` (2026-10-22). CI runs on Ubuntu and Windows.
- **What remains:**
  1. **The owner's feedback from the Linux test drive** (below): fixed and merged into master (2026-10-23; usage meter, speed). The next test drive checks the meter against claude.ai and the filing card's live progress.
  2. **The Kubuntu rollout: done on 2026-10-23 except the setup wizard**, which the owner runs in the app (http://127.0.0.1:8090).
     - The live copy `/mnt/data/BrainHub` is on master, with its old branches deleted.
     - Runtime slot A is in `~/.local/share/brainhub`.
     - The widget is built from the live copy into `~/.cache/brainhub-target/release/brainhub-desktop`. The owner starts it by hand: no autostart (their choice).
     - The integration is installed for real; its backup is `~/.claude/settings.json.brainhub-backup-20261023-113616`.
     - A terminal session showed Where = terminal, entrypoint `cli`. Still to check: a VS Code session (Where = IDE).
     - `claude-sync` waits for Windows (the owner's choice): without the shared store, its runs change nothing.
  4. **Phase 3, the workbench tablet: built on branch `phase-3`** (not pushed). Plus the arm repo's `brainhub-home` branch in `~/dev/RobotArmControl` (not pushed; `index.html` only).
     - Design, plan and decisions: `docs/superpowers/specs/2026-10-23-brainhub-phase3-design.md`, `plans/2026-10-23-brainhub-phase3.md`, `phase3-decisions.md`.
     - Checked in headless Chrome over a real LAN site (`app/README.md` → Measured → Phase 3).
     - Subagent reviews come after the build (the owner's instruction), then the owner merges.
     - **Its rollout needs the owner** (design §11; `app/README.md` → Phase 3): reserve `192.168.0.200` for MAC `24:b2:b9:cd:c5:69` in the router; `sudo ufw allow from 192.168.0.0/24 to any port 8090 proto tcp`; Settings → Tablet; the Fully Kiosk start URL `http://192.168.0.200:8090/tablet`; pair; reinstall the integration (adds `brainhub-request` and its `CLAUDE.md` line); merge the arm branch and restart `server.py`; then the device checks in `phase3-decisions.md` → Residuals.
  3. **On hold (owner, 2026-10-23): everything on Windows.** The owner uses Kubuntu only for a while and will come back to Windows later. That covers the Windows half of the memory spike (ruling D2, in a fresh Windows clone, never in `D:\BrainHub`), the Windows-side checks (the widget built hidden and then shown, D1; claude-sync's junctions, drive-letter keys and `D:\` defaults for real), the Windows rollout and the Windows half of design §10's verification. Until then: code stays portable, CI keeps running on both OSes (the update step's gate needs both `test (…)` jobs green), and nothing is checked on Windows by hand.
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
