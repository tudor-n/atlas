# Dual boot: build-time rulings

Rulings made while building the dual-boot plan (`plans/2026-10-22-brainhub-dual-boot.md`) that amend or settle the plan or the design. Newest last. D1 and D2 are reserved for the two spikes the plan names.

## D1: Wayland widget spike (Task 2)
Pending.

## D2: Claude Code memory spike (Task 11)
Pending.

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

## Residuals
- `app/README.md` still says the integration files live in `data/` (Task 12 fixes the docs).
