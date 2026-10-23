import hashlib
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from brainhub import voice_files as vf


@pytest.fixture
def served(tmp_path, monkeypatch):
    """A local web server for the pinned files, with small stand-ins for the real ones."""
    root = tmp_path / "web"
    root.mkdir()
    pins = {}
    for key, name, body in (("int8", "kokoro-v1.0.int8.onnx", b"model-int8" * 1000),
                            ("fp32", "kokoro-v1.0.onnx", b"model-fp32" * 3000),
                            ("voices", "voices-v1.0.bin", b"voices" * 500)):
        (root / name).write_bytes(body)
        pins[key] = vf.VoiceFile(name, len(body), hashlib.sha256(body).hexdigest())
    monkeypatch.setattr(vf, "MODELS", {"int8": pins["int8"], "fp32": pins["fp32"]})
    monkeypatch.setattr(vf, "VOICES", pins["voices"])
    handler = partial(type("Quiet", (SimpleHTTPRequestHandler,), {"log_message": lambda *a: None}), directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield root, f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


def test_download_checks_and_installs_both_files(tmp_path, served):
    _, url = served
    data = tmp_path / "data"
    seen = []
    d = vf.Downloader(data, base_url=url, on_change=seen.append)
    assert vf.files(data, "int8") is None and vf.missing_bytes(data, "int8") == vf.MODELS["int8"].size + vf.VOICES.size
    assert d.start("int8") is True
    d.wait(10)
    assert d.state["error"] is None and d.state["running"] is False and d.state["done"] == d.state["total"]
    got = vf.files(data, "int8")
    assert got == {"model": str(data / "voice" / "kokoro-v1.0.int8.onnx"), "voices": str(data / "voice" / "voices-v1.0.bin")}
    assert not list((data / "voice").glob("*.part"))
    assert seen[0]["running"] is True and seen[-1]["running"] is False
    assert d.start("int8") is False  # nothing left to fetch
    assert d.start("fp32") is True  # the voices file is shared: only the model comes down
    d.wait(10)
    assert d.state["total"] == vf.MODELS["fp32"].size and vf.files(data, "fp32")


def test_a_file_that_fails_its_checksum_is_never_installed(tmp_path, served):
    root, url = served
    (root / "voices-v1.0.bin").write_bytes(b"x" * vf.VOICES.size)  # right size, wrong bytes
    data = tmp_path / "data"
    d = vf.Downloader(data, base_url=url)
    d.start("int8")
    d.wait(10)
    assert "checksum" in d.state["error"]
    assert vf.files(data, "int8") is None
    assert not (data / "voice" / "voices-v1.0.bin").exists() and not list((data / "voice").glob("*.part"))


def test_a_missing_or_short_file_is_an_error(tmp_path, served):
    root, url = served
    (root / "kokoro-v1.0.int8.onnx").unlink()
    d = vf.Downloader(tmp_path / "data", base_url=url)
    d.start("int8")
    d.wait(10)
    assert "404" in d.state["error"]
    (root / "kokoro-v1.0.int8.onnx").write_bytes(b"short")
    d.start("int8")
    d.wait(10)
    assert "incomplete" in d.state["error"]


def test_an_unknown_model_is_refused(tmp_path):
    with pytest.raises(ValueError):
        vf.Downloader(tmp_path).start("int4")


def test_the_real_pins_are_the_spike_files():
    assert vf.BASE_URL.startswith("https://github.com/thewh1teagle/kokoro-onnx/releases/download/")
    assert vf.MODELS["int8"].sha256.startswith("6e742170") and vf.VOICES.sha256.startswith("bca610b8")
