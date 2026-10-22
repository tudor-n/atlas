# Dual boot: build-time rulings

Rulings made while building the dual-boot plan (`plans/2026-10-22-brainhub-dual-boot.md`) that amend or settle the plan or the design. Newest last. D1 and D2 are reserved for the two spikes the plan names.

## D1: Wayland widget spike (Task 2): run the shell under XWayland
Kubuntu 26.04, Plasma on Wayland, two screens (laptop 1536x864 logical at 125 %, external 1920x1080 at 100 %). Shell built from master's `app/desktop`, run connect-only against a temp hub. Window properties read from KWin with a script; visual checks by screenshot and by the owner.

| Check | Native Wayland (no rule) | XWayland (`GDK_BACKEND=x11`) |
|---|---|---|
| Transparent, frameless, rounded panel | (not reached) | yes |
| Stays above other windows | **no** (opened behind Firefox; `keepAbove` false) | yes (`keepAbove` true, owner confirmed) |
| Not in the taskbar | **no** | yes (`skipTaskbar`, `skipPager`) |
| Not in Alt+Tab | no | **no** (`skipSwitcher` false; owner saw it) |
| Draggable | (not reached) | yes (owner) |
| Expand/collapse resizes | (not reached) | yes (owner) |
| Position remembered | no (apps can't place windows) | yes, after a clean quit (saved 1400,750 physical = 1120,600 logical, restored exactly) |
| Kept on screen | no | yes: parked half off the bottom, it came back at y=656 (above the 48 px panel) |
| Sharp on the 125 % screen | | yes (owner) |
| Tray icon and menu | | yes (StatusNotifierItem, Active; menu items over D-Bus; owner sees it) |

**Ruling: option 1, XWayland.** It meets the parent spec §3.1 target except Alt+Tab, and needs no KWin rule. Option 2 (native plus a KWin rule) was only baseline-tested: the plan prefers option 1 when it works, and native Wayland can't restore or clamp the position by itself. Task 8 must:
- set `GDK_BACKEND=x11` before GTK starts (unless the env already sets it);
- keep the widget out of Alt+Tab: set the GTK window type hint to Utility before the window is mapped (build it hidden, set the hint, then show). If KWin still lists it, fall back to a one-line KWin rule (`skipswitcher`) installed by `desktop-config`;
- quit cleanly on SIGTERM/SIGINT (logout sends SIGTERM): call `app.exit(0)`, so the window-state plugin saves the position and the supervisor stops the hub. Today a killed shell saves nothing.
- `WEBKIT_DISABLE_DMABUF_RENDERER` wasn't needed (no blank window).
- The tray library warns that `libayatana-appindicator` is deprecated; harmless for now.

## D2: Claude Code memory spike (Task 11): Linux half done
Claude Code 2.1.308 on Kubuntu, a scratch project (`/tmp/bh-mem-spike`, key `-tmp-bh-mem-spike`: every non-alphanumeric character becomes `-`, case kept, a `.` too) and a scratch shared folder.
- **Option 1, a symlinked memory folder, fails on Linux.** Claude Code resolves `~/.claude/projects/<key>/memory` to its target, finds it outside the folders it may edit, and refuses to write *and* to read `MEMORY.md` ("blocked … outside the directories I'm allowed to edit"). (The design recorded an NTFS junction working on Windows with 2.1.306.)
- **Option 2, per-project `autoMemoryDirectory` in the project's `.claude/settings.local.json`, works:** the value is the memory folder itself (not a parent). The note and `MEMORY.md` were written there, and a fresh session answered from it.
- **Still open:** the home-session equivalent. A user-level `autoMemoryDirectory` would also apply to every project without its own override (personal memory leaking into new projects). Candidate: `~/.claude/settings.local.json`, read as the home folder's own project-local settings when a session starts in `~`. Testing it touches the real `~/.claude`, so it waits for the owner's OK. Then the Windows half (needs a reboot).

## D3: Legacy integration files are adopted only by install/uninstall (Task 3)
The plan had the integration view (`GET /api/integration`) adopt the legacy Windows files from `data/` too. But moving `statusline_passthrough.txt` on a GET cuts an installed status line off from it: `settings.json` still names `D:/BrainHub/data/statusline_passthrough.txt` until a reinstall, so the owner's own line would quietly stop showing. Only install and uninstall adopt, and both rewrite the status line in the same step. The rollout reinstalls the integration on Windows anyway.

## D4: Windows boot id from the registry boot counter (Task 3, amends P11)
Review found that the boot time rounded to the minute can differ between two processes of one boot (the wall clock drifts against `GetTickCount64` near a minute edge), which would let the hub lock be taken over from a live hub. The boot id now comes from Windows' own boot counter (`PrefetchParameters\BootId`, a DWORD bumped every boot), read-only via `winreg`. The rounded boot minute stays only as a fallback if the value can't be read.

## D5: The root guard needs positive evidence (Task 5 review)
- **A marker without `brain/map.json` is "foreign"**, not ok: the brain went missing. As ok, the full app would run over it, and confirming a proposal would write a new near-empty `map.json` (the anti-goal). Setup never runs there; the banner says to restore `brain/` (Task 6 holds captures meanwhile).
- **Errors are never "absent".** On Python 3.14 `Path.exists()` is False on any `OSError`, so an unreadable or I/O-failing root looked fresh. The guard uses strict `os.stat` checks (only "no such file" means absent), and any `OSError` while classifying gives "missing".
- **The hub lock records the holder's process start time** (`pid_start`: Linux `/proc/<pid>/stat` field 22, Windows `GetProcessTimes`). The shell kills the hub, so stale locks are normal, and a reused pid used to look like a live hub, blocking every start. A lock naming our own pid is stale too.
- `usage.json` and `settings.json` writes create `data/` but never its parents, so a root that vanished mid-run is never recreated.

## D6: Time zone check compares real UTC offsets (amends P5)
`time.altzone` is always `timezone - 3600` on Windows' CPython, even for zones without summer time, so comparing `timezone`/`altzone` across OSes could warn falsely. The marker stores the UTC offsets at noon on 15 January and 15 July (`tm_gmtoff`), and each start compares its own.

## D7: Brain git on two OSes: case rule per OS, filing path guard details (Task 7)
- **Every brain git call sets `core.ignorecase` for the OS running it** (`false` on POSIX, `true` on Windows) with `-c`, whatever the shared `.git/config` says, which is never edited. With Windows' `true`, Linux git didn't see an agent's `robot.md` next to `Robot.md` (the filing dropped it quietly), and the owner-edits sweep committed a case-only rename made on Windows as a deletion (which the backup then pushed). A brain first made on Linux has no `ignorecase`, so Windows git would compare case on NTFS.
- The case-clash check also compares folder names, and folds case as NTFS does (per-character uppercase), not `str.lower()`.
- Reserved names: CON, PRN, AUX, NUL, CONIN$, CONOUT$, COM0-9, LPT0-9 and COM/LPT with a superscript digit, with any extension, in any case.
- Agent rows refresh `os` and `boot` on every event, so a session resumed after a hub restart in the same boot isn't ended as a ghost.
- Leftover worktrees are removed with `worktree remove --force --force`: a power cut during `worktree add` leaves one locked, and a single `--force` (or prune) skips it on every retry.
- The status-line passthrough runs in its own process group (`process_group=0`) but not a new session, so the owner's command keeps the controlling terminal.

## D8: Reduced mode and read-only root, as built (Task 6)
- **`run_server` serves the reduced app; `create_app` still raises `RootUnavailable`.** It raises for missing, foreign and newer roots, and for a read-only root whose `hub.db` can't be read read-only (absent, damaged, or at an older schema that would need a migration). A locked or unopenable `hub.db` on a writable root is not damage: the start stops with a message, nothing is moved, and the shell retries.
- **No re-check while "newer".** Newer data never turns older while this code runs, and with a newer `hub.db` the root itself checks ok, so a watch would restart the hub every 15 s. An update restarts the hub anyway.
- **The watch is `check_root` as is.** A legacy brain (map, no marker) coming back gets its marker from it: the only write, and only to a root that is about to run in full.
- **`POST /api/open` works in every mode.** It writes nothing, and the widget's "Open in BrainHub" stays usable.
- **The read-only view is the last checkpoint.** `immutable=1` doesn't read a `-wal` left by a crash, so the newest index changes can be missing until the next full start. The raw log is untouched either way.
- **Held captures are marked queued when flushed**, as a fresh capture is, so the cap on never-filed entries at start (`MAX_AUTO_REQUEUE`) doesn't turn them into failures.
- **`port_free` binds with `SO_REUSEADDR` on POSIX**, as aiohttp does. Without it the restart right after exit 75 was refused for a minute (the old hub's connections in TIME_WAIT).
- **Manual check** (Kubuntu, temp roots, temp state folder, `claude_path` pointed at a missing command): a missing root showed the banner, held a capture (200), answered Today with 503 and a hook with 204, and was never created. Once a brain appeared there the hub exited 75 after 15 s; the restart put the held capture in its own day's file and queued it. A read-only root (`chmod -R a-w`) served Today and the journal, refused confirm (503), held a capture, and left `hub.db` unchanged with no `-wal`/`-shm`; after `chmod -R u+w` it exited 75 after 15 s and the restart flushed the capture.

## Residuals
- `app/README.md` still says the integration files live in `data/` (Task 12 fixes the docs).
- `map.json` load/change/save isn't serialised across `PUT /api/map`, the Reviewer's `add_node` and the Agents link: two edits at the same moment could lose one (the first two already raced before the dual boot).
- `//server/share` counts as a Linux path (P7 to the letter), so the map editor on Windows calls it a Linux folder; UNC paths in the `\\server\share` form are fine.
- Two hubs started at the same instant on one root can both take the lock; the port check and the supervisor make this unlikely in practice.
- `app["root_status"]` stays "fresh" after setup on the started app (aiohttp freezes it); "fresh" and "ok" show no banner alike, so nothing reads the difference.
