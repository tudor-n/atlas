# Next session: start here

Written 2026-10-21, at the end of the phase 2 build. The owner was setting up a Linux dual boot on this laptop, so this file carries everything the Windows-only Claude memory held. Read it first, then `app/CLAUDE.md`, which holds the project rules.

## Where things stand
- **Phase 1** (capture, filing, review, Today, recall) is merged into `master`.
- **Phase 2** (usage meter, Agents panel via Claude Code hooks, integration installer, widget, Tauri desktop shell) is **built and pushed on branch `phase-2`. It is not merged yet.**
  - The owner merges on GitHub. Open the PR at <https://github.com/tudor-n/brain-hub/compare/master...phase-2?expand=1>. `gh` isn't installed on the Windows side.
  - At push time, all tests passed: pytest 436, node 25, cargo test 14. `cargo build --release` passed with no warnings.
  - A real e2e with Claude Code 2.1.306 passed. The figures are in `app/README.md` under "Measured".
- **No real brain exists yet.** `D:\BrainHub\brain` and `D:\BrainHub\data` were never created, because the owner hasn't run the setup wizard. Nothing outside git can be lost.

## What the owner still has to do (in order)
1. **Merge `phase-2`** via the PR link above.
2. **First real run** (Windows), from `D:\BrainHub\app`:
   - `py -3 -m venv .venv`, then `.venv\Scripts\python -m pip install -e ".[test]"` (only if `.venv` is gone);
   - `.venv\Scripts\python -m brainhub --root D:\BrainHub`, then open <http://127.0.0.1:8090>;
   - complete the setup wizard: areas, projects and their folders (e.g. `D:\RobotArmControl` → robotarm), courses, people, quiet hours, and the optional backup remote.
3. **Install the Claude Code integration.** Either use Settings → Claude Code integration → Install, or run `.venv\Scripts\python -m brainhub integration install --root D:\BrainHub`.
   - It edits `~/.claude/settings.json` (hooks plus status line) after making a backup next to it. Remove undoes it.
4. **Desktop widget:**
   - `.venv\Scripts\python -m brainhub desktop-config --root D:\BrainHub`;
   - build with `cargo build --release` in `app\desktop\src-tauri` (Rust is in `%USERPROFILE%\.cargo\bin`, which is not on PATH);
   - run `app\desktop\src-tauri\target\release\brainhub-desktop.exe`, and tick "Start at login" in the tray.
   - See `app/desktop/README.md`.
5. **Verification checklist.** The full list is in `app/README.md`; it is design §12. Only the owner's machine can answer these:
   - Do terminal, VS Code and desktop-app sessions each appear in Agents?
     - Where shows "terminal" for `CLAUDE_CODE_ENTRYPOINT=cli`.
     - The VS Code and desktop values are guesses (`claude-vscode`, `claude-desktop`). If Where says "unknown", read the raw entrypoint from the row's tooltip and fix `WHERE_BY_ENTRYPOINT` in `app/brainhub/agents.py`.
   - Does the status line, and so the Usage page, update from VS Code or desktop sessions, or only from the terminal?
   - Does Claude Design in the desktop app show up? If not, it stays excluded.
   - Do the widget and hub come up after a reboot with Start at login ticked?
6. **Calibration** (open since phase 1): after real use, collect cases of "wrote X, it extracted Y, expected Z". Then tune `app/brainhub/prompts/filing.md` and the Settings defaults.
7. **Phase 3** (spec §12): the workbench tablet (SM-T110, ES5 only), LAN binding and pairing, measurement requests, and the HOME link in the arm UI.

## If the next session runs on Linux
- The app was built and tested **only on Windows**.
- **The hub** (Python 3.12+, aiohttp, git, the `claude` CLI logged in) should run on Linux. Expect two differences:
  - Map project folders are Windows paths.
  - `/api/open` falls back to `webbrowser.open`.
- **Don't reuse the Windows `.venv`.** Create a new one on Linux.
- **The desktop shell (`app/desktop`) is Windows-oriented:** `CREATE_NO_WINDOW`, `cmd /C start`, `%LOCALAPPDATA%`, and the venv layout `Scripts\python.exe`.
  - Building it on Linux needs Tauri's Linux prerequisites (webkit2gtk etc.).
  - Its supervisor paths would need porting. Treat that as new work, not a quick fix.
- `hook_client.py` and the installer would need Linux-path checks before being used against a Linux `~/.claude/settings.json`. The tests use temp paths, so they are safe to run.
- To reach the repo from Linux, clone `https://github.com/tudor-n/brain-hub.git` (the NTFS `D:` partition may also be mountable). Don't run two hubs on the same root.

## Standing rules (from the owner and the Windows memory)
- **Commits:** plain messages, never `Co-Authored-By` or `Claude-Session` trailers.
- **Nothing is pushed or published without asking.** The owner merges PRs on GitHub.
- **Never run the hub against the real root** (`D:\BrainHub`) in tests or manual checks. Use a temp root and delete it afterwards.
- **Never write the real `~/.claude/settings.json`** or `%LOCALAPPDATA%\BrainHub\desktop.json` from tests or manual checks.
  - Manual desktop runs leave `%APPDATA%\dev.brainhub.desktop` (window state) and `%LOCALAPPDATA%\dev.brainhub.desktop` (WebView2) behind. Delete them afterwards.
- **No `CLAUDE.md` at the repo root**, because the filing agent's worktrees live under it. Start dev sessions in `app/`.
- **The owner's anti-goal:** "more of a chore than paper". Keep it simple and fast, and put every tunable in Settings.
- **How the owner likes to work:**
  - brainstorm, then a spec, then a plan, then subagent-driven execution with reviews;
  - concise answers, with decisions surfaced plainly.

## Key docs
| File | What it is |
|---|---|
| `app/CLAUDE.md` | Hard rules, code map, run and test commands |
| `docs/superpowers/specs/2026-10-20-brainhub-design.md` | The parent spec (the authority); all six phases |
| `docs/superpowers/specs/2026-10-21-brainhub-phase2-design.md` | The phase 2 design; §2 holds the verified facts about hooks and the status line |
| `docs/superpowers/plans/2026-10-21-brainhub-phase2.md` | The phase 2 plan (11 tasks) |
| `docs/superpowers/phase2-decisions.md` | Rulings 1–19 (where the build deviated from the plan or design) and the known residuals |
| `docs/superpowers/phase2-build-ledger.md` | The raw task-by-task build ledger: reviews, fix rounds, deferred minors |
| `docs/superpowers/phase1-decisions.md` | The phase 1 rulings and residuals |
| `app/README.md` | Run, install, phase 2 usage, the verification checklist, known limitations, measured timings |
| `app/desktop/README.md` | Building and running the Tauri shell |

## Windows machine facts (as of 2026-10-21)
- Python 3.14 at `C:\Users\Tudor\AppData\Local\Programs\Python\Python314`, with the project venv in `D:\BrainHub\app\.venv`.
- Node 24; Claude Code 2.1.306 at `C:\Users\Tudor\.local\bin\claude.exe`.
- Rust 1.99.1 (MSVC), installed by rustup via winget in `%USERPROFILE%\.cargo\bin`; remove with `rustup self uninstall`.
- VS 2022 Community with the C++ tools, Windows SDK 10.0.26100, and WebView2.
- Claude Code facts verified for this project: HTTP hooks show errors when the hub is down, so command hooks are used instead. `CLAUDE_CODE_ENTRYPOINT` is `cli` in the terminal and `sdk-cli` under `claude -p` (Claude Code overrides that env var). `--settings <file>` hooks fire under `-p`.
