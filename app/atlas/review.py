"""Owner review actions: confirm / dismiss / edit proposals and items. Every change is committed."""
from __future__ import annotations

import functools
import threading
from datetime import date, datetime, timedelta
from typing import Callable

from .brainmap import BrainMap, add_node, default_map, ensure_folders, load_map, save_map, slugify
from .config import Paths, load_settings
from .filing import valid_date
from .gitrepo import BrainRepo
from .models import Item, Proposal
from .store import Store

KINDS = ("task", "deadline", "reminder", "waiting", "next_time")
STATUSES = ("open", "to_read", "done", "dismissed", "snoozed")
_NODE_TYPES = ("area", "project", "course", "person")
# Nodes apply in this order, so a person can link to a project proposed in the same confirm.
_NODE_ORDER = {"area": 0, "project": 1, "course": 2, "person": 3}
_ITEM_KEYS = ("kind", "text", "area", "project", "person", "due", "remind_at")
_PROPOSAL_KEYS = {
    "item": _ITEM_KEYS,
    "update": ("status", "note"),
    "node": ("type", "name", "parent"),
}
_UPDATE_STATUSES = ("to_read", "done")
_ITEM_PATCH_KEYS = _ITEM_KEYS + ("status", "snooze_until")


def _default_remind(due: str, lead_days: int) -> str:
    if len(due) == 10:
        return (date.fromisoformat(due) - timedelta(days=lead_days)).isoformat()
    return (datetime.fromisoformat(due) - timedelta(days=lead_days)).strftime("%Y-%m-%dT%H:%M")


def _validate_fields(fields: dict, m: BrainMap) -> None:
    """Validate the item-shaped fields present in `fields`. Raises ValueError."""
    slugs = {"area": {a["slug"] for a in m.areas},
             # an item's "project" may also be a uni course
             "project": {p["slug"] for p in m.projects} | {c["slug"] for c in m.courses},
             "person": {p["slug"] for p in m.people}}
    if "kind" in fields and fields["kind"] not in KINDS:
        raise ValueError(f"invalid kind: {fields['kind']!r}")
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"invalid status: {fields['status']!r}")
    if "text" in fields and not (isinstance(fields["text"], str) and fields["text"].strip()):
        raise ValueError("text must be a non-empty string")
    if "area" in fields and fields["area"] not in slugs["area"]:
        raise ValueError(f"unknown area: {fields['area']!r}")
    for key in ("project", "person"):
        if fields.get(key) is not None and fields[key] not in slugs[key]:
            raise ValueError(f"unknown {key}: {fields[key]!r}")
    for key in ("due", "remind_at", "snooze_until"):
        if fields.get(key) is not None and not valid_date(fields[key]):
            raise ValueError(f"invalid date for {key}: {fields[key]!r}")


def _serialized(method):
    """One review action at a time: two concurrent confirms of the same proposal must not
    both pass the "still proposed" check and create the item twice."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class Reviewer:
    def __init__(self, paths: Paths, store: Store, repo: BrainRepo,
                 on_commit: Callable[[str | None], None]):
        self.paths = paths
        self.store = store
        self.repo = repo
        self.on_commit = on_commit
        self._lock = threading.Lock()

    def _map(self) -> BrainMap:
        return load_map(self.paths.brain) or default_map()

    def _finish(self, message: str) -> None:
        self.store.export_items(self.paths.brain)
        self.on_commit(self.repo.commit_all(message))

    @_serialized
    def add_task(self, text: str, *, project: str | None, area: str, source: str) -> Item:
        """A task the owner confirmed outside a filing card (ATLAS's weekly self-review: chatter
        pattern C). The owner's ✓ is the confirmation (rule 2); exported and committed like any."""
        item = self.store.create_item(kind="task", text=text, area=area, project=project,
                                      source_entry=source, status="open")
        self._finish("confirm: 1 item (ATLAS's week)")
        return item

    # -- proposals -----------------------------------------------------------
    @_serialized
    def confirm(self, ids: list[str]) -> list[Item]:
        pending = self.store.list_proposals(status="proposed")
        by_id = {p.id: p for p in pending}
        proposals: list[Proposal] = []
        for i in dict.fromkeys(ids):
            if i not in by_id:
                raise ValueError(f"unknown or already handled proposal: {i}")
            proposals.append(by_id[i])
        lead = load_settings(self.paths)["default_lead_days"]
        original = self._map()

        # Pull in pending node proposals of the same entry that a selected item (or a selected
        # person's parent) depends on. A course node satisfies an item's "project".
        chosen = {p.id for p in proposals}
        wanted = {(t, p.entry_id, p.payload.get(t)) for p in proposals if p.ptype == "item"
                  for t in ("area", "project", "person") if p.payload.get(t)}
        wanted |= {("project", p.entry_id, p.payload.get("parent")) for p in proposals
                   if p.ptype == "node" and p.payload.get("type") == "person"
                   and p.payload.get("parent")}

        def satisfies(n: Proposal) -> str | None:
            t = n.payload.get("type")
            return "project" if t == "course" else t

        auto = [n for n in pending
                if n.ptype == "node" and n.id not in chosen
                and (satisfies(n), n.entry_id,
                     slugify(str(n.payload.get("name", "")))) in wanted]
        auto_ids = {n.id for n in auto}
        requested = len(proposals)
        proposals = proposals + auto

        # Dry run: nothing is touched until every proposal is known to apply.
        m = original
        node_names: list[str] = []
        nodes = sorted((p for p in proposals if p.ptype == "node"),
                       key=lambda p: _NODE_ORDER.get(p.payload.get("type"), len(_NODE_ORDER)))
        for p in nodes:
            ntype, name = p.payload.get("type"), p.payload.get("name")
            if ntype not in _NODE_TYPES or not isinstance(name, str):
                raise ValueError(f"invalid node proposal: {p.id}")
            existing = {"area": m.areas, "project": m.projects, "course": m.courses,
                        "person": m.people}[ntype]
            if any(x["slug"] == slugify(name) for x in existing):
                continue  # already in the map: a no-op, still marked confirmed
            m = add_node(m, ntype, name, p.payload.get("parent"))
            if p.id in auto_ids:
                node_names.append(slugify(name))
        new_items: list[tuple[Proposal, dict]] = []
        updates: list[tuple[Proposal, str, str]] = []
        for p in proposals:
            if p.ptype == "item":
                fields = {k: p.payload.get(k) for k in _ITEM_KEYS}
                if not (fields["kind"] and fields["text"] and fields["area"]):
                    raise ValueError(f"item proposal {p.id} lacks kind, text or area")
                _validate_fields(fields, m)
                if fields["due"] and not fields["remind_at"]:
                    fields["remind_at"] = _default_remind(fields["due"], lead)
                new_items.append((p, fields | {"source_entry": p.entry_id}))
            elif p.ptype == "update":
                item_id = p.payload.get("item_id")
                if not item_id or "unknown item id" in p.warnings:
                    raise ValueError(f"update proposal {p.id} has no valid item id")
                if self.store.get_item(item_id) is None:
                    raise ValueError(f"unknown item: {item_id}")
                status = p.payload.get("status")
                if status not in _UPDATE_STATUSES:
                    raise ValueError(f"invalid status for an update: {status!r}")
                updates.append((p, item_id, status))
            elif p.ptype != "node":
                raise ValueError(f"unknown proposal type: {p.ptype}")

        # Apply. Each proposal is marked confirmed right after its own step, so a failure
        # part-way never leaves an applied change with a still-proposed proposal.
        if m is not original:
            save_map(self.paths.brain, m)
            ensure_folders(self.paths.brain, m)
            for a in m.areas:  # ensure_folders does not create custom areas
                (self.paths.brain / a["slug"]).mkdir(parents=True, exist_ok=True)
        self.store.set_proposal_status([p.id for p in proposals if p.ptype == "node"], "confirmed")
        created = []
        for p, fields in new_items:
            created.append(self.store.create_item(**fields, status="open"))
            self.store.set_proposal_status([p.id], "confirmed")
        for p, item_id, status in updates:
            self.store.update_item(item_id, {"status": status})
            self.store.set_proposal_status([p.id], "confirmed")
        # If export or commit fails the exception propagates: SQLite is already consistent
        # and the next commit sweeps up the files.
        message = f"confirm: {requested} items"
        if node_names:
            message += " (+ node " + ", ".join(node_names) + ")"
        self._finish(message)
        return created

    @_serialized
    def dismiss(self, ids: list[str]) -> None:
        pending = {p.id for p in self.store.list_proposals(status="proposed")}
        ids = list(dict.fromkeys(ids))
        for i in ids:
            if i not in pending:
                raise ValueError(f"unknown or already handled proposal: {i}")
        self.store.set_proposal_status(ids, "dismissed")
        self.store.export_proposals(self.paths.brain)  # committed with the next hub commit

    @_serialized
    def edit_proposal(self, id: str, patch: dict) -> Proposal:
        p = next((p for p in self.store.list_proposals(status="proposed") if p.id == id), None)
        if p is None:
            raise ValueError(f"unknown or already handled proposal: {id}")
        bad = set(patch) - set(_PROPOSAL_KEYS.get(p.ptype, ()))
        if bad:
            raise ValueError(f"cannot edit {sorted(bad)} on a {p.ptype} proposal")
        if p.ptype == "node" and "type" in patch and patch["type"] not in _NODE_TYPES:
            raise ValueError(f"invalid node type: {patch['type']!r}")
        if p.ptype == "node" and "name" in patch and not (
                isinstance(patch["name"], str) and patch["name"].strip()):
            raise ValueError("name must be a non-empty string")
        if p.ptype == "update" and patch.get("status", "done") not in _UPDATE_STATUSES:
            raise ValueError(f"invalid status for an update: {patch['status']!r}")
        _validate_fields(patch if p.ptype == "item" else {k: v for k, v in patch.items() if k == "status"},
                         self._map())
        out = self.store.update_proposal_payload(id, patch)
        self.store.export_proposals(self.paths.brain)
        return out

    # -- items ---------------------------------------------------------------
    @_serialized
    def edit_item(self, id: str, patch: dict) -> Item:
        item = self.store.get_item(id)
        if item is None:
            raise ValueError(f"unknown item: {id}")
        bad = set(patch) - set(_ITEM_PATCH_KEYS)
        if bad:
            raise ValueError(f"cannot edit item fields: {sorted(bad)}")
        _validate_fields(patch, self._map())
        patch = dict(patch)
        if patch.get("status") == "snoozed":
            if not (patch.get("snooze_until") or item.snooze_until):
                raise ValueError("snoozed needs snooze_until")
        elif "status" in patch and "snooze_until" not in patch:
            patch["snooze_until"] = None
        out = self.store.update_item(id, patch)
        self._finish(f"edit: {id}")
        return out
