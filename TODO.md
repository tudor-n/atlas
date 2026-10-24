# ATLAS: the owner's to-do list

Everything that's waiting on you, in order. Tick each item here as you go.
Last updated: 2026-10-24 (phase 4.5 added; phases 5 and 6 on hold; the rename to ATLAS added as step 8).

**About the names:** the app is now called ATLAS (was BrainHub). The steps below already use the new names (`/mnt/data/ATLAS`, `~/.cache/atlas-target`, `atlas-request`…). They only exist once you've done **step 8**, so do step 8 as soon as its PR is merged, then carry on with whatever is still open above it.

## Now

### 1. Get phase 3 into the live copy
- [x] Merge #4 (phase 3) and 6-dof-arm#3 (the HOME button). Both are merged and CI passed.
- [ ] Start the widget. The update step pulls master into `/mnt/data/ATLAS`, which is still on `6057a7e`.
- [ ] Check that Settings now has a **Tablet** section.

### 2. Finish the Kubuntu rollout
- [ ] Run the setup wizard at http://127.0.0.1:8090. You don't have a real brain until you do.
- [ ] Open Claude Code in VS Code: its Agents row should say **Where = IDE**. This passed on the dev copy; this check is for the real install.

### 3. Test drive on the real brain
- [ ] The Usage meter matches claude.ai's usage page (`fable_usage_url`), and isn't ~2 points low.
- [ ] While something gets filed, its card shows live steps ("Filing… writing personal/errands.md · 5 s").
- [ ] Tell Claude whether filing still feels slow. If it does, the next fix is "fewer agent turns".

### 4. Set up the tablet
Details: `app/README.md` → Phase 3.
- [ ] **Router:** reserve `192.168.0.200` for the laptop's Wi-Fi MAC `24:b2:b9:cd:c5:69`. Reconnect the Wi-Fi, then check that `ip -4 addr` shows that address.
- [ ] **Firewall:** `sudo ufw allow from 192.168.0.0/24 to any port 8090 proto tcp`
- [ ] **Settings → Tablet:** press "Use … (now)", tick **Tablet over Wi-Fi**, then Save. It should say "Listening on http://192.168.0.200:8090/tablet".
- [ ] **Fully Kiosk:** set the start URL to `http://192.168.0.200:8090/tablet`, and turn on **DOM storage** (web settings).
- [ ] **Pair:** press **Pair a device**, then type the 6-digit code on the tablet within 5 minutes.
- [ ] **Integration:** Settings → Claude Code integration → **Install** again. This adds `atlas-request`.
- [ ] **Arm:** `git pull` master in `~/dev/RobotArmControl`, then restart `server.py`.

### 5. Check it on the tablet
Tell Claude about anything that fails.
- [ ] Restart Fully Kiosk: the tablet is still paired.
- [ ] The keyboard glyphs `⌫ ↵ ⇧ ⇪ ⌄ ‹ → · − Ω` all render, with no empty boxes.
- [ ] Switching between TODAY, NOTES and AGENTS has no visible lag.
- [ ] Type half a note, then reload: the draft is still there.
- [ ] Send a note with the laptop's Wi-Fi off. It shows "Waiting to send (1)", arrives once when the Wi-Fi is back, and **File this** on the PC files it.
- [ ] Run `atlas-request "Test" "Width of the desk" "Height of the desk"`. It shows on NOTES within about a second, and the widget says "1 request". Answer it with the unit chips, then SEND. The answer shows in the PC's **Tablet** view.
- [ ] IGNORE a note (two taps within 2 s), then bring it back with **Show ignored** on the PC.
- [ ] DUMMY → arm UI → **‹ HOME** (hold it or tap it twice) brings you back. It refuses with "LIVE ON" while LIVE is on, and with "LET GO" while you hold a grip button.

### 6. Phase 4: the voice
- [x] Merge the phase 4 PR (#5). Done on 2026-10-23.
- [ ] Merge the Windows fix PR (#6) once Claude marks it ready. Master's CI hangs on Windows until then, and the update step won't pull master while CI isn't green on both OSes.
- [ ] Restart the widget so the update pulls phase 4 in.
- [ ] Rebuild the widget so the tray gets **Pause voice**: in `/mnt/data/ATLAS/app/desktop/src-tauri`, run `CARGO_TARGET_DIR=~/.cache/atlas-target cargo build --release --locked`, then restart the widget. Settings will say "widget update available" until you do.
- [ ] Settings → **Voice** → **Download the voice** (about 121 MB, once).
- [ ] Press **Play sample**. It should sound like Atlas. Tell Claude if the sentences sound unnatural.
- [ ] Hear one of each:
  - a permission prompt in any Claude Code session ("… needs you");
  - an agent that works for 2 minutes or more ("… has finished. It took …");
  - a reminder due a minute ahead;
  - a tablet note ("Note saved. …").
- [ ] **Over music** (2026-10-24): play Spotify loud, then press Settings → Voice → **Play sample**. The voice should carry, and the music should dip while it speaks and come back after. Tune **Volume** and **Other sounds while speaking** to taste.
- [ ] Tray → **Pause voice** → For 1 hour: nothing is spoken. Then **Resume**.
- [ ] The widget's speaker icon switches the voice off and on.
- [ ] Settings → Voice → **Edit quotes**: read through the quips and change or delete what doesn't land.

### 7. Phase 4.5: the native app and the new look (after its PR is merged)
- [ ] Merge the phase 4.5 PR on GitHub once CI is green, then restart the widget so the update pulls it in.
- [ ] Rebuild the desktop shell. In `/mnt/data/ATLAS/app/desktop/src-tauri`, run `CARGO_TARGET_DIR=~/.cache/atlas-target cargo build --release --locked`. Then **Quit** from the tray and start it again. Settings says "widget update available" until you do. (Step 8 does this too: skip it if you've done step 8.)
- [ ] Add ATLAS to the KDE launcher (step 8 does this too). The exact command is in step 8, item 7. If it doesn't show up in the menu, run `kbuildsycoca6`.
- [ ] Start **ATLAS** from the launcher. A black window should say "STARTING THE HUB…", then show the app.
- [ ] Check the taskbar: the entry has the ATLAS icon and groups with the launcher entry. If it doesn't, run `xprop WM_CLASS`, click the window, and tell Claude what it prints.
- [ ] Alt+Tab lists the main window but not the widget.
- [ ] Resize or maximise the window, then Quit and restart. The window comes back the same size; the widget only keeps its position.
- [ ] Click a widget tile, for example AGENTS. The main window comes forward on that page.
- [ ] Closing the main window only hides it, and reopening is instant. The tray's **Quit** really quits.
- [ ] A login autostart, or a second launch without `--open`, shows only the widget. The tray's **Open in browser** still works.
- [ ] **The widget follows the window** (2026-10-24): open ATLAS from the menu, and no widget shows. Minimise the window, and the widget appears; click ATLAS in the taskbar, and it goes. Close the window, and both are gone (the tray icon stays).
- [ ] **The new widget:** expand it (▾). It shows capture, then one NEEDS YOU list (it scrolls past six rows), a status strip and OPEN ATLAS. Clicking a row opens that page in the app. Tell Claude what still feels awkward.
- [ ] Use it for a few days. Then tell Claude what still feels off, screen by screen.
- [ ] On the tablet:
  - **‹ HOME** is now top left. It shows as `‹`, not an empty box.
  - The home screen shows two tiles, **DUMMY** and **ATLAS**, each with its logo, and both logos look crisp at arm's length.
  - The 22px letters on the keys read well at arm's length.
  - The `2 REQUESTS` chip sits in the top bar.

### 8. The rename to ATLAS
The app is called ATLAS now. Most of the switch happens by itself the first time the new ATLAS starts: it moves its own settings folders and fixes your Claude Code hooks. You only rename the big data folder and rebuild the app. Copy each command into a terminal (Konsole) as it is.
- [ ] **Merge the PRs on GitHub, in this order:** #6 (the Windows fix), #7 (phase 4.5), then the rename PR. Then rename the repo on GitHub: the repo's **Settings** → **General** → **Repository name** → `atlas` (or another name you like: then use it below instead of `atlas`).
- [ ] **Quit the widget**, if it's running: tray icon → **Quit**.
- [ ] **Rename the data folder** (it holds your brain and the live copy of the code):
  `mv /mnt/data/BrainHub /mnt/data/ATLAS`
- [ ] **Point the live copy at the renamed repo, then update it:**
  `git -C /mnt/data/ATLAS remote set-url origin https://github.com/tudor-n/atlas.git`
  `git -C /mnt/data/ATLAS pull --ff-only`
  Then the same for your dev copy (its folder keeps its old name, that's fine):
  `git -C ~/dev/BrainHub remote set-url origin https://github.com/tudor-n/atlas.git`
- [ ] **Build the new app** (takes a few minutes):
  `cd /mnt/data/ATLAS/app/desktop/src-tauri && CARGO_TARGET_DIR=~/.cache/atlas-target cargo build --release --locked`
- [ ] **Start it once from the terminal**, so it can move its folders and build its new runtime (give it a minute or two):
  `~/.cache/atlas-target/release/atlas-desktop`
  Leave that terminal open; the widget appears when the hub is up. Open a new terminal tab (Ctrl+Shift+T) for the next step. If the tray menu says "connect only", tell Claude and paste the end of the log: `tail -n 30 ~/.local/share/atlas/hub.log`.
  - Settings → Runtime may say **update pending: hook client copy failed** this once. That's expected (the old update step looked for the hook in the old place); it clears at the next start.
  - If the log says "Quit BrainHub first", the old app is still running somewhere: quit it from its tray icon, then start ATLAS again.
- [ ] **Add ATLAS to the KDE launcher.** This one line finds the runtime ATLAS just built and installs the launcher entry with it. It also removes the old launcher entry and icon (was BrainHub):
  `cd ~/.local/share/atlas/runtime/slot-$(sed -n 's/.*"slot": "\([ab]\)".*/\1/p' ~/.local/share/atlas/runtime/current.json)/app && ../venv/bin/python -m atlas desktop-entry install --exec ~/.cache/atlas-target/release/atlas-desktop --live /mnt/data/ATLAS; cd ~`
  With `--live`, the launcher keeps ATLAS on master by itself: each start from the menu pulls master (once CI passed) and rebuilds the app when the code changed, with an "Updating ATLAS" notification. No more `cargo build` by hand.
  If ATLAS doesn't show up in the menu, run `kbuildsycoca6`.
  If it says **No module named atlas**, the first start couldn't build the new runtime yet (no internet for the download, for example). Quit ATLAS from the tray, start it once more from the first terminal (the same `~/.cache/atlas-target/release/atlas-desktop` line), wait a minute, check the end of the log with `tail ~/.local/share/atlas/hub.log`, then run the launcher line again. Tell Claude if it still fails.
- [ ] **Quit it from the tray** (the first terminal gets its prompt back; close it), **and from now on start ATLAS from the launcher.** Check that:
  - Settings → Claude Code says **installed** (not "out of date");
  - a new Claude Code session shows up in **Agents**;
  - `atlas-request "Test" "Width of the desk"` works (it shows on the tablet's NOTES).
- [ ] **Optional tidy-up:**
  - Settings → Map: if you made a "BrainHub" project in the wizard, rename it to ATLAS.
  - Delete the old build and the old web view folders (the window positions are remembered again after one start):
    `rm -rf ~/.cache/brainhub-target ~/.local/share/dev.brainhub.desktop ~/.config/dev.brainhub.desktop`
- [ ] **Windows (on hold):** the same folder rename on Windows, `D:\BrainHub` → `D:\ATLAS`, then build the app there. ATLAS moves `%LOCALAPPDATA%\BrainHub` by itself on its first start.

## Later
- [ ] **Windows (on hold):** the Windows half of the dual-boot checks, claude-sync, the Windows rollout, and the Windows half of the tablet (allow `python.exe` on private networks; update both OSes together because of schema 3).
- [ ] **Phases 5 (Google Calendar) and 6 (phone) are on hold** until the phases so far prove useful (your call, 2026-10-24).
- [ ] **Autostart** stays off unless you change your mind (tick **Start at login** in the tray menu).
