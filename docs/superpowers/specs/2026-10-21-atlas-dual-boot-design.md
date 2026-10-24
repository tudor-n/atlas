# ATLAS on a dual boot (Kubuntu + Windows): design

Date: 2026-10-21
Status: approved by the owner (confirmed 2026-10-22). The calls marked "not yet heard" in §11 are accepted with it.
Parent spec: `2026-10-20-atlas-design.md`. It stays the authority on what ATLAS does. This document changes **where it runs and how**, now that the laptop dual-boots. It amends the parent spec's §4 (where data lives), §11 (the shell is no longer Windows-only), §13.1 (folders per OS in the map) and §14 (deployment). The phase 2 design and its rulings still apply unless this document overrides them.

Marking: "owner" marks the owner's decisions. **(my call)** marks a call I made; the owner heard it played back and didn't object. **(my call, new)** marks a call made while writing, which the owner hasn't heard yet.

## 1. Intent

The laptop now dual-boots:
- **Kubuntu 26.04** is the daily driver, about 80% of the time. The owner codes there, runs many agents across terminals, tests apps, and hands work to agents while at the electronics bench.
- **Windows 11** is for leisure, light work, gaming and video.

ATLAS must be **one lifeline across both** (owner). Whichever OS is booted:
- it shows the same brain, Today, To review, items, history, usage and agents;
- it behaves the same way.

The owner's words: "same exact lifeline there". This has to hold **from day 1**, meaning from the first real capture, with no migration later.

**Claude Code itself** must also be the same on both (owner): the owner's memory, the per-project notes, skills, plugins and settings.

**Development** happens mainly on Kubuntu. Windows remains a supported place to run ATLAS and an occasional place to develop it.

**Why now:**
- No real brain exists yet, because the setup wizard has never run. So where the data lives can still be decided before there is any data.
- A Linux test run already fails 10 tests on Windows assumptions.

## 2. Goals, non-goals, anti-goals

**Goals**
- **One data root**, used by whichever OS is booted. Something captured on one OS is there after a reboot into the other. Nothing is lost or duplicated.
- **The same features on both OSes:**
  - hub, full app, widget, tray, start at login, self-healing;
  - the Agents panel and usage meter, fed by that OS's Claude Code sessions;
  - the integration installer and the backup push.
  - Later phases (tablet, voice, calendar) arrive working on both.
- **Only master is used** (owner). It is the only branch that goes back and forth and runs as the live copy, on both OSes.
- **Every change is tested on both OSes** before it can reach master.
- **Claude Code is the same on both:**
  - one copy of the home memory, the per-project memories and the personal skills;
  - the same plugins and portable settings;
  - a separate login and session history per OS.
- **Switching needs no ritual.** Forgetting a step never strands, splits or corrupts data. The only rule is that dev work is committed and pushed before a reboot (owner).

**Non-goals**
- Running both OSes at once, live sync, or a second machine. It is still one laptop with one OS at a time.
- Carrying uncommitted dev work across a reboot (owner).
- A KDE-native widget (plasmoid). The same widget runs on both OSes **(my call)**.
- WSL, VMs, or a server. The `workbench` home server is not part of this design.
- Sharing the Claude Code login, session history, caches or plugin installs.
- Moving the owner's other projects. Code stays in `~/dev` on Kubuntu and on `D:\` on Windows, as the Linux setup left it.

**Anti-goals.** It has failed, even if it technically works, when:
- the two OSes show different things, or the owner has to remember which OS they captured something on;
- a switch needs a manual step (sync, export, copy);
- a missing or read-only drive makes the hub quietly start an empty brain, or drops a capture;
- it adds chores: prompts, popups or extra steps (the parent spec's "more of a chore than paper");
- a Claude session on one OS acts on a note that is only true on the other OS, without saying so.

## 3. Facts this design rests on (checked 2026-10-21)

| Fact | Source |
|---|---|
| Lenovo LOQ 15IRX10: i7-14700HX, 32 GB RAM, RTX 5050 + Intel UHD. Kubuntu uses PRIME on-demand, so the desktop runs on the iGPU. | `D:\linux-setup\HANDOFF.md` |
| One 1 TB NVMe (GPT): p1 EFI 200 MB, p3 `C:` NTFS 463.5 GB, p5 `D:` NTFS 238.3 GB ("New Volume", about 152 GB free), p6 Kubuntu btrfs 250.7 GB. **`D:` is the only partition both OSes can use.** | `Get-Partition`, `Get-Volume` |
| Kubuntu 26.04 LTS with KDE Plasma and Secure Boot on. `D:` is mounted at `/mnt/data` via fstab: `ntfs3`, `uid=1000,gid=1000,windows_names,nofail`, plus a `~/Data` symlink. | HANDOFF |
| On Kubuntu, code lives in `~/dev` (btrfs), on purpose: NTFS is slow for git, node and file watchers. `~/dev/BrainHub` was rsynced from `D:\ATLAS`. | HANDOFF |
| Windows: Fast Startup off (`HiberbootEnabled=0`), hibernation off, hardware clock in UTC (`RealTimeIsUniversal=1`), BitLocker off. Time zone is `GTB Standard Time` (Europe/Bucharest). | registry; setup plan |
| Linux has written to `D:` and Windows can see it (`D:\linux-write-test.txt`). | file on `D:` |
| Git: Windows 2.52 with system `core.autocrlf=true`. Kubuntu has global `core.autocrlf=input`. The app repo has no `.gitattributes`. | `git config` |
| Kubuntu toolchains: uv (Python 3.12 and 3.14), nvm Node 24, rustup, gh. | HANDOFF |
| On Kubuntu, ATLAS has 424 tests passing and 10 failing: 8 from the fake `claude` being a `.bat`, 1 from `normcase`, 1 from a cmd-syntax passthrough. The audit lists them. | Linux setup run; code audit |
| `D:\ATLAS` is a clean checkout, now on local branch `dual-boot` (`phase-2` plus this document). **`master` has none of phase 2** (28 commits behind) until the owner merges the PR. | git |
| `brain/` and `data/` don't exist anywhere yet. | `D:\ATLAS` listing |
| Claude Code keeps auto memory per project under `~/.claude/projects/<key>/memory/`, where the key is derived from the folder path. Windows has a home memory (`C--Users-Tudor`, 6 notes) and notes for 10 projects, e.g. `d--RobotArmControl` (4) and `D--tracegreen` (4). Kubuntu has its own home memory from the setup session. | `~/.claude` listing; §7.1 |

## 4. What is shared and what stays per OS

| Thing | Where | Shared? |
|---|---|---|
| **Live copy** of the code (repo root; master only): the single source each OS runs from | `D:\ATLAS` = `/mnt/data/ATLAS` | shared |
| **Runtime copy**: what the hub actually runs; an exact copy of the live copy's commit, refreshed at every start (§5.1) | Kubuntu `~/.local/share/atlas/app`; Windows `%LOCALAPPDATA%\ATLAS\app` | per OS |
| **Brain** (the git-versioned Markdown) | `D:\ATLAS\brain` | shared |
| **Hub data** (`hub.db`, `usage.json`, `settings.json`, worktrees; later pairing tokens and calendar tokens) | `D:\ATLAS\data` | shared |
| **Claude Code shared store**: memory, skills, user `CLAUDE.md`, portable settings (§7) | `D:\ClaudeShared` **(my call, new: name)** | shared |
| Python venv | Kubuntu `~/.local/share/atlas/venv`; Windows `%LOCALAPPDATA%\ATLAS\venv` **(my call, new)**. Never on `D:`; today's `app\.venv` moves out. | per OS |
| Per-OS hub state: integration state and status line passthrough (today in `data/`, which is a bug, see §6.2), the holding inbox (§6.5), logs | Kubuntu `~/.local/share/atlas/`; Windows `%LOCALAPPDATA%\ATLAS\` | per OS |
| Desktop shell config (`desktop.json`) | Kubuntu `~/.config/atlas/`; Windows `%LOCALAPPDATA%\ATLAS\` | per OS |
| Desktop shell build output and binary | each OS's own `CARGO_TARGET_DIR` on local disk, never `src-tauri/target` on `D:` | per OS |
| Start at login | Kubuntu `~/.config/autostart/*.desktop`; Windows HKCU Run (both via the Tauri autostart plugin) | per OS |
| Claude Code login, sessions, history, caches, plugin installs, `~/.claude/settings.json` | each OS's `~/.claude` | per OS (settings kept in step, §7) |
| Owner's other project code | Kubuntu `~/dev/<name>`; Windows `D:\<name>` | per OS (paired by name, §6.4 and §7.3) |

## 5. Code: the live copy, dev copies and updates

### 5.1 One live copy, master only (owner)
- The live copy is the checkout at `D:\ATLAS`, which Kubuntu sees as `/mnt/data/ATLAS`.
  - **It only ever holds master** and is never edited by hand.
  - It is the single source of code for both OSes. It also carries code between them without the network: whatever one OS pulled, the other finds on `D:`.
- **Each OS runs the hub from its own runtime copy** on local disk (§4) **(my call, new, after the builder check)**.
  - At every start, before the hub launches, the update step (§5.2) makes the runtime copy exactly match the live copy's commit.
  - Both OSes still run the same commit against the shared data whenever the drive is there.
  - **Why:** if the hub ran straight from `D:`, then with `D:` missing it couldn't even import. The reduced mode would be impossible: banner, capture box, holding inbox (§6.5).
  - The venv's editable install, `web/`, the prompts and the hook client (§8.2) all resolve inside the runtime copy.
  - How the copy is made (a local clone fetched from the live repo, or a file copy of the tracked tree) is **left to the builder**.
- The per-OS parts are the runtime copy, the venv and the shell binary (§5.3). The update step keeps the first two in lockstep with the live copy.
- **The dev copy** is `~/dev/BrainHub` on Kubuntu (btrfs).
  - Branches, tests and spikes happen there, and it only ever runs against temp roots (existing rule 9).
  - Occasional Windows development happens in a separate clone on Windows, never in the live copy.
- **GitHub carries code between OSes.** Dev work is committed and pushed before a reboot (owner).

### 5.2 Updating the live copy (my call)
Before it starts the hub, the shell's supervisor runs one update step, in three parts.

1. **Pull the live copy.** It updates only when all of these hold:
   - `D:` is available;
   - the live copy is on master with a clean working tree;
   - `origin/master` can be reached and is a fast-forward of the local master;
   - **CI passed on both OSes for that exact commit** (§5.5).
   - It then runs `git pull --ff-only`.
   - It never merges, resets, stashes or switches branches.
2. **Refresh the runtime copy** from the live copy's commit, whether or not part 1 pulled anything.
3. **Sync the venv** if the dependencies changed.

**Parts 2 and 3 switch together.** The new runtime copy and its venv are prepared side by side and swapped in only when both are ready. If the venv can't be synced (typically offline after the other OS pulled a dependency change), this OS keeps its previous runtime copy and venv, and Settings says why.

**Any skipped part is quiet:** offline, dirty tree, other branch, diverged history, CI not green or unreadable, `D:` missing, or any error. The hub then starts on the runtime copy it already has, and Settings shows "update pending (reason)".

**Worst case:** an offline OS whose runtime copy is older than data the other OS already upgraded. The data-version guard (below) then puts the hub in reduced mode: banner, capture box, holding inbox. Nothing is lost, and the full app is back once that OS is online.
- **Optional, left to the builder:** when the updating OS syncs its venv, it also fills a wheelhouse on `D:` with the other OS's wheels, so the other OS can sync offline.
- **Data-version guard (my call, new):**
  - `hub.db` gets a schema version (`PRAGMA user_version`), and the brain gets a format version in `data/`.
  - Code meeting data newer than it understands refuses to open it and says so, e.g. a dev copy accidentally pointed at the real root.
  - Code meeting older data migrates it forward.
  - Today there is no version and no migration mechanism (`store.py` only does `CREATE TABLE IF NOT EXISTS`).

### 5.3 Per-OS build parts
- **Venv:** one per OS, on local disk (§4). The update step keeps it in sync (§5.2).
- **Desktop shell:**
  - Each OS builds the shell from the live copy's `app/desktop` into its own local `CARGO_TARGET_DIR` (two OSes sharing `src-tauri/target` on NTFS would keep overwriting each other's builds).
  - When the shell's source has changed since this OS's binary was built, Settings and the tray show "widget update available".
  - Rebuilding is one command. Whether it rebuilds by itself, or CI ships binaries, is **left to the builder**.
  - A stale shell must keep working with a newer hub; the hub never depends on a matching shell.

### 5.4 Git hygiene for a checkout two OSes share
- **Line endings.** The app repo gets a `.gitattributes` that pins text files to LF (e.g. `* text=auto eol=lf`, with binary types marked). Otherwise Windows (`autocrlf=true`) and Kubuntu (`input`) disagree about the shared working tree.
- **Repo-local config on the live copy:** `core.filemode=false`, because ntfs3 reports execute bits.
- **Ownership checks.** Every git call the hub or the update step makes passes `-c safe.directory=<path>` for the live copy and for `brain/`. Otherwise git may refuse files the other OS created, with a "dubious ownership" error.
- **Index refresh.** The first `git status` after a switch re-reads every file, because git caches per-OS file metadata. This is slow, not wrong, and fine at this size.

### 5.5 CI on both OSes (my call)
- GitHub Actions runs on every push and PR, on both `ubuntu-latest` and `windows-latest`:
  - pytest;
  - the node tests;
  - `cargo test`, plus a build check of the shell.
- **The gate is in the update step, not in GitHub settings (my call, new, after the builder check).**
  - Part 1 of §5.2 fast-forwards only to a master commit whose CI runs passed on both OSes.
  - It reads that status through `gh`, logged in on each OS (already on Kubuntu; Windows needs `gh` installed once).
  - If the status can't be read, it doesn't update.
  - So a master that breaks one OS never reaches the live copy, even without branch protection. On a private repo, branch protection needs GitHub Pro (included in the Student Developer Pack).
  - If the owner's account has Pro, they may also turn on "require CI green" on master. It's optional.
- **Keeping CI within the free tier** (Windows minutes count double):
  - run on PRs and on pushes to master, not on every branch push;
  - build the Rust shell only when `app/desktop/**` changes;
  - cache the toolchains.
  - The builder confirms the minutes fit.
- **Making the suite portable is part of the work:**
  - the fake `claude` becomes cross-platform;
  - OS-specific expectations become per-OS cases, not `win32` skips where a Linux equivalent exists;
  - no test creates `nul` or other names Windows can't handle.

## 6. Data: one root, used by whichever OS is booted

### 6.1 Layout
This is unchanged from parent §4: `brain/` and `data/` under the root, plus the live copy's `app/` next to them. The hub runs with `--root D:\ATLAS` on Windows and `--root /mnt/data/ATLAS` on Kubuntu.

### 6.2 Nothing shared may depend on one OS (requirement)
Nothing in `brain/` or the shared part of `data/` may hold an absolute path, a command, or any state that only works on one OS. From the audit:
- **Integration state is per-OS and lives in `data/` today, which is a bug that can cause config loss:**
  - `data/claude_integration.json` and `data/statusline_passthrough.txt` describe one OS's `~/.claude/settings.json`.
  - Installing on the second OS can adopt the first OS's status line.
  - Uninstalling on either OS deletes both files, so the other OS can no longer restore its own status line.
  - Fix: move both files to the per-OS state folder (§4).
- **`claude_path`** in `data/settings.json`: an absolute Windows path would break every filing on Kubuntu. Fix: either make it per OS, or keep the bare name `claude` and resolve it on each OS at startup.
- **Filing worktrees** record absolute paths, but they are temporary, and a leftover from the other OS is already pruned and rebuilt at startup. Keep it that way, and test it explicitly (§10).
- **`brain/map.json`** is written without a fixed newline, so it flips between CRLF and LF on every switch. Write it LF always, as the raw log already is.
- **Already safe:** `usage.json`, `_items/items.json`, `proposals.json`, `filings.files` and the FTS references hold no absolute paths.

### 6.3 The brain repo across two OSes
- **Already right:** `core.autocrlf=false` and `* -text` in `brain/.gitattributes` (the raw log's `len=` stays exact).
- **Add:**
  - `core.filemode=false` set explicitly, in case the repo is first created on Kubuntu;
  - `safe.directory` on every hub git call (§5.4);
  - clearing any stale `*.lock` under `brain/.git` at startup when no git process is running. Today only `index.lock` is cleared, so a leftover `refs/heads/main.lock` after a hard power-off would block every commit.
- **Names that are legal on Linux but not on Windows are the top risk of quiet loss.** These are reserved characters, a trailing dot or space, reserved device names, and two names that differ only by case.
  - Windows git would see such a file as deleted. The next "owner edits" commit (`git add -A`) would record the deletion, and the backup would push it.
  - `windows_names` on the mount covers characters and reserved names, but not case.
  - **Requirement:** before a filing's result is cherry-picked, the hub rejects any new or renamed path that is invalid on Windows, or that matches an existing path in a different case. The filing then fails visibly into To review, as other filing failures do. The filing prompt also tells the agent to use lowercase slugs.
- **The backup push works from both OSes.** Each OS needs non-interactive credentials for the private brain repo:
  - Windows keeps Git Credential Manager;
  - Kubuntu uses HTTPS through `gh auth setup-git` **(my call, new)**, so Plasma's SSH passphrase dialog never appears;
  - pushes over SSH get `BatchMode=yes`.

### 6.4 Projects with a folder on each OS
- **Map format.** `brain/map.json` gets folders per OS for each project, e.g. robotarm → Windows `D:\RobotArmControl`, Linux `/home/tudor/dev/RobotArmControl`. The exact format is left to the builder; existing single-list maps keep working.
  - Matching uses the current OS's folders, with that OS's own rules: case-insensitive with either separator on Windows, exact on Linux.
- **The setup wizard and Settings** show both OS columns. The owner adds the current OS's folder; the other column can be typed now or added later from that OS.
- **Name-match suggestion (my call):** a session in an unmapped folder may have the same folder name as a mapped project on the other OS (`~/dev/RobotArmControl` vs `D:\RobotArmControl`). Its Agents row then offers a one-click "link to robotarm". Nothing is ever mapped automatically.

### 6.5 Guards
1. **Root identity (my call, new).**
   - The root holds a marker file with a random id that the setup wizard creates.
   - **The hub and its shell never create a root by themselves.** If the configured root has no marker, the hub doesn't start a fresh brain. It shows the drive banner instead (item 2).
   - Creating a new brain is an explicit setup-wizard action on an empty folder.
   - On Kubuntu the hub also refuses a root whose mount point (`/mnt/data`) isn't actually mounted, because an empty mount point would otherwise look like an empty folder.
2. **Drive not available or read-only.**
   - When Windows crashes, loses power or is force-rebooted by an update, NTFS is left marked dirty. With `nofail`, Kubuntu boots, but `D:` isn't mounted (or is read-only).
   - **The hub stays up in a reduced mode** and shows a plain banner: "ATLAS's drive isn't available. Boot Windows once and shut it down normally; Windows checks and repairs the drive. Then come back."
   - **Not mounted:** the capture box works (item 3); everything else shows the banner.
   - **Read-only:** Today, journal and recall work read-only; captures are held (item 3); confirm and tick are disabled with the reason shown.
   - **Never run `ntfsfix` or any repair automatically** **(my call, new)**. It can lose data; the repair belongs to Windows' own check.
   - **The `hub.db` damage handler must not misfire.** Today any `sqlite3.DatabaseError` moves `hub.db` aside and rebuilds it, including "locked", "read-only" and "unable to open".
     - Narrow it to real corruption only.
     - On a read-only root, open the database read-only, or skip it and show the banner, rather than crash-looping.
3. **Captures are never lost (owner anti-goal; my call).**
   - While the root isn't writable, each capture goes to this OS's **holding inbox** (per-OS state folder):
     - written verbatim, with its original timestamp and source;
     - flushed to disk **before** the UI says "saved".
   - When the root is writable again, the hub moves held entries into the raw log, keeping their original timestamps. Each lands in its own day's file; the log stays append-only. They are then filed normally, so their confirm cards appear at that point.
   - The banner shows "N captures held on this OS".
   - **Accepted gap:** the held captures live on the OS where they were typed. Windows can't see Kubuntu's holding inbox while the owner is in Windows repairing the drive. They arrive at the next Kubuntu boot.
4. **Fast Startup watch (my call).**
   - At start on Windows, the hub checks Fast Startup and hibernation.
   - If either is back on (feature updates sometimes do this), the widget and Settings show a quiet warning with the one-line fix (`powercfg /h off` as admin). It is never a popup.
5. **One hub per root (my call, new).**
   - A lock file in `data/` records the OS, the boot id and the pid.
   - A lock written by the other OS is always stale (only one OS runs at a time), so it is taken over.
   - A lock written by this OS, in this boot, by a live process means another hub is running. Refuse, e.g. a dev copy started against the real root.
   - This replaces relying on the port probe alone.
6. **Clean stop.**
   - On Kubuntu, the supervisor stops the hub with SIGTERM before SIGKILL (today it's SIGKILL straight away). aiohttp already shuts down cleanly on SIGTERM.
   - Claude runs are started in their own process group, so stopping one also stops its tool children (today only `taskkill /T` does this, on Windows).
   - A leftover `-wal` after a power cut recovers normally on either OS.
7. **Clock and time zone.**
   - Both OSes keep the hardware clock in UTC (done).
   - The setup check confirms Kubuntu's time zone is `Europe/Bucharest`, matching Windows. The hub's timestamps are naive local time, so a time zone mismatch would skew every entry after a switch.

### 6.6 Clean-up when the hub starts on either OS
- **Agents:** rows from sessions that were alive before this boot are marked ended at startup, so there are no ghost "running" rows (today they linger up to 12 hours). Rows record which OS they came from; it is shown in the row's tooltip.
- **Filings cut off mid-run:** these are already re-queued, and leftover worktrees are already pruned. This covers worktrees from the other OS.
- **Usage:** the last known values carry over (same account), and their age is shown.
- **Backup:** a pending push is retried.

## 7. Claude Code parity

### 7.1 How it should behave (owner)
- **A home session** (`claude` started in the home folder) loads the owner's personal memory.
- **A project session** (a terminal in a project folder, or the IDE extension) never sees the personal memory. It loads only that project's own notes. The per-project notes stay (owner).
- **It works the same on both OSes, with the same notes.**

This is already how Claude Code behaves on a single machine. Auto memory lives in `~/.claude/projects/<key>/memory/` (index `MEMORY.md`), and the key comes from the folder path. The paths differ between the OSes (`C:\Users\Tudor` vs `/home/tudor`; `D:\RobotArmControl` vs `~/dev/RobotArmControl`), so each OS files the same project under a different key. Parity therefore means pointing both keys at one shared copy.

**Facts (Claude Code docs at code.claude.com/docs, read 2026-10-21; Claude Code 2.1.306)**
- **Location and key.** Auto memory is `~/.claude/projects/<key>/memory/` with a `MEMORY.md` index.
  - The key is derived from the git repository root, or from the project root outside a repo.
  - Worktrees and subfolders of one repo share it.
  - The docs show the encoding only for transcripts: every character that isn't a letter or digit becomes `-`, and case is kept (`/home/user/work/my-repo` → `-home-user-work-my-repo`).
  - Windows already has both `d--…` and `D--…` keys for the same drive, depending on how the path was typed.
- **Loading.** At session start, the first 200 lines or 25 KB of `MEMORY.md` load. Topic files are read on demand.
- **Machine-local.** The docs say memory "is not shared across machines".
- **`autoMemoryDirectory`** is a documented setting (user, project, local or `--settings`; an absolute path or one starting with `~/`).
  - The docs **don't** say whether it is the memory folder itself or a parent of per-project folders.
  - Set at user level, it may put every project into one folder, which would break the home/project split.
- **Links.**
  - Documented: a personal skill entry (`~/.claude/skills/<name>`) may be a symlink to a folder elsewhere.
  - **Not documented:** linking a whole memory folder or the whole `skills/` folder.
  - The docs list a "Refusing to write into symlinked directory" error for the Write tool.
  - **Tested here (Windows, 2.1.306):** a Write through an NTFS **junction** succeeded and landed in the target. Linux symlinks are untested.
- **Plugins.** `enabledPlugins` and `extraKnownMarketplaces` in user settings are documented to reproduce marketplace plugins: missing ones are fetched at session start. Install folders hold per-machine paths, so they are never copied.
- **No user-level overlay file.** There is no user-level `settings.local.json` and no include mechanism. Each OS's `~/.claude/settings.json` is the one user layer.

**Spike first (gates §7.2–7.4).** On both OSes, pick the first of these that reads **and** writes memory correctly in a real home session and a real project session:
1. **Linked memory folders:** a junction on Windows, a symlink on Kubuntu.
2. **Per-project `autoMemoryDirectory`** in each project's untracked `.claude/settings.local.json` on each OS, plus a tested equivalent for the home session.
3. **Mirroring:** copy in at login, and the desktop shell mirrors changes to the shared store while it runs.

The rest of §7 is written for option 1. Options 2 and 3 change only the "linking" step.

### 7.2 The shared store on `D:` (my call)
```
D:\ClaudeShared\                 (= /mnt/data/ClaudeShared)
  memory/home/                   both OSes' home-folder memory
  memory/projects/<name>/        one per paired project
  skills/                        the owner's personal skills
  CLAUDE.md                      user-level instructions, if the owner adds any
  settings.shared.json           portable settings: model, effort, enabledPlugins,
                                 extraKnownMarketplaces, tui, notification and update prefs
  pairs.json                     project name -> folder on each OS (exceptions only)
```
- **Linking.** Each OS's `~/.claude` points at the shared folders: symlinks on Kubuntu, directory junctions on Windows (no admin needed).
  - Skills are linked **one skill folder at a time** (`~/.claude/skills/<name>`), which is the documented form, not the whole `skills/` folder.
  - Skills that come from plugins or claude.ai sync (e.g. `skills/synced`) are left alone.
- **Single files** (`CLAUDE.md`) are linked, or kept in step by copy, whichever works on each OS without admin. On Windows a file symlink needs admin or Developer Mode. **Left to the builder.**
- **Backup (owner).**
  - `D:\ClaudeShared` is a git repository: `* -text`, `core.autocrlf=false` and `core.filemode=false`, plus `safe.directory` on every call (as for the brain, §6.3).
  - Changes are committed in quiet batches: by the desktop shell while it runs (at most every few minutes) and by the login check. They are pushed to a **new private GitHub repo**, the same way the brain's backup works:
    - non-interactive;
    - retried quietly when offline;
    - a failure shows as a quiet note in ATLAS Settings, never a popup.
  - The owner creates the repo (nothing is published without asking), and it uses the same per-OS git credentials as the brain push.
  - Session data never enters it; only the store's own files are committed.

### 7.3 Pairing projects across OSes (my call)
- A project is the same on both OSes when its folders have the same name, e.g. `D:\RobotArmControl` and `~/dev/RobotArmControl`. The Linux setup copied projects under their original names.
- `pairs.json` lists only the exceptions.
- Windows sometimes records the same folder under two keys that differ only in drive-letter case (`d--` vs `D--`). Pairing treats them as one.
- ATLAS's folder→project map (§6.4) may feed pairing, but **pairing must not depend on the hub running**.

### 7.4 The login check (my call)
- **When it runs:** at every login on each OS, before the shell starts the hub. In a dual boot, every switch is a fresh login.
- **What it does:**
  1. **Links:** makes sure the home memory and every paired project with notes on either OS are linked to the shared store.
  2. **Merges an unlinked folder** when this OS has a real folder with notes:
     - files the shared copy lacks are copied in;
     - two files with the same name but different content are both kept, the local one renamed with an OS suffix, and the owner is told;
     - `MEMORY.md` indexes are merged line by line, with duplicates dropped;
     - the local folder is backed up to the per-OS state folder, then replaced by the link.
   - **It never deletes anything else.**
  3. **Settings sync both ways, per key (my call, new, after the builder check).**
     - **Shared by default.** Every key in `~/.claude/settings.json` is shared, except a per-OS list: `hooks`, `statusLine`, `permissions` (today's allow-list holds Windows paths), `env`, `defaultShell`, `apiKeyHelper`, and any other key the builder finds holding a path. The owner can extend the list in ATLAS Settings. So today's `model`, `effortLevel`, `modelSettings`, `enabledPlugins`, `extraKnownMarketplaces`, `tui`, `autoUpdatesChannel`, `remoteControlAtStartup` and the `skip…Prompt` keys are all shared.
     - **Changes flow out from where they're made.**
       - While the desktop shell runs, it watches this OS's `settings.json`. When a shared key changes (`/model`, effort, `/plugin install`, disabling a plugin), it records the change in `settings.shared.json` within seconds, with the time it was made.
       - At login, the check first records any changes made since the last sync, for when the shell wasn't running. Then it applies the store.
     - **Conflicts:** per key, the later change wins.
     - **Removals propagate.** A plugin disabled or removed on one OS is disabled or removed on the other. ("Never delete" applies to memory notes and skills, not settings.)
     - **New personal skills:** a skill folder created on one OS is moved into the store and linked, by the watcher or at the next login.
     - Writes back up `settings.json` and replace it atomically, the same as ATLAS's installer, which keeps managing the hooks and status line.
  4. **Plugins:** nothing extra.
     - `enabledPlugins` and `extraKnownMarketplaces` are shared keys, and Claude Code fetches missing marketplace plugins itself at session start.
     - Plugins or MCP servers that need per-OS setup (e.g. the `rea` reverse-engineering server and its tool paths) stay per OS. The check notes them; it doesn't manage them.
- **It is silent:**
  - no prompts or popups;
  - problems go to a one-line log and a quiet note in ATLAS Settings;
  - if `D:` isn't available, it changes nothing.
- **Where it lives (my call, new):**
  - inside the ATLAS repo as its own module (e.g. `python -m atlas claude-sync`), with no dependency on the hub or its data;
  - run by the desktop shell at login, so each OS has one autostart entry and nothing else to install;
  - it can also run by hand.

### 7.5 One-time merge and clean-up (my call)
- **The first run on each OS merges** that OS's existing memories: the Windows home memory (6 notes), Kubuntu's home memory from the setup session, and the 10 Windows project memories.
- **Notes true on one OS only get labelled**, e.g. "(Windows only)" on the GlassFish workaround and on "Rust isn't on PATH", so a Kubuntu session won't act on them.
- **Going forward (owner): one line at the top of every shared `MEMORY.md` index**, kept there by the login check. For example: "Shared by Windows and Kubuntu. Label any note that is only true on one OS: (Windows only) / (Linux only)."
  - It loads only where memory loads. It never reaches ATLAS's own filing agents, and nothing goes into a user-level `CLAUDE.md` for this.
  - The check puts the line back if it goes missing, and never duplicates it.
- **The ATLAS memory note is rewritten for the new layout:** live copy, dev copy, roots on both OSes.
- `NEXT-SESSION.md` stops being the only carrier between OSes.

### 7.6 Never shared
- `.credentials.json`: the owner signs in once on each OS.
- Sessions, `history.jsonl`, `file-history`, caches, `ide/`, `daemon*`.
- The plugin install folders. The same plugins are installed separately on each OS.

## 8. Each OS's experience

### 8.1 Hub and desktop shell on Kubuntu
- **The same Tauri v2 shell**, built for Linux (WebKitGTK) **(my call)**. Porting work from the audit:
  - `open_browser` uses `cmd /C start` and isn't OS-gated, so tray → Open does nothing on Linux. Use the platform's opener (`xdg-open`).
  - `config.rs` reads `%LOCALAPPDATA%`, so on Linux it never finds `desktop.json` and never spawns a hub. Use XDG paths.
  - `desktop_config.py` crashes without `LOCALAPPDATA`. It needs XDG paths and the Linux venv layout (`bin/python`).
  - The supervisor's stop becomes SIGTERM, then SIGKILL.
  - `hub.log` moves to the per-OS state folder, so a missing drive can still be diagnosed.
  - Before starting the hub, the supervisor runs the update step (§5.2) and the root guards (§6.5).
- **The tray** uses StatusNotifierItem on KDE through Tauri's appindicator support. The builder verifies it on the machine.
- **Wayland spike first. This gates the rest of the shell work.**
  - Kubuntu's Plasma session is Wayland. There, apps can't keep themselves always on top, skip the taskbar or set their position. So today's always-on-top, skip-taskbar, saved position and on-screen clamp would all do nothing.
  - **Option 1:** run the shell under XWayland (`GDK_BACKEND=x11`).
  - **Option 2:** stay native Wayland and use a KWin window rule, installed by the setup step, to force keep-above, skip-taskbar and position.
  - **Target:** the parent spec's §3.1 widget on both OSes: transparent, frameless, always on top, draggable, remembered position, kept on screen, legible over any background.
  - **If neither option meets the target fully, stop and bring it to the owner** (my call; the owner heard this).
- **WebKitGTK and NVIDIA.** The desktop runs on the iGPU, so the usual WebKitGTK blank-window problem isn't expected. The fallback is `WEBKIT_DISABLE_DMABUF_RENDERER=1`.

### 8.2 Claude Code integration on Kubuntu
- **The installer writes Linux paths:** this OS's venv python and a hook client path.
- **The hook client must never live on `D:`.**
  - If the hooks pointed at `hook_client.py` inside the live copy and `D:` were missing, python would fail to find the script, and Claude Code would show a hook error on every event. That breaks parent §7.2 ("must not ... show errors").
  - The hooks therefore point at the hook client inside this OS's runtime copy (§5.1), which is on local disk.
  - The installer must write a path that survives the runtime-copy swap in §5.2, e.g. a stable path rather than a versioned folder.
- **The status line** runs through `sh` on Kubuntu. The passthrough command is per OS (§6.2).
- **`WHERE_BY_ENTRYPOINT`.** Kubuntu's values for the terminal and VS Code are checked on the machine, like Windows' (parent §12 checklist).
- **The port is the same on both OSes**, so hooks, the widget and later the tablet use one address.

### 8.3 Requirements for later phases (not built now)
- **Tablet (phase 3).**
  - It reaches the hub at **one fixed address whichever OS is booted**: a DHCP reservation on the router for the laptop's Wi-Fi MAC, or the same static IP on both OSes.
  - Both OSes must use the hardware MAC on the home network, not a randomised one.
  - The SM-T110 can't resolve `.local` names, so a hostname isn't enough.
  - Each OS's firewall allows the port.
  - Pairing tokens live in shared `data/`, so the tablet pairs once.
- **Voice (phase 4)** plays on both OSes.
- **Google Calendar (phase 5).** OAuth tokens live in shared `data/`, so it's authorised once. Queued offline changes survive a switch.
- **Robot arm tile.** It probes whichever OS is running the arm server.

## 9. Getting to day 1 (my order; the owner heard it)
1. **The owner merges `phase-2` into master** on GitHub.
2. **The port**, on Kubuntu in the dev copy, on a branch from master. This document's commit comes over from `D:\ATLAS`'s local `dual-boot` branch. Order:
   1. CI on both OSes, with a portable test suite (§5.5), so everything after it is tested on both.
   2. Wayland widget spike (§8.1). Stop and ask if it falls short.
   3. Fix the 10 Windows assumptions; make shared data OS-neutral (§6.2); move per-OS state out of `data/`; per-OS folders in the map (§6.4).
   4. Guards and clean-up (§6.5, §6.6), git hygiene (§5.4, §6.3), data-version guard (§5.2).
   5. Shell, supervisor, `desktop-config` and the integration installer on Linux; runtime copies and the update step with its CI gate (§5.1, §5.2, §5.5, §8).
   6. Claude Code parity (§7), starting with its memory spike on both OSes. The spike needs a reboot into Windows, so it is the one step that has to be scheduled.
   7. Docs: `app/CLAUDE.md`, the READMEs, and a replacement for `NEXT-SESSION.md`. Then the owner's merge.
3. **Set up Kubuntu:**
   - move the live copy `/mnt/data/ATLAS` to master;
   - create the venv, build the shell, install the integration, run the first `claude-sync` (which merges the home memories);
   - check the time zone and the git credentials;
   - then the **setup wizard**, with both OS columns for project folders. The first real capture lands on the shared drive.
4. **Set up Windows:**
   - move the venv out of `app\.venv`, build the shell, install the integration, run `claude-sync`;
   - re-check Fast Startup.
   - The wizard doesn't run again: Windows opens the same brain.
5. **Verification** (§10) on both OSes.

## 10. Verification
- **CI** is green on both OSes.
- **Round trip:**
  - capture on Kubuntu;
  - reboot: on Windows it's in Today and the journal, and recall finds it; confirm an item and tick another;
  - reboot: both show on Kubuntu;
  - `git status` is clean in `brain/` and in the live copy on both OSes.
- **On each OS:**
  - terminal and VS Code sessions appear in Agents with the right Where;
  - the usage meter updates;
  - the widget is transparent, on top, draggable, remembers its position and stays on screen;
  - the tray works;
  - the hub and widget come up after a reboot.
- **Drills,** run against a test root or a scratch mount, never the real root:
  - **Mount point empty** (`D:` not mounted): banner shown, no new brain created, a capture is held, then it reaches the raw log with its original timestamp once the root is back.
  - **Root read-only:** reading works, captures are held, confirm and tick are disabled with the reason, and `hub.db` is not moved aside.
  - **Hub killed mid-filing, then the other OS boots on the same root:** the filing is recovered, no ghost agents remain, git is clean, and stale locks are cleared.
  - **A filing proposing a Windows-invalid or case-clashing path:** it is rejected into To review, and nothing is deleted on Windows.
  - **Fast Startup turned back on:** a quiet warning appears on Windows.
  - **Dev copy started against the live root while the live hub runs:** refused.
  - **Integration installed on both OSes, then uninstalled on one:** the other OS's status line and hooks are untouched.
- **Claude Code parity:**
  - a note written in a home session on Kubuntu is in the next home session on Windows; the same for a project note;
  - skills and plugins match on both;
  - a Windows-only note is labelled.
- **Updates:**
  - a trivial change merged on GitHub is picked up at the next start on each OS;
  - an offline start runs the current code quietly;
  - a dirty live copy is left alone, with a note in Settings;
  - a commit without green CI on both OSes is not pulled;
  - with `D:` unmounted, the hub still starts from the runtime copy in reduced mode;
  - if one OS pulled a dependency change and the other boots offline, the other keeps its previous runtime copy and venv; if the data is already newer, it runs in reduced mode, and nothing crashes.

## 11. Decisions

**The owner's**
- Kubuntu is the main dev machine. Windows is a supported runtime and an occasional dev seat.
- One lifeline: identical data and behaviour on both OSes from day 1.
- Only master goes back and forth and runs live.
- Unfinished dev work doesn't follow a reboot; commit and push first.
- Claude Code is the same on both OSes: memory, skills, plugins and settings.
  - Home session = personal memory. Project session (terminal or IDE) = that project's notes only.
  - Per-project notes are kept and shared.
- `D:\ClaudeShared` is backed up to its own private GitHub repo, the same way as the brain (§7.2).
- One-OS notes get labelled going forward through one line at the top of each shared memory index, not a user-level `CLAUDE.md` (§7.5).

**Mine, heard by the owner:**
- one shared root on `D:` with no sync, and the live copy on `D:`; per-OS venv, shell, settings and autostart;
- self-update to master at start (fast-forward only, quiet when it can't);
- CI on both OSes gating master;
- missing-drive guard, holding inbox, Fast Startup watch, clean-up at switch;
- folders per OS in the project map, with a name-match suggestion;
- the same widget on both OSes; Wayland checked first; stop and ask if it can't match;
- one network address for the tablet;
- Claude Code parity through a shared store on `D:` and a login check; merges never delete; one-OS notes are labelled.

**Mine, made after the builder check (not yet heard):**
- **each OS runs from a local runtime copy**, refreshed from the live copy at every start, so the hub works without `D:` (§5.1);
- the runtime copy and venv swap together; if a dependency can't be synced offline, keep the previous pair (§5.2);
- the CI gate lives in the update step, read through `gh` (§5.5);
- Claude Code settings sync both ways per key: shared by default, a per-OS deny-list, later change wins, removals propagate (§7.4).

**Mine, made while writing (not yet heard):**
- the names and places: `D:\ClaudeShared`, runtime copies, per-OS venvs and state folders;
- the data-version guard;
- root marker and "never create a root implicitly";
- never auto-repairing NTFS;
- the lock-file takeover rule;
- `claude-sync` living in the ATLAS repo, run by the shell;
- HTTPS plus `gh` credentials for the brain push on Kubuntu.

**Left to the builder:**
- exact formats (per-OS map folders, root marker, lock file, holding inbox, `pairs.json`);
- the Wayland approach (within §8.1);
- how shell rebuilds happen (§5.3);
- CI details;
- how `CLAUDE.md` is kept in step on Windows;
- which memory mechanism wins the spike (§7.1), within the behaviour §7 describes;
- the read-only database mode.

## 12. Risks
- **ntfs3 writing NTFS.** It's the newest moving part.
  - The brain is git and pushed to the private remote.
  - `_items/items.json` and `proposals.json` sit in the brain, and `hub.db` can already be rebuilt from `brain/`.
  - So even a damaged `D:` loses at most the unpushed batch. The push interval stays short (parent §14).
  - The same holds for `D:\ClaudeShared` through its own private repo (§7.2).
- **Windows updates** may turn Fast Startup back on. The watch in §6.5 item 4 covers this.
- **Wayland limits on the widget.** The spike in §8.1 covers this, with an explicit stop-and-ask.
- **Claude Code may change** how it keys or stores memory. `claude-sync` detects layouts it doesn't recognise and reports them; it never guesses.
- **Unknown entrypoint values** on Kubuntu (VS Code). They are checked on the machine.
