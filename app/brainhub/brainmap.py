from __future__ import annotations

import copy
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_DEFAULT_AREAS = ("general", "projects", "uni", "personal")
# Top-level folders the brain already uses. Only the area entries with the four default slugs may use those.
_RESERVED = ("log", "_items", "people") + _DEFAULT_AREAS
# A project's folders per OS (Plan decision P7): {"windows": [...], "linux": [...]}.
OS_NAMES = ("windows", "linux")
_WINDOWS_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


@dataclass
class BrainMap:
    areas: list[dict] = field(default_factory=list)
    projects: list[dict] = field(default_factory=list)
    courses: list[dict] = field(default_factory=list)
    people: list[dict] = field(default_factory=list)


def slugify(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(c for c in folded if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")


def default_map() -> BrainMap:
    return BrainMap(areas=[{"slug": s, "name": s.capitalize()} for s in _DEFAULT_AREAS])


def path_os(folder) -> str | None:
    """The OS a folder path belongs to, by its shape: a drive letter or UNC path is Windows, a
    leading "/" is Linux. Anything else (relative, blank) belongs to neither and is ignored."""
    if not isinstance(folder, str):
        return None
    if _WINDOWS_PATH.match(folder):
        return "windows"
    return "linux" if folder.startswith("/") else None


def folders_for(project: dict, os_name: str) -> list[str]:
    """A project's folders on one OS: its `os_folders` list for that OS, then the legacy `folders`
    entries whose shape belongs to that OS (P7). Tolerant of a hand-edited map.json."""
    per_os = project.get("os_folders")
    own = per_os.get(os_name) if isinstance(per_os, dict) else None
    out = [f for f in own if isinstance(f, str)] if isinstance(own, list) else []
    legacy = project.get("folders")
    if isinstance(legacy, list):
        out += [f for f in legacy if path_os(f) == os_name]
    return out


def _os_folder_errors(slug, os_folders) -> list[str]:
    if not isinstance(os_folders, dict):
        return [f"project {slug!r}: os_folders must be an object with windows and linux lists"]
    errors = []
    for key, folders in os_folders.items():
        if key not in OS_NAMES:
            errors.append(f"project {slug!r}: os_folders has an unknown OS {key!r} (use windows or linux)")
        elif not isinstance(folders, list) or not all(isinstance(f, str) for f in folders):
            errors.append(f"project {slug!r}: os_folders.{key} must be a list of folder paths")
        else:
            # Catches a Windows path typed into the Linux column and the reverse.
            errors += [f'project {slug!r}: "{f}" is not a {key} folder path (os_folders.{key})'
                       for f in folders if path_os(f) != key]
    return errors


def validate_map(m: BrainMap) -> list[str]:
    errors: list[str] = []
    for label, nodes in (("area", m.areas), ("project", m.projects),
                         ("course", m.courses), ("person", m.people)):
        seen: set[str] = set()
        for n in nodes:
            slug = n.get("slug", "")
            if not isinstance(slug, str) or not _SLUG.match(slug):
                errors.append(f"{label} slug is invalid: {slug!r}")
            if slug in seen:
                errors.append(f"duplicate {label} slug: {slug}")
            seen.add(slug)
            if isinstance(slug, str) and slug in _RESERVED and not (
                    label == "area" and slug in _DEFAULT_AREAS):
                errors.append(f"{label} slug is reserved: {slug}")
            if not n.get("name"):
                errors.append(f"{label} {slug!r} has no name")
    for p in m.projects:
        if "os_folders" in p:
            errors += _os_folder_errors(p.get("slug", ""), p["os_folders"])
    if sum(1 for p in m.projects if p.get("is_company")) > 1:
        errors.append("at most one project can be the company")
    known = {p.get("slug") for p in m.projects}
    for person in m.people:
        for ref in person.get("projects", []):
            if ref not in known:
                errors.append(f"person {person.get('slug')!r} references unknown project: {ref}")
    return errors


def _to_dict(m: BrainMap) -> dict:
    return {"areas": m.areas, "projects": m.projects, "courses": m.courses, "people": m.people}


def load_map(brain: Path) -> BrainMap | None:
    f = Path(brain) / "map.json"
    if not f.exists():
        return None
    d = json.loads(f.read_text(encoding="utf-8"))
    return BrainMap(areas=d.get("areas", []), projects=d.get("projects", []),
                    courses=d.get("courses", []), people=d.get("people", []))


def save_map(brain: Path, m: BrainMap) -> None:
    errors = validate_map(m)
    if errors:
        raise ValueError(errors)
    brain = Path(brain)
    brain.mkdir(parents=True, exist_ok=True)
    # LF always: both OSes write this file, and it must not flip between CRLF and LF in git.
    with open(brain / "map.json", "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(_to_dict(m), indent=2, sort_keys=True, ensure_ascii=False))


def ensure_folders(brain: Path, m: BrainMap) -> None:
    brain = Path(brain)
    dirs = [brain / "general", brain / "personal", brain / "uni", brain / "projects",
            brain / "people", brain / "log", brain / "_items"]
    dirs += [brain / "uni" / c["slug"] for c in m.courses]
    dirs += [brain / "projects" / p["slug"] for p in m.projects]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def add_node(m: BrainMap, ntype: str, name: str, parent: str | None) -> BrainMap:
    """Return a new map with the node added. For a person, `parent` is an optional project slug;
    one that is not a project in the map (a course, a typo) is left out rather than failing."""
    new = copy.deepcopy(m)
    slug = slugify(name)
    if ntype == "project":
        target, node = new.projects, {"slug": slug, "name": name, "is_company": False, "folders": []}
    elif ntype == "course":
        target, node = new.courses, {"slug": slug, "name": name}
    elif ntype == "person":
        link = parent if parent in {p["slug"] for p in new.projects} else None
        target, node = new.people, {"slug": slug, "name": name, "projects": [link] if link else []}
    elif ntype == "area":
        target, node = new.areas, {"slug": slug, "name": name}
    else:
        raise ValueError(f"unknown node type: {ntype}")
    if any(n["slug"] == slug for n in target):
        raise ValueError(f"duplicate {ntype} slug: {slug}")
    target.append(node)
    errors = validate_map(new)
    if errors:
        raise ValueError(errors)
    return new


def add_os_folder(m: BrainMap, slug: str, os_name: str, folder: str) -> BrainMap:
    """Return a new map with `folder` added to project `slug`'s folders on `os_name`."""
    new = copy.deepcopy(m)
    project = next((p for p in new.projects if p.get("slug") == slug), None)
    if project is None:
        raise ValueError(f"unknown project: {slug}")
    per_os = project.setdefault("os_folders", {})
    if not isinstance(per_os, dict) or not isinstance(per_os.setdefault(os_name, []), list):
        raise ValueError(_os_folder_errors(slug, per_os))
    if folder not in per_os[os_name]:
        per_os[os_name].append(folder)
    errors = validate_map(new)
    if errors:
        raise ValueError(errors)
    return new


def _count_md(folder: Path) -> int:
    return sum(1 for _ in folder.rglob("*.md")) if folder.is_dir() else 0


def hierarchy(brain: Path, m: BrainMap, item_counts: dict[str, int]) -> dict:
    brain = Path(brain)

    def node(name, slug, kind, files, children=None):
        return {"name": name, "slug": slug, "kind": kind, "files": files,
                "items": item_counts.get(slug, 0), "children": children or []}

    areas = []
    for a in m.areas:
        folder = brain / a["slug"]
        children: list[dict] = []
        if a["slug"] == "projects":
            children = [node(p["name"], p["slug"], "project", _count_md(folder / p["slug"]))
                        for p in m.projects]
        elif a["slug"] == "uni":
            folder = brain / "uni"
            children = [node(c["name"], c["slug"], "course", _count_md(brain / "uni" / c["slug"]))
                        for c in m.courses]
        areas.append(node(a["name"], a["slug"], "area", _count_md(folder), children))
    people = [node(p["name"], p["slug"], "person",
                   1 if (brain / "people" / f"{p['slug']}.md").exists() else 0)
              for p in m.people]
    areas.append(node("People", "people", "people", _count_md(brain / "people"), people))
    root = node("brain", "brain", "root", _count_md(brain), areas)
    root["items"] = sum(item_counts.values())
    return root
