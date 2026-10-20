"""Owner review actions: confirm / dismiss / edit proposals and items. Every change is committed."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable

from .brainmap import BrainMap, add_node, default_map, ensure_folders, load_map, save_map
from .config import Paths, load_settings
from .filing import _valid_date
from .gitrepo import BrainRepo
from .models import Item, Proposal
from .store import Store

KINDS = ("task", "deadline", "reminder", "waiting", "next_time")
STATUSES = ("open", "to_read", "done", "dismissed", "snoozed")
_NODE_TYPES = ("area", "project", "course", "person")
_ITEM_KEYS = ("kind", "text", "area", "project", "person", "due", "remind_at")
_PROPOSAL_KEYS = {
    "item": _ITEM_KEYS,
    "update": ("status", "note"),
    "node": ("type", "name", "parent"),
}
_ITEM_PATCH_KEYS = _ITEM_KEYS + ("status", "snooze_until")


def _default_remind(due: str, lead_days: int) -> str:
    if len(due) == 10:
        return (date.fromisoformat(due) - timedelta(days=lead_days)).isoformat()
    return (datetime.fromisoformat(due) - timedelta(days=lead_days)).strftime("%Y-%m-%dT%H:%M")


def _validate_fields(fields: dict, m: BrainMap) -> None:
    """Validate the item-shaped fields present in `fields`. Raises ValueError."""
    slugs = {"area": {a["slug"] for a in m.areas}, "project": {p["slug"] for p in m.projects},
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
        if fields.get(key) is not None and not _valid_date(fields[key]):
            raise ValueError(f"invalid date for {key}: {fields[key]!r}")


class Reviewer:
    def __init__(self, paths: Paths, store: Store, repo: BrainRepo, on_commit: Callable[[], None]):
        self.paths = paths
        self.store = store
        self.repo = repo
        self.on_commit = on_commit

    def _map(self) -> BrainMap:
        return load_map(self.paths.brain) or default_map()

    def _finish(self, message: str) -> None:
        self.store.export_items(self.paths.brain)
        self.repo.commit_all(message)
        self.on_commit()

    # -- proposals -----------------------------------------------------------
    def confirm(self, ids: list[str]) -> list[Item]:
        by_id = {p.id: p for p in self.store.list_proposals(status="proposed")}
        proposals: list[Proposal] = []
        for i in dict.fromkeys(ids):
            if i not in by_id:
                raise ValueError(f"unknown or already handled proposal: {i}")
            proposals.append(by_id[i])
        lead = load_settings(self.paths)["default_lead_days"]

        # Dry run: nothing is touched until every proposal is known to apply.
        original = self._map()
        m = original
        for p in (p for p in proposals if p.ptype == "node"):
            m = add_node(m, p.payload.get("type"), p.payload.get("name"), p.payload.get("parent"))
        new_items: list[dict] = []
        updates: list[tuple[str, str]] = []
        for p in proposals:
            if p.ptype == "item":
                fields = {k: p.payload.get(k) for k in _ITEM_KEYS}
                if not (fields["kind"] and fields["text"] and fields["area"]):
                    raise ValueError(f"item proposal {p.id} lacks kind, text or area")
                _validate_fields(fields, m)
                if fields["due"] and not fields["remind_at"]:
                    fields["remind_at"] = _default_remind(fields["due"], lead)
                new_items.append(fields | {"source_entry": p.entry_id})
            elif p.ptype == "update":
                item_id = p.payload.get("item_id")
                if not item_id or "unknown item id" in p.warnings:
                    raise ValueError(f"update proposal {p.id} has no valid item id")
                if self.store.get_item(item_id) is None:
                    raise ValueError(f"unknown item: {item_id}")
                _validate_fields({"status": p.payload.get("status")}, m)
                updates.append((item_id, p.payload["status"]))
            elif p.ptype != "node":
                raise ValueError(f"unknown proposal type: {p.ptype}")

        if m is not original:
            save_map(self.paths.brain, m)
            ensure_folders(self.paths.brain, m)
            for a in m.areas:  # ensure_folders does not create custom areas
                (self.paths.brain / a["slug"]).mkdir(parents=True, exist_ok=True)
        created = [self.store.create_item(**f, status="open") for f in new_items]
        for item_id, status in updates:
            self.store.update_item(item_id, {"status": status})
        self.store.set_proposal_status([p.id for p in proposals], "confirmed")
        self._finish(f"confirm: {len(proposals)} items")
        return created

    def dismiss(self, ids: list[str]) -> None:
        self.store.set_proposal_status(list(ids), "dismissed")

    def edit_proposal(self, id: str, patch: dict) -> Proposal:
        p = next((p for p in self.store.list_proposals(status="proposed") if p.id == id), None)
        if p is None:
            raise ValueError(f"unknown or already handled proposal: {id}")
        bad = set(patch) - set(_PROPOSAL_KEYS.get(p.ptype, ()))
        if bad:
            raise ValueError(f"cannot edit {sorted(bad)} on a {p.ptype} proposal")
        if p.ptype == "node" and "type" in patch and patch["type"] not in _NODE_TYPES:
            raise ValueError(f"invalid node type: {patch['type']!r}")
        _validate_fields(patch if p.ptype == "item" else {k: v for k, v in patch.items() if k == "status"},
                         self._map())
        return self.store.update_proposal_payload(id, patch)

    # -- items ---------------------------------------------------------------
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
