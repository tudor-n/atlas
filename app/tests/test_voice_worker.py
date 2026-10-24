import json
import os
import subprocess
import sys
import time
import wave
from pathlib import Path

import pytest

from atlas import voice_worker as vw

APP = Path(__file__).resolve().parents[1]


def _worker(tmp_path, delay="0", extra_env=None):
    env = {**os.environ, "ATLAS_VOICE_TRACE": "1", "ATLAS_VOICE_FAKE": str(tmp_path / "said.jsonl"), "ATLAS_VOICE_FAKE_DELAY": delay,
           **(extra_env or {})}
    return subprocess.Popen([sys.executable, "-m", "atlas", "voice-worker", "--state", str(tmp_path / "state")],
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


MODEL_DIR = os.environ.get("ATLAS_VOICE_TEST_MODELS")


@pytest.mark.skipif(not MODEL_DIR, reason="set ATLAS_VOICE_TEST_MODELS to a folder with the voice files")
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


# -- boost and ducking (owner, 2026-10-24: the voice was lost under music) ---------------------

def _speechlike(level_db, seconds=2.0):
    """Half a second of tone, then a pause, repeated: voiced at `level_db` RMS, silent between."""
    import numpy as np
    sr = vw.SAMPLE_RATE
    t = np.arange(int(sr * seconds)) / sr
    tone = np.sin(2 * np.pi * 220 * t) * np.sqrt(2) * 10 ** (level_db / 20)
    gate = (np.floor(t * 2) % 2 == 0)  # on, off, on, off…
    return (tone * gate).astype(np.float32)


def _voiced_db(x):
    import numpy as np
    x = np.asarray(x)
    v = x[np.abs(x) > 1e-4]
    return 10 * np.log10(np.mean(v ** 2))


def test_boost_brings_a_quiet_line_to_the_target_without_clipping():
    import numpy as np
    out = vw.boost(_speechlike(-24))
    assert abs(_voiced_db(out) - vw.BOOST_TARGET_DB) < 1.5
    assert np.max(np.abs(out)) <= vw.LIMIT_CEILING + 1e-6
    assert np.all(out[np.abs(_speechlike(-24)) == 0] == 0)  # the pauses stay silent


def test_boost_never_makes_a_line_quieter_and_caps_its_gain():
    import numpy as np
    loud = _speechlike(-12)
    assert _voiced_db(vw.boost(loud)) >= _voiced_db(loud) - 1.0  # never quieter (the limiter may bend peaks)
    faint = _speechlike(-60)
    assert _voiced_db(vw.boost(faint)) <= -60 + vw.BOOST_MAX_GAIN_DB + 0.5
    assert np.all(vw.boost(np.zeros(4800, dtype=np.float32)) == 0)
    assert len(vw.boost(np.zeros(5, dtype=np.float32))) == 5  # shorter than a frame: unchanged


def test_soft_limit_leaves_the_body_alone_and_bends_peaks():
    import numpy as np
    x = np.array([0.0, 0.3, -0.69, 0.71, 1.5, -3.0], dtype=np.float32)
    y = vw.soft_limit(x)
    assert list(y[:3]) == pytest.approx([0.0, 0.3, -0.69])
    assert 0.7 < y[3] < 0.72 and 0.7 < y[4] <= vw.LIMIT_CEILING and -vw.LIMIT_CEILING <= y[5] < -0.7
    assert np.all(np.diff(vw.soft_limit(np.linspace(0, 5, 200, dtype=np.float32))) >= 0)  # monotonic


def test_to_wav_boosted_is_louder(tmp_path):
    import numpy as np
    plain, boosted = tmp_path / "p.wav", tmp_path / "b.wav"
    vw.to_wav(plain, _speechlike(-24), 80)
    vw.to_wav(boosted, _speechlike(-24), 80, boosted=True)
    level = lambda p: _voiced_db(np.frombuffer(wave.open(str(p)).readframes(10 ** 7), dtype="<i2") / 32767)
    assert level(boosted) > level(plain) + 6


FAKE_PACTL = r'''
import json, os, sys
db = os.environ["FAKE_PACTL_DB"]
streams = json.load(open(db))
a = sys.argv[1:]
with open(db + ".calls", "a") as f:
    f.write(json.dumps(a) + "\n")
if a[:4] == ["-f", "json", "list", "sink-inputs"]:
    print(json.dumps([{"index": int(i), "volume": {f"ch{n}": {"value": v} for n, v in enumerate(vols)},
                       "properties": {"application.name": "x"}} for i, vols in streams.items()]))
elif a[0] == "set-sink-input-volume":
    streams[a[1]] = [int(v) for v in a[2:]]
    json.dump(streams, open(db, "w"))
'''


@pytest.fixture
def pactl(tmp_path, monkeypatch):
    from tests.fakes import fake_cli
    db = tmp_path / "streams.json"
    db.write_text(json.dumps({"7": [65536, 65536], "9": [32768]}), encoding="utf-8")
    monkeypatch.setenv("FAKE_PACTL_DB", str(db))
    exe = fake_cli(tmp_path, FAKE_PACTL, name="pactl")
    streams = lambda: json.loads(db.read_text(encoding="utf-8"))
    return exe, streams, db


@pytest.mark.skipif(sys.platform == "win32", reason="pactl is Linux")
def test_ducking_lowers_other_streams_and_gives_their_volume_back(tmp_path, pactl):
    exe, streams, _ = pactl
    d = vw.Ducker(tmp_path / "state", pactl=exe)
    record = d.duck(30)
    assert streams() == {"7": [19661, 19661], "9": [9830]}
    assert (tmp_path / "state" / "voice" / "ducked.json").is_file()  # written before anything changed
    d.restore(record)
    assert streams() == {"7": [65536, 65536], "9": [32768]}
    assert not (tmp_path / "state" / "voice" / "ducked.json").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="pactl is Linux")
def test_a_volume_changed_while_speaking_is_left_as_the_owner_set_it(tmp_path, pactl):
    exe, streams, db = pactl
    d = vw.Ducker(tmp_path / "state", pactl=exe)
    record = d.duck(30)
    s = streams(); s["7"] = [40000, 40000]; db.write_text(json.dumps(s), encoding="utf-8")  # the owner turned it up
    d.restore(record)
    assert streams() == {"7": [40000, 40000], "9": [32768]}


@pytest.mark.skipif(sys.platform == "win32", reason="pactl is Linux")
def test_a_worker_that_died_mid_line_is_undone_by_the_next(tmp_path, pactl):
    exe, streams, _ = pactl
    vw.Ducker(tmp_path / "state", pactl=exe).duck(30)  # and then it dies
    vw.Ducker(tmp_path / "state", pactl=exe).recover()
    assert streams() == {"7": [65536, 65536], "9": [32768]}


@pytest.mark.skipif(sys.platform == "win32", reason="pactl is Linux")
def test_no_ducking_at_100_or_without_pactl(tmp_path, pactl):
    exe, streams, db = pactl
    assert vw.Ducker(tmp_path / "state", pactl=exe).duck(100) is None
    assert vw.Ducker(tmp_path / "state", pactl="").duck(30) is None
    assert vw.Ducker(tmp_path / "state", pactl=exe).duck("loud") is None
    assert streams() == {"7": [65536, 65536], "9": [32768]}
    assert not Path(str(db) + ".calls").exists() or "set-sink-input-volume" not in Path(str(db) + ".calls").read_text()


def test_serve_ducks_around_each_line_with_the_request_level(tmp_path):
    import io
    calls = []

    class Recorder:
        def recover(self): calls.append("recover")
        def duck(self, level): calls.append(("duck", level)); return {"r": level}
        def restore(self, rec): calls.append(("restore", rec))

    import threading
    r, w = os.pipe()
    inp, wr = os.fdopen(r, "r", encoding="utf-8"), os.fdopen(w, "w", encoding="utf-8")
    out = io.StringIO()
    done = threading.Event()
    threading.Thread(target=lambda: (vw.serve(tmp_path / "state", inp, out, engine=vw.FakeEngine(),
                                              player=vw.FakePlayer(tmp_path / "said.jsonl"), ducker=Recorder()),
                                     done.set()), daemon=True).start()
    wr.write(json.dumps({**_say("a", "One."), "boost": True, "duck_to": 25}) + "\n")
    wr.flush()
    deadline = time.monotonic() + 10
    while '"id": "a"' not in out.getvalue() and time.monotonic() < deadline:
        time.sleep(0.05)
    wr.close()
    assert done.wait(10)
    assert calls == ["recover", ("duck", 25), ("restore", {"r": 25})]
    assert json.loads(out.getvalue().splitlines()[-1]) == {"id": "a", "ok": True}
