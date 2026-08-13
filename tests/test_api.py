"""HTTP API 层测试（FastAPI + launch headless Chrome）。"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from easybrowser.api import create_app
from easybrowser.config import Config


@pytest_asyncio.fixture
async def client(tmp_path):
    cfg = Config(mode="launch", profile_dir="/tmp/eb-test-profile",
                 executable_path="/usr/bin/google-chrome", headless=True)
    app = create_app(cfg)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ok"
    assert d["tools"] == 52


async def test_list_tools(client):
    r = await client.get("/api/tools")
    assert r.status_code == 200
    d = r.json()
    assert d["count"] == 52
    names = {t["name"] for t in d["tools"]}
    assert "browser_navigate" in names and "browser_snapshot_ax" in names
    assert "browser_click" in names and "browser_cdp_call" in names


async def test_call_tool_navigate(client):
    r = await client.post("/api/tool/browser_new_tab",
                          json={"url": "data:text/html,<title>API Test</title>"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    tab_id = d["result"]
    r = await client.post("/api/tool/browser_get_title", json={})
    assert r.status_code == 200
    assert r.json()["result"] == "API Test"


async def test_call_tool_unknown(client):
    r = await client.post("/api/tool/nonexistent_tool", json={})
    assert r.status_code == 404
    assert r.json()["ok"] is False


async def test_batch(client):
    steps = [
        {"tool": "browser_new_tab", "params": {"url": "data:text/html,<p>hi</p>"}},
        {"tool": "browser_get_url", "params": {}},
    ]
    r = await client.post("/api/batch", json={"steps": steps})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["executed"] == 2
    assert all(s["ok"] for s in d["results"])


async def test_batch_stop_on_error(client):
    steps = [
        {"tool": "browser_get_url", "params": {}},
        {"tool": "not_a_tool", "params": {}},
        {"tool": "browser_get_title", "params": {}},
    ]
    r = await client.post("/api/batch", json={"steps": steps})
    d = r.json()
    assert d["ok"] is False
    assert d["executed"] == 2  # 停在错误步骤


async def test_snapshot_ax_via_api(client):
    await client.post("/api/tool/browser_new_tab",
                      json={"url": "data:text/html,<button>点我</button>"})
    r = await client.post("/api/tool/browser_snapshot_ax", json={})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert "ref=" in d["result"]
