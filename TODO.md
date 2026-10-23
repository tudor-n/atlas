# BrainHub: the owner's to-do list

Everything that's waiting on you, in order. Tick each item here as you go.
Last updated: 2026-10-23.

## Now

### 1. Get phase 3 into the live copy
- [x] Merge brain-hub#4 (phase 3) and 6-dof-arm#3 (the HOME button). Both are merged and CI passed.
- [ ] Start the widget. The update step pulls master into `/mnt/data/BrainHub`, which is still on `6057a7e`.
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
- [ ] **Integration:** Settings → Claude Code integration → **Install** again. This adds `brainhub-request`.
- [ ] **Arm:** `git pull` master in `~/dev/RobotArmControl`, then restart `server.py`.

### 5. Check it on the tablet
Tell Claude about anything that fails.
- [ ] Restart Fully Kiosk: the tablet is still paired.
- [ ] The keyboard glyphs `⌫ ↵ ⇧ ⇪ ⌄ ‹ → · − Ω` all render, with no empty boxes.
- [ ] Switching between TODAY, NOTES and AGENTS has no visible lag.
- [ ] Type half a note, then reload: the draft is still there.
- [ ] Send a note with the laptop's Wi-Fi off. It shows "Waiting to send (1)", arrives once when the Wi-Fi is back, and **File this** on the PC files it.
- [ ] Run `brainhub-request "Test" "Width of the desk" "Height of the desk"`. It shows on NOTES within about a second, and the widget says "1 request". Answer it with the unit chips, then SEND. The answer shows in the PC's **Tablet** view.
- [ ] IGNORE a note (two taps within 2 s), then bring it back with **Show ignored** on the PC.
- [ ] ROBOT → arm UI → **‹ HOME** (hold it or tap it twice) brings you back. It refuses with "LIVE ON" while LIVE is on, and with "LET GO" while you hold a grip button.

## Later
- [ ] **Windows (on hold):** the Windows half of the dual-boot checks, claude-sync, the Windows rollout, and the Windows half of the tablet (allow `python.exe` on private networks; update both OSes together because of schema 3).
- [ ] **Autostart** stays off unless you change your mind (tick **Start at login** in the tray menu).
