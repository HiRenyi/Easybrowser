"""BrowserManager tab 管理核心测试。"""
from __future__ import annotations

import asyncio

import pytest

from easybrowser.errors import TabError


async def test_connect_registers_existing_pages(browser):
    # connect 后至少有一个 page（持久化 context 的首个 about:blank）
    tabs = await browser.list_tabs()
    assert len(tabs) >= 1
    assert all("tab_id" in t and "url" in t for t in tabs)


async def test_new_tab_auto_registers_and_active(browser):
    before = len(await browser.list_tabs())
    info = await browser.new_tab()
    after = await browser.list_tabs()
    assert len(after) == before + 1
    # 新 tab 自动设为 active
    assert browser._active_tab_id == info["tab_id"]
    # get_page() 默认返回 active 页
    page = browser.get_page()
    assert page is browser.get_page(info["tab_id"])


async def test_new_tab_with_url(browser):
    info = await browser.new_tab("data:text/html,<title>Hello Test</title><h1>hi</h1>")
    assert "data:" in info["url"]
    # Playwright page 已导航
    page = browser.get_page(info["tab_id"])
    assert await page.title() == "Hello Test"


async def test_select_tab_switches_active(browser):
    t1 = await browser.new_tab()
    t2 = await browser.new_tab()
    await browser.select_tab(t1["tab_id"], focus=False)
    assert browser._active_tab_id == t1["tab_id"]
    page = browser.get_page()
    assert page is browser.get_page(t1["tab_id"])
    await browser.select_tab(t2["tab_id"], focus=False)
    assert browser._active_tab_id == t2["tab_id"]


async def test_get_page_unknown_tab_raises(browser):
    with pytest.raises(TabError):
        browser.get_page(999999)


async def test_close_tab_unregisters_and_active_fallback(browser):
    t1 = await browser.new_tab()
    t2 = await browser.new_tab()
    t3 = await browser.new_tab()
    # 当前 active = t3
    assert browser._active_tab_id == t3["tab_id"]
    await browser.close_tab(t3["tab_id"])
    # t3 注销，active 回退到某个存活的 tab（launch 模式含初始 about:blank 页）
    assert t3["tab_id"] not in browser._pages
    assert browser._active_tab_id != t3["tab_id"]
    assert browser._active_tab_id in browser._pages
    # 关掉的页不能再操作
    with pytest.raises(TabError):
        browser.get_page(t3["tab_id"])


async def test_write_lock_serializes_same_tab(browser):
    tid = (await browser.new_tab())["tab_id"]
    order = []

    async def job(n):
        async with browser.write_lock(tid):
            order.append(n)
            await asyncio.sleep(0.05)
            order.append(-n)

    await asyncio.gather(job(1), job(2), job(3))
    # 同页写操作必须完全串行：1,-1,2,-2,3,-3 或等价配对
    assert order[0] == order[1] * -1
    assert len(order) == 6
    for i in range(0, 6, 2):
        assert order[i] == -order[i + 1]


async def test_target_id_stable(browser):
    tid = (await browser.new_tab())["tab_id"]
    tid1 = await browser.target_id(tid)
    tid2 = await browser.target_id(tid)
    assert tid1 == tid2 and tid1 != ""


async def test_console_buffer_and_dialog(browser):
    tid = (await browser.new_tab("data:text/html,<script>console.log('hello-log')</script>"))["tab_id"]
    await asyncio.sleep(0.5)
    assert any("hello-log" in c for c in browser._console_buffers.get(tid, []))


async def test_tab_isolation_refmap(browser):
    """refmap 按 tab 隔离，不串页。"""
    t1 = (await browser.new_tab())["tab_id"]
    t2 = (await browser.new_tab())["tab_id"]
    browser.set_refmap(t1, {"e1": {"role": "button", "name": "A", "nth": 1}})
    assert browser.get_refmap(t1)["e1"]["name"] == "A"
    assert t2 not in browser.get_refmap(t2) or browser.get_refmap(t2) == {}
