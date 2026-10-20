import asyncio

from brainhub.backup import BackupPusher
from tests.fakes import Clock, FakeRepo


async def noop(*_a, **_k):
    pass


def settings(remote="r", debounce=0):
    return lambda: {"backup_remote": remote, "backup_debounce_s": debounce}


async def test_debounce_then_push():
    clk = Clock(0); repo = FakeRepo([True]); b = BackupPusher(repo, settings("r", 300), noop, clk)
    b.notify_commit(); await b.tick(); assert repo.pushes == []
    clk.t = 301; await b.tick(); assert repo.pushes == ["r"] and not b.status()["pending"]


async def test_failure_stays_pending_and_backs_off():
    clk = Clock(0); repo = FakeRepo([False, True]); b = BackupPusher(repo, settings("r", 0), noop, clk)
    b.notify_commit(); await b.tick(); assert b.status()["pending"] and b.status()["last_error"]
    clk.t = 30; await b.tick(); assert len(repo.pushes) == 1
    clk.t = 61; await b.tick(); assert len(repo.pushes) == 2 and not b.status()["pending"]


async def test_no_remote_no_push():
    repo = FakeRepo([True]); b = BackupPusher(repo, settings(None, 0), noop, Clock(0))
    b.notify_commit(); await b.tick(); assert repo.pushes == []
    assert b.status()["remote_set"] is False and b.status()["pending"]


async def test_success_sets_last_ok_and_clears_error():
    clk = Clock(0); repo = FakeRepo([False, True]); b = BackupPusher(repo, settings(), noop, clk)
    b.notify_commit(); await b.tick()
    assert b.status()["last_ok"] is None and b.status()["last_error"] == "err"
    clk.t = 100; await b.tick()
    s = b.status()
    assert s["last_error"] is None and s["last_ok"] and "T" in s["last_ok"]


async def test_nothing_pending_no_push():
    repo = FakeRepo([True]); b = BackupPusher(repo, settings(), noop, Clock(0))
    await b.tick(); assert repo.pushes == []


async def test_broadcast_after_every_attempt():
    events = []

    async def bc(kind, payload):
        events.append((kind, payload))

    clk = Clock(0); repo = FakeRepo([False, True]); b = BackupPusher(repo, settings(), bc, clk)
    b.notify_commit(); await b.tick()
    clk.t = 61; await b.tick()
    assert [e[0] for e in events] == ["backup.status", "backup.status"]
    assert events[0][1]["last_error"] == "err" and events[1][1]["pending"] is False


async def test_push_runtime_error_is_a_failure():
    class Broken:
        def push(self, remote_url):
            raise RuntimeError("repo not initialised")

    b = BackupPusher(Broken(), settings(), noop, Clock(0))
    b.notify_commit(); await b.tick()
    assert b.status()["pending"] and "not initialised" in b.status()["last_error"]


async def test_commit_during_push_stays_pending():
    class Racing:
        def __init__(self): self.pusher = None; self.pushes = []
        def push(self, remote_url):
            self.pushes.append(remote_url)
            self.pusher.notify_commit()
            return True, ""

    repo = Racing(); b = BackupPusher(repo, settings(), noop, Clock(0)); repo.pusher = b
    b.notify_commit(); await b.tick()
    assert b.status()["pending"]


async def test_run_forever_survives_errors_and_cancels(monkeypatch):
    b = BackupPusher(FakeRepo([]), settings(), noop, Clock(0))
    calls = 0

    async def boom():
        nonlocal calls
        calls += 1
        raise ValueError("x")

    b.tick = boom
    real_sleep = asyncio.sleep
    monkeypatch.setattr("brainhub.backup.asyncio.sleep", lambda s: real_sleep(0))
    task = asyncio.create_task(b.run_forever())
    for _ in range(20):
        await real_sleep(0)
    assert calls >= 2 and not task.done()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert task.done()
