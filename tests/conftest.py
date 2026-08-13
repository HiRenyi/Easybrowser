"""pytest 共享 fixture：launch 模式独立 headless Chrome。"""
from __future__ import annotations

import pytest
import pytest_asyncio

from easybrowser.config import Config
from easybrowser.browser import BrowserManager

#: 通用测试页面（按钮/输入框/计数）
TEST_HTML = """<!DOCTYPE html><html><head><title>Test Page</title></head><body>
<button id="btn1">Click Me</button>
<button id="btn2">Second</button>
<input id="inp1" placeholder="Name" type="text">
<p id="out">0</p>
<a href="https://example.com">Example Link</a>
<script>
document.getElementById('btn1').addEventListener('click', () => {
  const o = document.getElementById('out');
  o.textContent = String(Number(o.textContent) + 1);
});
</script>
</body></html>"""


@pytest.fixture
def test_page_url(tmp_path):
    p = tmp_path / "test_page.html"
    p.write_text(TEST_HTML, encoding="utf-8")
    return p.as_uri()


@pytest.fixture(scope="session")
def chrome_cfg() -> Config:
    return Config(
        mode="launch",
        profile_dir="/tmp/eb-test-profile",
        executable_path="/usr/bin/google-chrome",
        headless=True,
        action_timeout_ms=10000,
        nav_timeout_ms=15000,
        lock_timeout_ms=5000,
    )


@pytest_asyncio.fixture
async def browser(chrome_cfg):
    """每个测试独立的 BrowserManager（复用同一 Chrome profile）。"""
    bm = BrowserManager(chrome_cfg)
    await bm.connect()
    yield bm
    await bm.close()
