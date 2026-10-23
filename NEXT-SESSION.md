# Next session: start here

A short pointer, updated 2026-10-23. It is no longer the only thing carried between the OSes: once claude-sync is rolled out, Claude Code memory is shared through `D:\ClaudeShared` = `/mnt/data/ClaudeShared`.

## The owner's to-do list
Everything waiting on you, in order. Tick it here as you go; the details are in the sections below.

**1. Land phase 3** (both branches are built and reviewed, not pushed):
- [ ] Choose how: merge locally to master and push (like the test-drive fixes), or push the branches and open PRs you merge on GitHub.
- [ ] BrainHub: `phase-3` in `~/dev/BrainHub`.
- [ ] Arm UI: `brainhub-home` in `~/dev/RobotArmControl` (repo `6-dof-arm`, into `master`). It only changes `index.html`. The untracked `design_handoff_tablet_ui/` folder stays out of it.
- [ ] Wait for CI to go green on both OSes. The update step won't pull a commit without it.
- [ ] Restart the widget so the hub picks up phase 3 from master. Settings should then show a **Tablet** section.

**2. Finish the Kubuntu rollout:**
- [ ] Run the setup wizard on the real brain at http://127.0.0.1:8090. No real brain exists until you do.
- [ ] Open a Claude Code session in VS Code and check that its Agents row says **Where = IDE** on the real install.

**3. Next test drive (on the real brain):**
- [ ] Compare the Usage meter with claude.ai's usage page (`fable_usage_url`). It should match, not read about 2 points low.
- [ ] Watch a filing card: it should show live steps ("Filing… writing personal/errands.md · 5 s"), not a static "Filing…".
- [ ] Tell me whether filing still feels slow. If it does, "fewer agent turns" is the next fix (not built yet; it needs measuring before and after).

**4. Phase 3 rollout** (after step 1; `app/README.md` → Phase 3 has the details):
- [ ] **Router:** reserve `192.168.0.200` for the laptop's Wi-Fi MAC `24:b2:b9:cd:c5:69` in its DHCP page. Reconnect the Wi-Fi, then check that `ip -4 addr` shows `192.168.0.200`.
- [ ] **Firewall:** ufw is active on this laptop, so run `sudo ufw allow from 192.168.0.0/24 to any port 8090 proto tcp`.
- [ ] **Settings → Tablet:** press "Use … (now)" to fill in the address, tick **Tablet over Wi-Fi**, then Save. It should say "Listening on http://192.168.0.200:8090/tablet".
- [ ] **Fully Kiosk:** set the start URL to `http://192.168.0.200:8090/tablet`. Make sure **DOM storage** is on in Fully's web settings.
- [ ] **Pair:** press **Pair a device** in Settings and type the 6-digit code on the tablet's keyboard within 5 minutes. The tablet then appears under Settings → Tablet, with **Revoke**.
- [ ] **Integration:** Settings → Claude Code integration → **Install** again. This adds `brainhub-request` to `~/.local/bin` and one marked line to `~/.claude/CLAUDE.md`, after a backup.
- [ ] **Arm:** restart the arm's `server.py` from the merged `master`.

**5. Checks on the tablet itself** (SM-T110 with Fully Kiosk; `phase3-decisions.md` → Residuals). Tell me anything that fails:
- [ ] Restart Fully Kiosk: the tablet is still paired and doesn't show the pairing screen.
- [ ] The keyboard glyphs `⌫ ↵ ⇧ ⇪ ⌄ ‹ → · − Ω` all render, with no empty boxes.
- [ ] Switching between TODAY, NOTES and AGENTS has no visible lag.
- [ ] Type half a note, reload the page: the draft is still there (this needs DOM storage).
- [ ] Send a note with the laptop's Wi-Fi off. It shows "Waiting to send (1)"; when the Wi-Fi is back it arrives once, in the PC's **Tablet** view. Press **File this** there and it gets filed.
- [ ] Measurement request: from any terminal, run `brainhub-request "Test" "Width of the desk" "Height of the desk"`.
  - It should appear on NOTES within about a second, and the widget should show "1 request".
  - Answer it with the unit chips, then SEND. The answer shows in the PC's **Tablet** view.
- [ ] IGNORE a note (two taps within 2 s), then bring it back with **Show ignored** on the PC.
- [ ] **ROBOT → arm UI → ‹ HOME** (hold it, or tap it twice) comes back to the tablet page.
  - With LIVE on, HOME should refuse with "LIVE ON".
  - While a grip button is held, HOME should refuse with "LET GO".

**Later, not now:**
- [ ] Everything on Windows (on hold, see "What remains" item 4). This includes setting up claude-sync and the Windows half of the tablet: allowing `python.exe` on private networks, and updating both OSes together, because of schema 3.
- [ ] Autostart stays off unless you change your mind (tick **Start at login** in the tray menu).

## Where things stand
- **Phases 1 and 2 and the dual-boot port** (plan Tasks 1–12, PR #3) are merged into `master` (2026-10-22). CI runs on Ubuntu and Windows.
- **What remains:**
  1. **The owner's feedback from the Linux test drive** (below): fixed and merged into master (2026-10-23; usage meter, speed). The next test drive checks the meter against claude.ai and the filing card's live progress.
  2. **The Kubuntu rollout: done on 2026-10-23 except the setup wizard**, which the owner runs in the app (http://127.0.0.1:8090).
     - The live copy `/mnt/data/BrainHub` is on master, with its old branches deleted.
     - Runtime slot A is in `~/.local/share/brainhub`.
     - The widget is built from the live copy into `~/.cache/brainhub-target/release/brainhub-desktop`. The owner starts it by hand: no autostart (their choice).
     - The integration is installed for real; its backup is `~/.claude/settings.json.brainhub-backup-20261023-113616`.
     - A terminal session showed Where = terminal, entrypoint `cli`. Still to check: a VS Code session (Where = IDE; the to-do list, step 2).
     - `claude-sync` waits for Windows (the owner's choice): without the shared store, its runs change nothing.
  3. **Phase 3, the workbench tablet: built and reviewed on branch `phase-3`** (not pushed). Plus the arm repo's `brainhub-home` branch in `~/dev/RobotArmControl` (not pushed; `index.html` only).
     - Design, plan and decisions: `docs/superpowers/specs/2026-10-23-brainhub-phase3-design.md`, `plans/2026-10-23-brainhub-phase3.md`, `phase3-decisions.md`.
     - Checked in headless Chrome over a real LAN site (`app/README.md` → Measured → Phase 3).
     - Reviewed by three subagents after the build (the owner's instruction). Every finding is fixed (decisions T17–T27); 966 pytest and 62 node tests pass. Landing it waits on the owner (the to-do list, step 1).
     - **Its rollout and device checks need the owner:** the to-do list, steps 4 and 5 (design §11; `app/README.md` → Phase 3; `phase3-decisions.md` → Residuals).
  4. **On hold (owner, 2026-10-23): everything on Windows.** The owner uses Kubuntu only for a while and will come back to Windows later. That covers the Windows half of the memory spike (ruling D2, in a fresh Windows clone, never in `D:\BrainHub`), the Windows-side checks (the widget built hidden and then shown, D1; claude-sync's junctions, drive-letter keys and `D:\` defaults for real), the Windows rollout and the Windows half of design §10's verification. Until then: code stays portable, CI keeps running on both OSes (the update step's gate needs both `test (…)` jobs green), and nothing is checked on Windows by hand.
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
