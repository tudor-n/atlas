import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

RETRY_BACKOFF_S = 60
TICK_INTERVAL_S = 15


class BackupPusher:
    """Debounced background push of the brain repo. Failures are quiet: status + broadcast only."""

    def __init__(self, repo, settings_getter: Callable[[], dict], broadcast: Callable[..., Awaitable],
                 clock: Callable[[], float] = time.monotonic):
        self._repo = repo
        self._settings = settings_getter
        self._broadcast = broadcast
        self._clock = clock
        self._pending = False
        self._first_pending: float | None = None
        self._seq = 0  # bumped per commit, so a commit during a push is not lost
        self._retry_at: float | None = None
        self._last_ok: str | None = None
        self._last_error: str | None = None

    def notify_commit(self) -> None:
        self._seq += 1
        if not self._pending:
            self._pending = True
            self._first_pending = self._clock()

    def status(self) -> dict:
        return {
            "remote_set": bool(self._settings().get("backup_remote")),
            "pending": self._pending,
            "last_ok": self._last_ok,
            "last_error": self._last_error,
        }

    async def tick(self) -> None:
        if not self._pending:
            return
        cfg = self._settings()
        remote = cfg.get("backup_remote")
        if not remote:
            return
        now = self._clock()
        if now - self._first_pending < cfg.get("backup_debounce_s", 0):
            return
        if self._retry_at is not None and now < self._retry_at:
            return
        seq = self._seq
        try:
            ok, err = await asyncio.to_thread(self._repo.push, remote)
        except RuntimeError as e:
            ok, err = False, str(e)
        if ok:
            self._last_ok = datetime.now(timezone.utc).isoformat()
            self._last_error = None
            self._retry_at = None
            if seq == self._seq:
                self._pending = False
                self._first_pending = None
            else:  # committed while pushing: push again, debounced from now
                self._first_pending = self._clock()
        else:
            self._last_error = err or "push failed"
            self._retry_at = now + RETRY_BACKOFF_S
        await self._broadcast("backup.status", self.status())

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("backup tick failed")
            await asyncio.sleep(TICK_INTERVAL_S)
