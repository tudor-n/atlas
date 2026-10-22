import pytest

from brainhub.server import create_app
from tests.test_server import make_runner


@pytest.fixture
async def client(aiohttp_client, paths, tmp_path):
    app = create_app(paths, runner=make_runner(), start_workers=False,
                     claude_settings_path=tmp_path / "s" / "settings.json")
    return await aiohttp_client(app)


async def test_integration_roundtrip(client, tmp_path):
    j = await (await client.get("/api/integration")).json()
    assert j["hooks_installed"] is False and j["last_hook_at"] is None and j["last_usage_at"] is None
    r = await client.post("/api/integration/install", json={})
    assert r.status == 200 and (await r.json())["hooks_installed"] is True
    assert (await (await client.get("/api/integration")).json())["hooks_installed"] is True
    r = await client.post("/api/integration/uninstall", json={})
    assert (await r.json())["hooks_installed"] is False


async def test_integration_invalid_json_is_400(client, tmp_path):
    f = tmp_path / "s" / "settings.json"
    f.parent.mkdir()
    f.write_text("{ bad")
    assert (await client.post("/api/integration/install", json={})).status == 400
