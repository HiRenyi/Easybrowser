"""工具层端到端测试（launch headless Chrome + 本地测试页面）。"""
from __future__ import annotations

import json

import pytest

from easybrowser.tools import build_registry


async def _run(reg, name, params=None):
    return await reg.run(name, params or {})


async def _open_page(browser, url):
    info = await browser.new_tab(url)
    return info["tab_id"]


async def test_navigate_and_read(browser, test_page_url):
    reg = build_registry(browser)
    tid = await _open_page(browser, test_page_url)
    r = await _run(reg, "browser_get_title", {"tab_id": tid})
    assert r.text == "Test Page"
    r = await _run(reg, "browser_get_url", {"tab_id": tid})
    assert r.text.startswith("file://")


async def test_snapshot_ax_refs(browser, test_page_url):
    reg = build_registry(browser)
    tid = await _open_page(browser, test_page_url)
    r = await _run(reg, "browser_snapshot_ax", {"tab_id": tid})
    assert not r.is_error
    assert "ref=" in r.text
    # refmap 已按 tab 存储
    rm = browser.get_refmap(tid)
    assert len(rm) >= 2
    assert any(e["role"] == "button" for e in rm.values())


async def test_click_via_ref(browser, test_page_url):
    reg = build_registry(browser)
    tid = await _open_page(browser, test_page_url)
    await _run(reg, "browser_snapshot_ax", {"tab_id": tid})
    rm = browser.get_refmap(tid)
    btn_ref = next(e["ref"] for e in rm.values() if e["role"] == "button" and e["name"] == "Click Me")
    # 点之前 out=0
    r = await _run(reg, "browser_evaluate_js",
                   {"tab_id": tid, "expression": "document.getElementById('out').textContent"})
    assert r.text.strip('"') == "0"
    r = await _run(reg, "browser_click", {"tab_id": tid, "ref": btn_ref})
    assert not r.is_error
    r = await _run(reg, "browser_evaluate_js",
                   {"tab_id": tid, "expression": "document.getElementById('out').textContent"})
    assert r.text.strip('"') == "1"


async def test_fill_via_ref(browser, test_page_url):
    reg = build_registry(browser)
    tid = await _open_page(browser, test_page_url)
    await _run(reg, "browser_snapshot_ax", {"tab_id": tid})
    rm = browser.get_refmap(tid)
    inp_ref = next(e["ref"] for e in rm.values()
                   if e["role"] == "textbox" or (e["name"] == "Name"))
    r = await _run(reg, "browser_fill", {"tab_id": tid, "ref": inp_ref, "value": "张三"})
    assert not r.is_error
    r = await _run(reg, "browser_evaluate_js",
                   {"tab_id": tid, "expression": "document.getElementById('inp1').value"})
    assert r.text.strip('"') == "张三"


async def test_evaluate_js_no_double_encoding(browser, test_page_url):
    """evaluate_js 返回原生值，仅一次 JSON 编码。"""
    reg = build_registry(browser)
    tid = await _open_page(browser, test_page_url)
    r = await _run(reg, "browser_evaluate_js",
                   {"tab_id": tid, "expression": "({a: 1, b: 'hi'})"})
    assert not r.is_error
    parsed = json.loads(r.text)
    assert parsed == {"a": 1, "b": "hi"}


async def test_new_tab_auto_active(browser, test_page_url):
    reg = build_registry(browser)
    tid1 = await _open_page(browser, test_page_url)
    r = await _run(reg, "browser_new_tab", {"url": "data:text/html,<title>New Tab</title>"})
    assert not r.is_error
    # 新 tab 自动成为 active，无需手动 switch
    r = await _run(reg, "browser_get_title", {})
    assert r.text == "New Tab"


async def test_list_tabs_and_close(browser, test_page_url):
    reg = build_registry(browser)
    tid1 = await _open_page(browser, test_page_url)
    tid2 = await _open_page(browser, test_page_url)
    r = await _run(reg, "browser_list_tabs", {})
    assert not r.is_error
    assert f"[{tid1}]" in r.text and f"[{tid2}]" in r.text
    # 关闭后不再列出
    await _run(reg, "browser_close_tab", {"tabId": tid2})
    r = await _run(reg, "browser_list_tabs", {})
    assert f"[{tid2}]" not in r.text


async def test_click_at_coords(browser, test_page_url):
    reg = build_registry(browser)
    tid = await _open_page(browser, test_page_url)
    # 找到按钮坐标
    r = await _run(reg, "browser_snapshot", {"tab_id": tid})
    assert not r.is_error
    nm = browser.get_nodemap(tid)
    # 找一个按钮的坐标
    entry = next((e for e in nm.values() if e["tag"] == "button"), None)
    if entry:
        r = await _run(reg, "browser_click_at", {"tab_id": tid, "x": entry["x"], "y": entry["y"]})
        assert not r.is_error


async def test_snapshot_returns_nodes(browser, test_page_url):
    reg = build_registry(browser)
    tid = await _open_page(browser, test_page_url)
    r = await _run(reg, "browser_snapshot", {"tab_id": tid})
    assert not r.is_error
    assert "[1]" in r.text
