"""录制 / 重放：三形态。

1. 页面状态录制给模型选 —— browser_snapshot_ax 本身（带 ref 清单）。
2. 录制用户操作生成脚本 —— add_init_script 注入捕获 JS（click/input/select），
   转成 {tool, params} 步骤；密码掩码 [PASSWORD_MASKED]。
3. 截图+状态快照录制 —— 每个 AI 步骤后记录 url/title，周期性截证据帧。
"""
from __future__ import annotations

import base64
import logging
import time

from .browser import BrowserManager
from .errors import ToolError

log = logging.getLogger("easybrowser.recorder")

#: 每 N 个 AI 步骤截一张证据帧（性能与证据密度折中）
EVIDENCE_EVERY = 3

#: 注入页面的用户操作捕获脚本（导航后自动重注入）
CAPTURE_JS = r"""
() => {
  if (window.__ebCaptureInstalled) return;
  window.__ebCaptureInstalled = true;
  const send = (e) => { try { window.__ebRecord && window.__ebRecord(e); } catch (_) {} };
  const pick = (el) => el && el.closest
    ? (el.closest('button, a, [role="button"], input, textarea, select, label') || el)
    : el;
  document.addEventListener('click', (ev) => {
    const el = pick(ev.target);
    const tag = (el.tagName || '').toLowerCase();
    send({
      type: 'click', tag,
      text: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 80),
      id: el.id || '',
      name: el.getAttribute ? (el.getAttribute('name') || '') : '',
      href: (el.href || '').slice(0, 200),
    });
  }, true);
  document.addEventListener('input', (ev) => {
    const el = ev.target;
    const tag = (el.tagName || '').toLowerCase();
    if (tag !== 'input' && tag !== 'textarea') return;
    const isPwd = el.type === 'password';
    send({
      type: 'input', tag,
      id: el.id || '',
      name: el.getAttribute ? (el.getAttribute('name') || '') : '',
      placeholder: el.placeholder || '',
      value: isPwd ? '[PASSWORD_MASKED]' : (el.value || '').slice(0, 500),
    });
  }, true);
  document.addEventListener('change', (ev) => {
    const el = ev.target;
    if ((el.tagName || '').toLowerCase() !== 'select') return;
    send({
      type: 'select', tag: 'select',
      id: el.id || '',
      name: el.getAttribute ? (el.getAttribute('name') || '') : '',
      value: el.value || '',
    });
  }, true);
}
"""


class RecordSession:
    def __init__(self, record_id: int, tab_id: int | None, capture_user: bool):
        self.id = record_id
        self.tab_id = tab_id
        self.capture_user = capture_user
        self.steps: list[dict] = []       # {"tool","params","ts","source"}
        self.evidence: list[dict] = []    # {"idx","url","title","screenshot"}
        self.started = time.time()
        self.ended: float | None = None
        self.active = True
        self._ai_step_count = 0

    def script(self) -> dict:
        """可重放脚本：{version, steps:[{tool,params}]}。"""
        return {
            "version": 1,
            "record_id": self.id,
            "steps": [{"tool": s["tool"], "params": s["params"]} for s in self.steps],
        }


class RecorderManager:
    def __init__(self, browser: BrowserManager, registry):
        self._browser = browser
        self._registry = registry
        self.sessions: dict[int, RecordSession] = {}
        self._next_id = 1
        self._active_id: int | None = None

    # ------------------------------------------------------------ 生命周期
    async def start(self, tab_id: int | None = None, capture_user: bool = False) -> RecordSession:
        sid = self._next_id
        self._next_id += 1
        sess = RecordSession(sid, tab_id, capture_user)
        self.sessions[sid] = sess
        self._active_id = sid
        if capture_user:
            try:
                page = self._browser.get_page(tab_id)
                await page.expose_function("__ebRecord", self._on_user_event)
                await page.add_init_script(CAPTURE_JS)
                sess.capture_user = True
            except Exception as e:
                log.warning("注入用户捕获失败: %s", e)
        log.info("录制开始 record_id=%d tab=%s capture_user=%s", sid, tab_id, capture_user)
        return sess

    async def stop(self, record_id: int) -> dict:
        sess = self.sessions.get(record_id)
        if not sess:
            raise ToolError(f"录制会话 {record_id} 不存在")
        sess.active = False
        sess.ended = time.time()
        if self._active_id == record_id:
            self._active_id = None
        # 结束时补一张最终证据帧
        try:
            await self._capture_evidence(sess)
        except Exception:
            pass
        script = sess.script()
        script["duration_s"] = round(sess.ended - sess.started, 1)
        script["steps_total"] = len(sess.steps)
        return script

    def get(self, record_id: int) -> RecordSession | None:
        return self.sessions.get(record_id)

    # ------------------------------------------------------------ 记录
    def record_ai_step(self, tool: str, params: dict, ok: bool) -> None:
        """AI 工具调用自动记录（在激活录制会话时）。"""
        if self._active_id is None:
            return
        sess = self.sessions.get(self._active_id)
        if not sess or not sess.active:
            return
        sess.steps.append({
            "tool": tool, "params": dict(params), "ts": time.time(),
            "source": "ai", "ok": ok,
        })
        sess._ai_step_count += 1

    async def after_ai_step(self) -> None:
        """AI 步骤后的证据帧：周期截图 + 记录当前状态。"""
        if self._active_id is None:
            return
        sess = self.sessions.get(self._active_id)
        if not sess or not sess.active:
            return
        idx = len(sess.steps)
        # 记录状态（无开销）
        try:
            page = self._browser.get_page()
            sess.evidence.append({
                "idx": idx,
                "url": page.url,
                "title": await page.title() if page.url else "",
                "screenshot": None,
            })
        except Exception:
            pass
        # 周期截图（异步，不阻塞响应）
        if sess._ai_step_count % EVIDENCE_EVERY == 0:
            import asyncio
            asyncio.create_task(self._capture_evidence(sess))

    async def _capture_evidence(self, sess: RecordSession) -> None:
        try:
            page = self._browser.get_page(sess.tab_id)
            raw = await page.screenshot(type="jpeg", quality=35)
            b64 = base64.b64encode(raw).decode()
            if sess.evidence:
                sess.evidence[-1]["screenshot"] = b64
            else:
                sess.evidence.append({"idx": 0, "url": page.url, "title": "", "screenshot": b64})
        except Exception as e:
            log.warning("证据帧截图失败: %s", e)

    # ------------------------------------------------------------ 用户事件
    async def _on_user_event(self, event: dict) -> None:
        if self._active_id is None:
            return
        sess = self.sessions.get(self._active_id)
        if not sess or not sess.active:
            return
        step = self._event_to_step(event)
        if step:
            sess.steps.append({**step, "ts": time.time(), "source": "user"})

    def _event_to_step(self, ev: dict) -> dict | None:
        etype = ev.get("type")
        selector = self._selector(ev)
        if etype == "click":
            params = {}
            if selector:
                params["selector"] = selector
            elif ev.get("text"):
                params["text"] = ev["text"]
            elif ev.get("href"):
                params["selector"] = f'a[href="{ev["href"]}"]'
            else:
                return None
            return {"tool": "browser_click", "params": params}
        if etype == "input":
            if not selector:
                return None
            value = ev.get("value", "")
            if value == "[PASSWORD_MASKED]":
                value = "[PASSWORD_MASKED]"
            return {"tool": "browser_fill", "params": {"selector": selector, "value": value}}
        if etype == "select":
            if not selector:
                return None
            return {"tool": "browser_select_option",
                    "params": {"selector": selector, "value": ev.get("value", "")}}
        return None

    @staticmethod
    def _selector(ev: dict) -> str:
        tag = ev.get("tag", "")
        if ev.get("id"):
            return f"#{ev['id']}"
        if ev.get("name"):
            return f"{tag}[name=\"{ev['name']}\"]" if tag else ""
        return ""

    # ------------------------------------------------------------ 重放
    async def replay(self, source: dict | int) -> dict:
        if isinstance(source, int):
            sess = self.sessions.get(source)
            if not sess:
                raise ToolError(f"录制会话 {source} 不存在")
            script = sess.script()
        else:
            script = source
        steps = (script or {}).get("steps") or []
        if not isinstance(steps, list):
            raise ToolError("脚本格式错误：steps 必须是数组")
        results = []
        all_ok = True
        for i, step in enumerate(steps):
            tool = step.get("tool", "")
            params = step.get("params") or {}
            r = await self._registry.run(tool, params)
            ok = not r.is_error
            results.append({"step": i, "tool": tool, "ok": ok,
                            "result": r.text, "error": r.text if r.is_error else ""})
            if not ok:
                all_ok = False
        return {"ok": all_ok, "executed": len(results), "results": results}
