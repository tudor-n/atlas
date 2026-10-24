"""The guard on the rename (rename spec §4): the app was BrainHub and is ATLAS now. No tracked file
may hold the old name, and no tracked path, outside the small allow-list below.

The rename script (`rename.sh`, outside the repo) reads EVERYWHERE and ALLOWED from this file too:
it skips the whole files listed with None and never rewrites text matching a listed pattern, so a
re-run after a rebase keeps the migration's own legacy names.
"""
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OLD = re.compile(r"brain[\s_-]?hub", re.IGNORECASE)

# Allowed in any file: the dev copy's real folder, which stays (Claude Code keys its memory by it).
EVERYWHERE = [r"~/dev/BrainHub", r"/home/tudor/dev/BrainHub"]

# A doc may say what something was called: "(was `…`)" or "(pre-rename: …)", in one parenthesis.
_WAS = r"\((?:was|pre-rename:) [^()]*\)"

# Where the old name may stay. None: anywhere in that file. A list: only in text matching one of
# the patterns. Keep it small: the migration and legacy-recognition code and its tests, the
# rename's spec and decisions, and the owner's cut-over steps.
ALLOWED = {
    # The rename's own spec, decisions and this guard.
    "docs/superpowers/specs/2026-10-24-atlas-rename-design.md": None,
    "docs/superpowers/atlas-rename-decisions.md": None,
    "app/tests/test_no_old_name.py": None,
    # The migration and the recognition of our own old entries, and their tests.
    "app/atlas/legacy.py": None,
    "app/tests/test_legacy.py": None,
    "app/desktop/src-tauri/src/migrate.rs": None,
    "app/web/js/legacy-keys.js": None,
    "app/web/js/legacy-keys.test.mjs": None,
    # The update step's legacy-slot fallback (stdlib-only: it can't import atlas.legacy).
    "app/atlas/update_step.py": [r'LEGACY_PACKAGE = "brainhub"'],
    # The docs: what things were called, and the owner's cut-over steps (TODO.md step 8).
    "app/CLAUDE.md": [_WAS],
    "app/README.md": [_WAS],
    "app/desktop/README.md": [_WAS],
    "README.md": [_WAS],
    "NEXT-SESSION.md": [_WAS, r"settings\.json\.brainhub-backup-[\w*-]+"],  # the real backups, made before the rename
    "TODO.md": [_WAS, r"mv /mnt/data/BrainHub /mnt/data/ATLAS", r"~/\.cache/brainhub-target",
                r"~/\.local/share/dev\.brainhub\.desktop", r"~/\.config/dev\.brainhub\.desktop",
                r'"BrainHub" project', r"%LOCALAPPDATA%\\BrainHub", r"D:\\BrainHub", r'"Quit BrainHub first"'],
}


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def leftovers(path: str, text: str) -> list[str]:
    """The lines of `text` that still hold the old name once the allowed text is taken out."""
    rule = ALLOWED.get(path, [])
    if rule is None:
        return []
    allowed = [re.compile(p) for p in EVERYWHERE + rule]
    bad = []
    for n, line in enumerate(text.splitlines(), 1):
        for rx in allowed:
            line = rx.sub("", line)
        if OLD.search(line):
            bad.append(f"{path}:{n}: {line.strip()[:160]}")
    return bad


def test_no_tracked_path_holds_the_old_name():
    assert [p for p in tracked() if OLD.search(p)] == []


def test_no_tracked_file_holds_the_old_name():
    bad = []
    for path in tracked():
        try:
            raw = (REPO / path).read_bytes()
        except OSError:
            continue  # deleted in the working tree
        if b"\0" in raw:
            continue  # binary: icons, fonts
        bad += leftovers(path, raw.decode("utf-8", errors="replace"))
    assert bad == [], "the old name outside the allow-list:\n" + "\n".join(bad)


def test_the_allow_list_names_real_files():
    files = set(tracked())
    assert [p for p in ALLOWED if p not in files] == []


def test_the_guard_catches_what_it_should():
    assert leftovers("app/atlas/x.py", "import brainhub\n")
    assert leftovers("app/atlas/x.py", "BRAINHUB_STATE_DIR\n")
    assert leftovers("app/CLAUDE.md", "Brain Hub says hi\n")
    assert leftovers("app/CLAUDE.md", "see brain-hub#4\n")
    assert not leftovers("app/CLAUDE.md", "the dev copy `~/dev/BrainHub/app`\n")
    assert not leftovers("app/CLAUDE.md", "the state folder `~/.local/share/atlas` (was `~/.local/share/brainhub`)\n")
    assert leftovers("app/CLAUDE.md", "(was `brainhub`) and then brainhub again\n")
    assert leftovers("app/atlas/update_step.py", 'os.path.join(app, "brainhub")\n')
    assert not leftovers("app/atlas/legacy.py", "brainhub\n")
