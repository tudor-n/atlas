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

## Residuals
- `app/README.md` still says the integration files live in `data/` (Task 12 fixes the docs).
