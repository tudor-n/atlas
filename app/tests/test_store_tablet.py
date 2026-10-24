"""Phase 3 store: paired devices, measurement requests, hidden tablet items, their exports."""
import json
import sqlite3
from datetime import datetime

import pytest

from atlas import store as store_mod
from atlas.models import Entry
from atlas.store import Store


def test_schema_3_adds_the_tablet_tables_to_a_version_2_db(paths):
    s = Store(paths.db)
    s.init_schema()
    s._db.executescript("DROP TABLE devices; DROP TABLE requests; DROP TABLE tablet_hidden;")
    s._db.execute("PRAGMA user_version=2")
    s._db.commit()
    s.close()
    s = Store(paths.db)
    s.init_schema()
    assert s.schema_version() == store_mod.SCHEMA_VERSION == 3
    tables = {r[0] for r in s._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"devices", "requests", "tablet_hidden"} <= tables
    s.close()


def test_devices(store):
    d = store.add_device("Workbench tablet", "h1")
    assert d["name"] == "Workbench tablet" and d["id"].startswith("d-") and d["last_seen"] is None
    assert store.device_by_hash("h1")["id"] == d["id"] and store.device_by_hash("nope") is None
    store.touch_device(d["id"], "2026-10-23T12:00:00")
    assert store.list_devices()[0]["last_seen"] == "2026-10-23T12:00:00"
    assert "token_hash" not in store.list_devices()[0]  # never leaves the store
    assert store.delete_device(d["id"]) is True and store.delete_device(d["id"]) is False
    assert store.device_by_hash("h1") is None
    with pytest.raises(sqlite3.IntegrityError):
        store.add_device("a", "h2")
        store.add_device("b", "h2")


def test_requests(store):
    r = store.add_request("Arm calibration", ["ArUco ID 3 → arm base", "ID 3 → ID 7"], "robotarm", "Robot arm")
    assert r["id"].startswith("r-") and r["status"] == "open" and r["items"][1] == "ID 3 → ID 7"
    assert store.get_request(r["id"]) == r and store.get_request("r-x") is None
    assert [x["id"] for x in store.list_requests()] == [r["id"]]
    store.set_request_answered(r["id"], "e-20261023-120000-abcd")
    got = store.get_request(r["id"])
    assert got["status"] == "answered" and got["answer_entry"] == "e-20261023-120000-abcd"
    assert store.request_for_entry("e-20261023-120000-abcd")["id"] == r["id"]
    assert store.request_for_entry("e-other") is None


def test_hidden(store):
    assert store.hidden_refs("note") == set()
    store.hide("note", "e-1")
    store.hide("note", "e-1")  # idempotent
    store.hide("request", "r-1")
    assert store.hidden_refs("note") == {"e-1"} and store.hidden_refs("request") == {"r-1"}
    assert store.unhide("note", "e-1") is True and store.unhide("note", "e-1") is False
    with pytest.raises(ValueError):
        store.hide("item", "x")


def test_entries_by_source(store):
    store.add_entry(Entry("e-20261023-100000-aaaa", datetime(2026, 10, 23, 10), "pc", "pc one"))
    store.add_entry(Entry("e-20261023-110000-bbbb", datetime(2026, 10, 23, 11), "tablet", "412 mm"))
    store.add_entry(Entry("e-20261023-120000-cccc", datetime(2026, 10, 23, 12), "tablet", "later"))
    assert [e.id for e in store.list_entries(source="tablet")] == ["e-20261023-120000-cccc", "e-20261023-110000-bbbb"]
    assert [e.id for e in store.list_entries(source="tablet", limit=1)] == ["e-20261023-120000-cccc"]


def test_tablet_export_and_restore(store, paths):
    r = store.add_request("Arm calibration", ["a", "b"], "robotarm", "Robot arm")
    store.set_request_answered(r["id"], "e-20261023-120000-abcd")
    store.hide("note", "e-20261023-110000-bbbb")
    store.add_device("Tablet", "secret-hash")
    store.export_tablet(paths.brain)
    items = paths.brain / "_items"
    req = json.loads((items / "requests.json").read_text(encoding="utf-8"))
    assert req == [store.get_request(r["id"])]
    hidden = json.loads((items / "tablet.json").read_text(encoding="utf-8"))
    assert [(h["kind"], h["ref"]) for h in hidden["hidden"]] == [("note", "e-20261023-110000-bbbb")]
    assert not (items / "devices.json").exists()  # pairing hashes stay in hub.db (never in the backup)
    assert "secret-hash" not in "".join(p.read_text(encoding="utf-8") for p in items.iterdir())
    mtime = (items / "requests.json").stat().st_mtime_ns
    store.export_tablet(paths.brain)
    assert (items / "requests.json").stat().st_mtime_ns == mtime  # unchanged: not rewritten

    fresh = Store(paths.data / "rebuilt.db")
    fresh.init_schema()
    assert fresh.restore_tablet_if_empty(paths.brain) == 2
    assert fresh.get_request(r["id"]) == store.get_request(r["id"])
    assert fresh.hidden_refs("note") == {"e-20261023-110000-bbbb"}
    assert fresh.restore_tablet_if_empty(paths.brain) == 0  # not empty any more
    fresh.close()


def test_damaged_tablet_export_names_the_file(store, paths):
    items = paths.brain / "_items"
    items.mkdir(parents=True, exist_ok=True)
    (items / "requests.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(ValueError, match="requests.json"):
        store.restore_tablet_if_empty(paths.brain)
    (items / "requests.json").write_text('[{"id": 1}]', encoding="utf-8")
    with pytest.raises(ValueError, match="requests.json"):
        store.restore_tablet_if_empty(paths.brain)
    assert store.list_requests() == []
