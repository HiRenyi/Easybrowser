"""录制/重放功能测试。"""
from __future__ import annotations

import pytest

from easybrowser.recorder import RecorderManager
from easybrowser.tools import build_registry


@pytest.fixture
async def recorder(browser):
    reg = build_registry(browser)
    rm = RecorderManager(browser, reg)
    return rm, reg


async def test_ai_track_recording(recorder, test_page_url):
    rm, reg = recorder
    sess = await rm.start()
    assert sess.active and sess.id == 1
    # 模拟 AI 步骤
    tid = (await rm._browser.new_tab(test_page_url))["tab_id"]
    rm.record_ai_step("browser_get_title", {"tab_id": tid}, True)
    rm.record_ai_step("browser_get_url", {"tab_id": tid}, True)
    script = await rm.stop(sess.id)
    assert script["steps_total"] == 2
    assert script["steps"][0]["tool"] == "browser_get_title"
    assert script["version"] == 1


async def test_replay(recorder, test_page_url):
    rm, reg = recorder
    sess = await rm.start()
    # 录制真实操作并重放
    steps = [
        {"tool": "browser_new_tab", "params": {"url": test_page_url}},
        {"tool": "browser_get_url", "params": {}},
    ]
    for s in steps:
        rm.record_ai_step(s["tool"], s["params"], True)
    script = await rm.stop(sess.id)
    result = await rm.replay(script)
    assert result["ok"] is True
    assert result["executed"] == 2
    # 按 record_id 重放
    result2 = await rm.replay(sess.id)
    assert result2["ok"] is True


async def test_evidence_after_ai_step(recorder, test_page_url):
    rm, reg = recorder
    sess = await rm.start()
    await rm._browser.new_tab(test_page_url)
    rm.record_ai_step("browser_get_title", {}, True)
    await rm.after_ai_step()
    assert len(sess.evidence) >= 1
    assert "url" in sess.evidence[0]
    # 周期截图：连续 N 步后 evidence 应含截图
    from easybrowser.recorder import EVIDENCE_EVERY
    for _ in range(EVIDENCE_EVERY):
        rm.record_ai_step("browser_get_url", {}, True)
        await rm.after_ai_step()
    await rm.stop(sess.id)
    assert any(e.get("screenshot") for e in sess.evidence)


async def test_user_event_to_step(recorder):
    rm, reg = recorder
    sess = await rm.start()
    # 模拟用户事件（密码掩码）
    await rm._on_user_event({"type": "click", "tag": "button", "id": "btn",
                             "text": "提交", "name": ""})
    await rm._on_user_event({"type": "input", "tag": "input", "id": "pwd",
                             "name": "password", "value": "[PASSWORD_MASKED]"})
    script = await rm.stop(sess.id)
    assert script["steps_total"] == 2
    assert script["steps"][0]["tool"] == "browser_click"
    assert script["steps"][0]["params"].get("selector") == "#btn"
    assert script["steps"][1]["tool"] == "browser_fill"
    assert script["steps"][1]["params"].get("value") == "[PASSWORD_MASKED]"
