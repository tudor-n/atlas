"""The names ATLAS had before the rename (it was BrainHub; rename spec §1 and §2).

Only the code that finds and replaces our own pre-rename entries uses these: the Claude Code
hooks, status line, `brainhub-request` wrapper and CLAUDE.md block (`claude_integration`), and the
old launcher entry (`desktop_entry`). Nothing is ever written under these names. The update step
and the desktop shell keep their own copies (`update_step.LEGACY_PACKAGE`, `migrate.rs`): the
update step is stdlib-only and runs as a file, and the shell is Rust.
"""

NAME = "brainhub"  # the package, the Linux state and config folder names
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
