"""The voice files (phase 4 design §6): Kokoro's model and voices, downloaded once into shared
`data/voice/` so both OSes use one copy. They're pinned: a file is used only after its sha256
matches, and it's written to a `.part` file first and renamed only once the check passes."""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

BASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"


@dataclass(frozen=True)
class VoiceFile:
    name: str
    size: int
    sha256: str


VOICES = VoiceFile("voices-v1.0.bin", 28214398, "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d")
MODELS = {
    "int8": VoiceFile("kokoro-v1.0.int8.onnx", 92361271, "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb"),
    "fp32": VoiceFile("kokoro-v1.0.onnx", 325532387, "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5"),
}
CHUNK = 1 << 20


def voice_dir(data: Path) -> Path:
    return Path(data) / "voice"


def needed(model: str) -> list[VoiceFile]:
    return [MODELS[model], VOICES]


def _present(folder: Path, f: VoiceFile) -> bool:
    """Present means the right size: the sha256 was checked when it was downloaded, and hashing
    120–350 MB on every look would be slow. A damaged file shows up as the worker's error."""
    try:
        return (folder / f.name).stat().st_size == f.size
    except OSError:
        return False


def files(data: Path, model: str) -> dict | None:
    """{"model", "voices"} paths when both are present, else None."""
    folder = voice_dir(data)
    if model not in MODELS or not all(_present(folder, f) for f in needed(model)):
        return None
    return {"model": str(folder / MODELS[model].name), "voices": str(folder / VOICES.name)}


def missing_bytes(data: Path, model: str) -> int:
    folder = voice_dir(data)
    return sum(f.size for f in needed(model) if not _present(folder, f))


def fetch(url: str, target: Path, f: VoiceFile, progress: Callable[[int], None],
          cancel: threading.Event | None = None) -> None:
    """Download `url` to `target` through `<target>.part`, checking size and sha256."""
    part = target.with_name(target.name + ".part")
    h = hashlib.sha256()
    got = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ATLAS"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(part, "wb") as out:
            while chunk := resp.read(CHUNK):
                if cancel is not None and cancel.is_set():
                    raise RuntimeError("cancelled")
                got += len(chunk)
                if got > f.size:
                    raise ValueError(f"{f.name} is bigger than expected")
                h.update(chunk)
                out.write(chunk)
                progress(len(chunk))
            out.flush()
            os.fsync(out.fileno())
        if got != f.size:
            raise ValueError(f"{f.name} is incomplete ({got} of {f.size} bytes)")
        if h.hexdigest() != f.sha256:
            raise ValueError(f"{f.name} failed its checksum: not the pinned file")
        os.replace(part, target)
    finally:
        try:
            part.unlink()
        except OSError:
            pass


class Downloader:
    """One download at a time, in a thread; its state is what Settings shows."""

    def __init__(self, data: Path, *, base_url: str = BASE_URL, on_change: Callable[[dict], None] | None = None):
        self._data, self._base, self._on_change = Path(data), base_url, on_change
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.state = {"running": False, "done": 0, "total": 0, "error": None, "model": None}

    def start(self, model: str) -> bool:
        """False when one is already running (or there's nothing to fetch)."""
        if model not in MODELS:
            raise ValueError(f"unknown voice model {model!r}")
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            total = missing_bytes(self._data, model)
            self.state = {"running": total > 0, "done": 0, "total": total, "error": None, "model": model}
            if not total:
                return False
            self._thread = threading.Thread(target=self._run, args=(model,), name="voice-download", daemon=True)
            self._thread.start()
        self._changed()
        return True

    def _run(self, model: str) -> None:
        folder = voice_dir(self._data)
        last = [0]

        def progress(n: int) -> None:
            self.state["done"] += n
            if self.state["done"] - last[0] >= 4 * CHUNK:  # a progress line every few MB, not every chunk
                last[0] = self.state["done"]
                self._changed()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for f in needed(model):
                if not _present(folder, f):
                    fetch(self._base + f.name, folder / f.name, f, progress)
        except Exception as e:
            log.warning("voice download failed: %s", e)
            self.state["error"] = str(e) or type(e).__name__
        finally:
            self.state["running"] = False
            self._changed()

    def _changed(self) -> None:
        if self._on_change:
            try:
                self._on_change(dict(self.state))
            except Exception:
                log.exception("voice download: progress report failed")

    def wait(self, timeout: float | None = None) -> None:
        t = self._thread
        if t:
            t.join(timeout)
