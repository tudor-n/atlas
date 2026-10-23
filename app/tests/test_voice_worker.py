import json
import os
import subprocess
import sys
import time
import wave
from pathlib import Path

import pytest

from brainhub import voice_worker as vw

APP = Path(__file__).resolve().parents[1]


def _worker(tmp_path, delay="0", extra_env=None):
    env = {**os.environ, "BRAINHUB_VOICE_FAKE": str(tmp_path / "said.jsonl"), "BRAINHUB_VOICE_FAKE_DELAY": delay,
           **(extra_env or {})}
    return subprocess.Popen([sys.executable, "-m", "brainhub", "voice-worker", "--state", str(tmp_path / "state")],
                            cwd=APP, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8")


def _reply(p, timeout=20):
    """One protocol line from the worker, or a failure carrying its stderr: never a hung test."""
    import queue
    import threading
    q = queue.Queue()
    threading.Thread(target=lambda: q.put(p.stdout.readline()), daemon=True).start()
    try:
        line = q.get(timeout=timeout)
    except queue.Empty:
        p.kill()
        pytest.fail(f"the worker didn't answer in {timeout} s; its stderr:\n{p.stderr.read()}")
    if not line:
        pytest.fail(f"the worker exited ({p.wait(5)}); its stderr:\n{p.stderr.read()}")
    return json.loads(line)


def _send(p, obj):
    p.stdin.write(json.dumps(obj) + "\n")
    p.stdin.flush()


def _say(rid, *parts):
    return {"op": "say", "id": rid, "parts": list(parts), "blend": {"bm_george": 0.7, "bm_fable": 0.3},
            "speed": 0.95, "volume": 80, "model": "m.onnx", "voices": "v.bin"}


def _said(tmp_path):
    f = tmp_path / "said.jsonl"
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


def test_say_replies_ok_and_plays_in_order(tmp_path):
    p = _worker(tmp_path)
    try:
        _send(p, _say("a", "Robot arm has finished.", "Splendid."))
        _send(p, _say("b", "Note saved."))
        assert _reply(p) == {"id": "a", "ok": True}
        assert _reply(p) == {"id": "b", "ok": True}
    finally:
        p.stdin.close()
        assert p.wait(10) == 0
    said = _said(tmp_path)
    assert [s["id"] for s in said] == ["a", "b"]
    assert said[0]["parts"] == ["Robot arm has finished.", "Splendid."] and said[0]["frames"] == 2 * vw.SAMPLE_RATE
    assert not list((tmp_path / "state" / "voice").glob("*.wav"))  # the temp clip is gone


def test_a_failing_line_is_reported_and_the_worker_carries_on(tmp_path):
    p = _worker(tmp_path)
    try:
        _send(p, _say("bad", "FAIL-SYNTH"))
        p.stdin.write("not json\n[1,2]\n")
        _send(p, _say("good", "Fine."))
        bad = _reply(p)
        assert bad["id"] == "bad" and bad["ok"] is False and "fake synthesis failure" in bad["error"]
        assert _reply(p) == {"id": "good", "ok": True}
    finally:
        p.stdin.close()
        p.wait(10)


def test_stop_cuts_the_line_playing_now(tmp_path):
    p = _worker(tmp_path, delay="20")
    try:
        _send(p, _say("long", "A very long line."))
        deadline = time.monotonic() + 10
        while not (tmp_path / "state" / "voice").is_dir() and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.5)
        t0 = time.monotonic()
        _send(p, {"op": "stop"})
        assert _reply(p) == {"id": "long", "ok": True}
        assert time.monotonic() - t0 < 5
    finally:
        p.stdin.close()
        p.wait(10)
    assert _said(tmp_path)[0]["stopped"] is True


def test_short_espeak_data_keeps_a_short_path_and_copies_a_long_one(tmp_path):
    assert vw.short_espeak_data("/usr/share/espeak-ng-data", tmp_path) == "/usr/share/espeak-ng-data"
    deep = tmp_path / ("d" * 170) / "espeak-ng-data"
    deep.mkdir(parents=True)
    (deep / "phontab").write_bytes(b"x")
    state = tmp_path / "s"
    got = vw.short_espeak_data(str(deep), state)
    assert len(got) <= vw.ESPEAK_PATH_MAX and (Path(got) / "phontab").read_bytes() == b"x"
    (deep / "phontab").write_bytes(b"y")
    assert vw.short_espeak_data(str(deep), state) == got  # copied once, then reused


def test_short_espeak_data_falls_back_to_the_temp_folder(tmp_path, monkeypatch):
    deep = tmp_path / ("d" * 170) / "espeak-ng-data"
    deep.mkdir(parents=True)
    (deep / "phontab").write_bytes(b"x")
    (tmp_path / "t").mkdir()
    monkeypatch.setattr(vw.tempfile, "gettempdir", lambda: str(tmp_path / "t"))
    got = vw.short_espeak_data(str(deep), tmp_path / ("s" * 170))
    folder = Path(got).parent
    assert folder.parent == tmp_path / "t" and folder.name.startswith("bh-espeak-")  # private, per run
    assert (Path(got) / "phontab").read_bytes() == b"x"


def test_to_wav_scales_by_volume(tmp_path):
    import numpy as np
    path = tmp_path / "x.wav"
    secs = vw.to_wav(path, np.full(vw.SAMPLE_RATE, 0.5, dtype=np.float32), 50)
    assert secs == 1.0
    with wave.open(str(path), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (vw.SAMPLE_RATE, 1, 2)
        first = int.from_bytes(w.readframes(1), "little", signed=True)
    assert abs(first - 0.25 * 32767) < 2


@pytest.mark.skipif(sys.platform == "win32", reason="Linux players")
def test_find_player_prefers_pipewire(monkeypatch):
    have = {"paplay", "aplay"}
    monkeypatch.setattr(vw.shutil, "which", lambda c: f"/usr/bin/{c}" if c in have else None)
    assert vw.find_player() == ("paplay",)
    have.add("pw-play")
    assert vw.find_player() == ("pw-play",)
    have.clear()
    assert vw.find_player() is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows plays through winsound")
def test_find_player_is_winsound_on_windows():
    assert vw.find_player() == ("winsound",)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_player_stop_kills_a_real_player_process(tmp_path):
    script = tmp_path / "slowplay"
    script.write_text("#!/bin/sh\nexec sleep 30\n", encoding="utf-8")
    script.chmod(0o755)
    player = vw.Player((str(script),))
    wav = tmp_path / "a.wav"
    vw.to_wav(wav, [0.0] * 10, 80)
    import threading
    threading.Timer(0.5, player.stop).start()
    t0 = time.monotonic()
    player.play(wav, 1.0, 0)  # a stopped player is not an error
    assert time.monotonic() - t0 < 5


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX players")
def test_player_reports_a_failing_player(tmp_path):
    script = tmp_path / "badplay"
    script.write_text("#!/bin/sh\necho 'no such device' >&2\nexit 1\n", encoding="utf-8")
    script.chmod(0o755)
    wav = tmp_path / "a.wav"
    vw.to_wav(wav, [0.0] * 10, 80)
    with pytest.raises(RuntimeError, match="no such device"):
        vw.Player((str(script),)).play(wav, 1.0, 0)


MODEL_DIR = os.environ.get("BRAINHUB_VOICE_TEST_MODELS")


@pytest.mark.skipif(not MODEL_DIR, reason="set BRAINHUB_VOICE_TEST_MODELS to a folder with the voice files")
def test_real_synthesis_in_the_atlas_blend(tmp_path):
    engine = vw.Engine(tmp_path)
    req = {**_say("r", "Robot arm has finished. It took forty-two minutes.", "Splendid."),
           "model": str(Path(MODEL_DIR) / "kokoro-v1.0.int8.onnx"), "voices": str(Path(MODEL_DIR) / "voices-v1.0.bin")}
    audio = engine.synth(req)
    assert 3 * vw.SAMPLE_RATE < len(audio) < 12 * vw.SAMPLE_RATE
    with pytest.raises(ValueError, match="unknown voice"):
        engine.synth({**req, "blend": {"zz_nobody": 1}})


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX players")
def test_the_hub_going_away_ends_a_line_mid_play(tmp_path):
    """The hub closes the worker's stdin (or dies): the player must not play on by itself."""
    import io
    import threading
    script = tmp_path / "slowplay"
    script.write_text("#!/bin/sh\necho $$ > \"$0.pid\"\nexec sleep 30\n", encoding="utf-8")
    script.chmod(0o755)
    r, w = os.pipe()
    inp, wr = os.fdopen(r, "r", encoding="utf-8"), os.fdopen(w, "w", encoding="utf-8")
    out = io.StringIO()
    done = threading.Event()
    threading.Thread(target=lambda: (vw.serve(tmp_path / "state", inp, out, engine=vw.FakeEngine(),
                                              player=vw.Player((str(script),))), done.set()), daemon=True).start()
    wr.write(json.dumps(_say("a", "Long.")) + "\n")
    wr.flush()
    pidfile = Path(str(script) + ".pid")
    deadline = time.monotonic() + 10
    while not pidfile.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    pid = int(pidfile.read_text())
    wr.close()  # the hub is gone
    assert done.wait(10)
    time.sleep(0.2)
    with pytest.raises(OSError):
        os.kill(pid, 0)  # the player is gone too (reaped by communicate)


class SlowEngine(vw.FakeEngine):
    def synth(self, req):
        time.sleep(1.0)
        return super().synth(req)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX players")
def test_a_stop_during_synthesis_cancels_the_line(tmp_path):
    """Found in review: stop (or pause, or voice off) arriving while a line is still being
    synthesised used to be forgotten, and the line played in full."""
    import io
    import threading
    played = tmp_path / "played"
    script = tmp_path / "play"
    script.write_text(f"#!/bin/sh\necho x >> {played}\n", encoding="utf-8")
    script.chmod(0o755)
    r, w = os.pipe()
    inp, wr = os.fdopen(r, "r", encoding="utf-8"), os.fdopen(w, "w", encoding="utf-8")
    out = io.StringIO()
    done = threading.Event()
    threading.Thread(target=lambda: (vw.serve(tmp_path / "state", inp, out, engine=SlowEngine(),
                                              player=vw.Player((str(script),))), done.set()), daemon=True).start()
    wr.write(json.dumps(_say("a", "Cut me.")) + "\n")
    wr.flush()
    time.sleep(0.3)  # synthesising
    wr.write(json.dumps({"op": "stop"}) + "\n")
    wr.write(json.dumps(_say("b", "After the stop.")) + "\n")
    wr.flush()
    deadline = time.monotonic() + 10
    while out.getvalue().count("\n") < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    wr.close()
    assert done.wait(10)
    replies = [json.loads(x) for x in out.getvalue().splitlines()]
    assert replies == [{"id": "a", "ok": True}, {"id": "b", "ok": True}]
    assert played.read_text().count("x") == 1  # only the line after the stop played
