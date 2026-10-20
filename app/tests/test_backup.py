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

    clk = Clock(0)
    repo = Racing(); b = BackupPusher(repo, settings("r", 300), noop, clk); repo.pusher = b
    b.notify_commit(); clk.t = 301; await b.tick()
    assert b.status()["pending"] and len(repo.pushes) == 1
    clk.t = 400; await b.tick(); assert len(repo.pushes) == 1  # debounce counts from the new commit
    clk.t = 602; await b.tick(); assert len(repo.pushes) == 2


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


async def test_last_error_is_redacted_and_never_broadcast_raw():
    events = []

    async def bc(kind, payload):
        events.append(payload)

    class Leaky:
        def push(self, remote_url):
            return False, "fatal: unable to access https://tok@github.com/x"

    b = BackupPusher(Leaky(), settings(), bc, Clock(0))
    b.notify_commit(); await b.tick()
    assert b.status()["last_error"] == "fatal: unable to access https://***@github.com/x"
    assert "tok@" not in repr(events)


async def test_redacts_exception_text_too():
    class Boom:
        def push(self, remote_url):
            raise OSError("cannot reach https://tok@github.com/x")

    b = BackupPusher(Boom(), settings(), noop, Clock(0))
    b.notify_commit(); await b.tick()
    assert "tok@" not in b.status()["last_error"]


async def test_oserror_is_failure_with_backoff():
    class Boom:
        pushes = 0
        def push(self, remote_url):
            Boom.pushes += 1
            raise OSError("disk")

    clk = Clock(0); b = BackupPusher(Boom(), settings(), noop, clk)
    b.notify_commit(); await b.tick()
    assert b.status()["pending"] and b.status()["last_error"] == "disk"
    clk.t = 30; await b.tick(); assert Boom.pushes == 1
    clk.t = 61; await b.tick(); assert Boom.pushes == 2


async def test_none_debounce_treated_as_zero():
    repo = FakeRepo([True]); b = BackupPusher(repo, settings("r", None), noop, Clock(0))
    b.notify_commit(); await b.tick(); assert repo.pushes == ["r"]


async def test_force_ignores_debounce_and_backoff_but_needs_remote():
    clk = Clock(0); repo = FakeRepo([False, True, True]); b = BackupPusher(repo, settings("r", 9999), noop, clk)
    b.notify_commit(); await b.tick(); assert repo.pushes == []  # debounced
    await b.tick(force=True); assert repo.pushes == ["r"] and b.status()["last_error"]  # fails, backs off
    await b.tick(); assert len(repo.pushes) == 1  # normal tick respects the backoff
    await b.tick(force=True); assert len(repo.pushes) == 2 and not b.status()["pending"]
    await b.tick(force=True); assert len(repo.pushes) == 3  # nothing pending: explicit push still goes
    none = FakeRepo([True]); n = BackupPusher(none, settings(None, 0), noop, clk)
    n.notify_commit(); await n.tick(force=True); assert none.pushes == []


async def test_forced_and_background_pushes_never_overlap():
    import threading
    import time as _time

    class Slow:
        def __init__(self):
            self.active = 0; self.peak = 0; self.pushes = 0; self.guard = threading.Lock()

        def push(self, remote_url):
            with self.guard:
                self.active += 1; self.peak = max(self.peak, self.active)
            _time.sleep(0.05)
            with self.guard:
                self.active -= 1; self.pushes += 1
            return True, ""

    repo = Slow(); b = BackupPusher(repo, settings("r", 0), noop, Clock(0))
    b.notify_commit()
    await asyncio.gather(b.tick(), b.tick(force=True), b.tick(force=True))
    assert repo.peak == 1 and repo.pushes == 3  # all ran, one at a time
