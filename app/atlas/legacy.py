"""The names ATLAS had before the rename (it was BrainHub; rename spec §1 and §2).

Only the code that finds and replaces our own pre-rename entries uses these: the Claude Code
hooks, status line, `brainhub-request` wrapper and CLAUDE.md block (`claude_integration`), and the
old launcher entry (`desktop_entry`). Nothing is ever written under these names. The update step
and the desktop shell keep their own copies (`update_step.LEGACY_PACKAGE`, `migrate.rs`): the
update step is stdlib-only and runs as a file, and the shell is Rust.
"""

from pathlib import Path

from . import osdirs

NAME = "brainhub"  # the package, the Linux state and config folder names
WINDOWS_NAME = "BrainHub"  # the Windows state-and-config folder under %LOCALAPPDATA%
# Our own hook client, as whole path components (lowercased, forward slashes): the package's copy
# and a default state folder's stable copy (`~/.local/share/brainhub/hook/`, `%LOCALAPPDATA%\BrainHub\hook\`).
HOOK_SUFFIXES = ("brainhub/hook_client.py", "brainhub/hook/hook_client.py")
# The measurement request command and the mark that says a file of that name is ours.
WRAPPER = "brainhub-request"
WRAPPER_NAMES = ("brainhub-request", "brainhub-request.cmd")
WRAPPER_MARK = "written by `brainhub integration install`"
# The CLAUDE.md block's marker lines.
MD_BEGIN = "<!-- brainhub: measurement requests (managed by `brainhub integration install`) -->"
MD_END = "<!-- /brainhub -->"
# The launcher entry and icon (phase 4.5), and what marks the entry as ours.
LAUNCHER_ID = "dev.brainhub.desktop"
LAUNCHER_ICON_LINE = "Icon=dev.brainhub.desktop"

# The compat copy the shell leaves on Windows in the old state folder after moving it (rename spec
# §2.1): the old absolute hook paths keep working until the hub has repaired the hooks. Linux gets
# a symlink instead. This file marks the folder as that copy, and these are all it may hold.
COMPAT_MARKER = "moved-to-atlas.txt"
COMPAT_FILES = (COMPAT_MARKER, "hook/hook_client.py", "hook/request_client.py", "statusline_passthrough.txt")


def state_dir(os_name: str | None = None) -> Path:
    """The default pre-rename state folder: `${XDG_DATA_HOME:-~/.local/share}/brainhub` on Linux,
    `%LOCALAPPDATA%\\BrainHub` on Windows (no override: the old variable is never read)."""
    if (os_name or osdirs.os_name()) == "windows":
        return osdirs._localappdata() / WINDOWS_NAME
    return osdirs._xdg("XDG_DATA_HOME", ".local/share") / NAME
