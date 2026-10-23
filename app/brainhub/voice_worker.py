"""The voice worker (phase 4 design §3): a child process that turns text into speech and plays it.

Run as `python -m brainhub voice-worker --state <dir>`. It speaks JSON lines:
  in:  {"op": "say", "id": "…", "parts": ["info", "quip"], "blend": {"bm_george": 0.7, …},
        "speed": 0.95, "volume": 80, "model": "<model file>", "voices": "<voices file>"}
       {"op": "stop"}   stops the line playing now (a "say" waiting behind it still plays)
  out: {"id": "…", "ok": true} or {"id": "…", "ok": false, "error": "…"}, one per "say"

It lives in its own process so its model's memory goes when it exits, and so a crash or a missing
audio device never takes the hub down. Its stdout carries only the protocol: anything a library
prints goes to stderr. With BRAINHUB_VOICE_FAKE=<file> it synthesises nothing and plays nothing:
it appends each request to that file (the tests' boundary, like FakeRunner for claude)."""
from __future__ import annotations

import argparse
import json
import math
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

SAMPLE_RATE = 24000
GAP_S = 0.4  # between the info and the quip: the owner's pick (sample C)
ESPEAK_PATH_MAX = 159  # espeak-ng keeps its data path in a 160-byte buffer and silently cuts it
LINUX_PLAYERS = (("pw-play",), ("paplay",), ("aplay", "-q"))


def short_espeak_data(data_path: str, state: Path) -> str:
    """espeak-ng cuts a data path of 160 characters or more and then can't phonemize anything
    ("Error processing file '/home/runner/…/phontab'"). Such a path is copied once to a short
    folder: the state folder's voice/ when that's short enough, else the temp folder."""
    if len(data_path) <= ESPEAK_PATH_MAX:
        return data_path
    short = state / "voice" / "espeak-ng-data"
    if len(str(short)) <= ESPEAK_PATH_MAX:
        bases = [state / "voice"]
    else:  # a private folder of this run's own: never one another user could have planted
        bases = [Path(tempfile.mkdtemp(prefix="bh-espeak-"))]
    for base in bases:
        target = base / "espeak-ng-data"
        if len(str(target)) > ESPEAK_PATH_MAX:
            continue
        stamp = target / ".source"
        source = f"{data_path}\n{sum(f.stat().st_size for f in Path(data_path).rglob('*') if f.is_file())}"
        fresh = (target / "phontab").is_file() and stamp.is_file() and stamp.read_text(encoding="utf-8") == source
        if not fresh:  # missing, or an espeakng-loader upgrade since the copy
            tmp = base / "espeak-ng-data.tmp"
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.copytree(data_path, tmp)
            (tmp / ".source").write_text(source, encoding="utf-8")
            shutil.rmtree(target, ignore_errors=True)
            os.replace(tmp, target)
        return str(target)
    raise RuntimeError("the espeak-ng data path is too long, and no short folder is available")


def find_player() -> tuple[str, ...] | None:
    """The command that plays a WAV file here, or None. Windows uses winsound instead."""
    if sys.platform == "win32":
        return ("winsound",)
    for cmd in LINUX_PLAYERS:
        if shutil.which(cmd[0]):
            return cmd
    return None


def to_wav(path: Path, samples, volume: int) -> float:
    """Write mono 16-bit PCM; returns the length in seconds. Volume 0–100 scales the samples."""
    import numpy as np
    pcm = np.clip(np.asarray(samples, dtype=np.float32) * (volume / 100.0), -1.0, 1.0)
    data = (pcm * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(data)
    return len(pcm) / SAMPLE_RATE


class Engine:
    """kokoro-onnx, loaded once per model file, kept while the worker lives."""

    def __init__(self, state: Path):
        self._state = state
        self._kokoro = None
        self._key: tuple[str, str] | None = None

    def _load(self, model: str, voices: str):
        if self._key != (model, voices):
            import espeakng_loader
            from kokoro_onnx import Kokoro
            from kokoro_onnx.config import EspeakConfig
            cfg = EspeakConfig(lib_path=espeakng_loader.get_library_path(),
                               data_path=short_espeak_data(espeakng_loader.get_data_path(), self._state))
            self._kokoro, self._key = Kokoro(model, voices, espeak_config=cfg), (model, voices)
        return self._kokoro

    def synth(self, req: dict):
        import numpy as np
        k = self._load(req["model"], req["voices"])
        known = set(k.get_voices())
        blend = req["blend"]
        unknown = [v for v in blend if v not in known]
        if unknown:
            raise ValueError(f"unknown voice {unknown[0]} (not in the voices file)")
        total = sum(blend.values())
        style = sum(k.get_voice_style(v) * (w / total) for v, w in blend.items())
        gap = np.zeros(int(SAMPLE_RATE * GAP_S), dtype=np.float32)
        clips: list = []
        for text in req["parts"]:
            if not text.strip():
                continue
            audio, _sr = k.create(text, voice=style, speed=float(req["speed"]), lang="en-gb")
            clips += [np.asarray(audio, dtype=np.float32), gap]
        return np.concatenate(clips[:-1]) if clips else np.zeros(0, dtype=np.float32)


class _Stoppable:
    """A stop counter. Each line is stamped with the count when it arrives; a stop bumps it, so a
    line stopped while it was still being synthesised (or waiting) never starts playing."""

    def __init__(self):
        self._cv = threading.Condition()
        self.stops = 0

    def cancelled(self, gen: int) -> bool:
        return self.stops != gen

    def _bump(self) -> None:
        with self._cv:
            self.stops += 1
            self._cv.notify_all()

    def _wait(self, gen: int, seconds: float) -> bool:
        """Wait out a clip; True when a stop came first."""
        with self._cv:
            return self._cv.wait_for(lambda: self.stops != gen, seconds)


class Player(_Stoppable):
    """Plays one WAV at a time; stop() cuts it short from another thread."""

    def __init__(self, cmd: tuple[str, ...]):
        super().__init__()
        self._cmd = cmd
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def play(self, path: Path, seconds: float, gen: int) -> None:
        if self._cmd == ("winsound",):
            import winsound
            with self._cv:  # stop() bumps under this lock: it can't slip between the check and the start
                if self.cancelled(gen):
                    return
                winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            # SND_ASYNC returns at once: wait out the clip, unless stop() purges it first
            self._wait(gen, seconds + 0.2)
            return
        # stderr to a file, not a pipe: a player's leftover child can't hold the wait up. In the
        # worker's own process group (no new session): when the hub kills the worker's group,
        # the player goes with it, mid-sentence included.
        with tempfile.TemporaryFile() as err:
            with self._lock:  # stop() kills under this lock, after its bump: no start after a stop
                if self.cancelled(gen):
                    return
                self._proc = subprocess.Popen([*self._cmd, str(path)], stdin=subprocess.DEVNULL,
                                              stdout=subprocess.DEVNULL, stderr=err)
            try:
                self._proc.wait(timeout=seconds + 30)
            except subprocess.TimeoutExpired:
                self.stop()
                raise RuntimeError("the audio player hung") from None
            finally:
                with self._lock:
                    proc, self._proc = self._proc, None
            if proc.returncode and not self.cancelled(gen):
                err.seek(0)
                msg = err.read().decode("utf-8", "replace").strip().splitlines()
                raise RuntimeError(f"{self._cmd[0]} failed: {msg[-1] if msg else f'exit {proc.returncode}'}")

    def stop(self) -> None:
        if self._cmd == ("winsound",):
            import winsound
            with self._cv:
                self.stops += 1
                self._cv.notify_all()
                winsound.PlaySound(None, 0)
            return
        self._bump()
        with self._lock:
            proc = self._proc
            if proc and proc.poll() is None:
                try:
                    proc.kill()  # pw-play, paplay and aplay are single processes
                except OSError:
                    pass


class FakeEngine:
    """BRAINHUB_VOICE_FAKE: no model, a second of silence per part."""

    def synth(self, req: dict):
        import numpy as np
        if any("FAIL-SYNTH" in p for p in req["parts"]):
            raise ValueError("fake synthesis failure")
        return np.zeros(SAMPLE_RATE * len(req["parts"]), dtype=np.float32)


class FakePlayer(_Stoppable):
    """BRAINHUB_VOICE_FAKE: records what would have played; BRAINHUB_VOICE_FAKE_DELAY seconds each."""

    def __init__(self, log: Path):
        super().__init__()
        self._log = log
        self._req: dict = {}

    def play(self, path: Path, seconds: float, gen: int) -> None:
        if self.cancelled(gen):
            return
        with wave.open(str(path), "rb") as w:
            frames = w.getnframes()
        stopped = self._wait(gen, float(os.environ.get("BRAINHUB_VOICE_FAKE_DELAY", "0")))
        with open(self._log, "a", encoding="utf-8") as f:
            f.write(json.dumps({**self._req, "frames": frames, "stopped": stopped, "pid": os.getpid()}) + "\n")

    def stop(self) -> None:
        self._bump()


def serve(state: Path, inp, out, *, engine=None, player=None) -> int:
    """The protocol loop. A reader thread takes "stop" at once; "say" requests play in order."""
    state.mkdir(parents=True, exist_ok=True)
    fake = os.environ.get("BRAINHUB_VOICE_FAKE")
    if engine is None:
        engine = FakeEngine() if fake else Engine(state)
    if player is None:
        if fake:
            player = FakePlayer(Path(fake))
        elif cmd := find_player():
            player = Player(cmd)
    says: queue.Queue = queue.Queue()
    out_lock = threading.Lock()

    def reply(obj: dict) -> None:
        with out_lock:
            out.write(json.dumps(obj) + "\n")
            out.flush()

    def reader() -> None:
        for line in inp:
            try:
                req = json.loads(line)
            except ValueError:
                continue
            if not isinstance(req, dict):
                continue
            if req.get("op") == "stop":
                if player:
                    player.stop()
            elif req.get("op") == "say":
                req["_gen"] = player.stops if player else 0  # stamped on arrival (see _Stoppable)
                says.put(req)
        # stdin closed: the hub is gone (or done with us). Stop talking at once, even mid-line.
        if player:
            player.stop()
        says.put(None)

    threading.Thread(target=reader, daemon=True).start()
    wav = state / "voice" / f"line-{os.getpid()}.wav"
    wav.parent.mkdir(parents=True, exist_ok=True)
    for old in wav.parent.glob("line-*.wav"):  # left by a worker that was killed mid-line
        try:
            old.unlink()
        except OSError:
            pass
    try:
        while (req := says.get()) is not None:
            rid = req.get("id")
            try:
                if player is None:
                    raise RuntimeError("no audio player (pw-play, paplay or aplay) was found")
                gen = req.pop("_gen")
                if isinstance(player, FakePlayer):
                    player._req = req
                if not player.cancelled(gen):
                    samples = engine.synth(req)
                    seconds = to_wav(wav, samples, int(req.get("volume", 80)))
                    if seconds > 0 and not math.isnan(seconds):
                        player.play(wav, seconds, gen)
                reply({"id": rid, "ok": True})
            except Exception as e:  # one bad line never ends the worker
                reply({"id": rid, "ok": False, "error": str(e) or type(e).__name__})
    finally:
        try:
            wav.unlink()
        except OSError:
            pass
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="brainhub voice-worker")
    ap.add_argument("--state", required=True, help="this OS's BrainHub state folder")
    args = ap.parse_args(argv)
    # The protocol owns the real stdout; whatever a library prints goes to stderr instead.
    proto = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", newline="\n")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdin.reconfigure(encoding="utf-8")
    return serve(Path(args.state), sys.stdin, proto)


if __name__ == "__main__":
    sys.exit(main())
