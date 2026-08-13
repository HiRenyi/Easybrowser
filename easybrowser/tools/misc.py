"""其他（7 个）工具：evaluate_js / cdp_call / clipboard / console_logs / download_media / 会话。"""
from __future__ import annotations

import json
from pathlib import Path

from ..errors import ToolError
from ._util import get_str, require_str
from .registry import ToolContext, ToolResult


def build_misc(reg):
    async def browser_evaluate_js(ctx: ToolContext, p: dict) -> ToolResult:
        expression = require_str(p, "expression")
        # Playwright evaluate 默认会 await Promise 结果
        try:
            result = await ctx.page.evaluate(expression)
        except Exception as e:
            raise ToolError(f"JS 执行失败: {e}")
        try:
            return ToolResult.ok(json.dumps(result, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            return ToolResult.ok(str(result))

    reg.add("browser_evaluate_js",
            "执行 JavaScript 并返回结果（原生 JSON 值，一次编码无 double-encoding）。",
            {"expression": {"type": "string", "description": "JS 表达式（必填）"},
             "awaitPromise": {"type": "boolean", "description": "是否等待 Promise 结果（默认 true）"},
             "tab_id": {"type": "integer"}},
            kind="read", handler=browser_evaluate_js)

    async def browser_cdp_call(ctx: ToolContext, p: dict) -> ToolResult:
        method = require_str(p, "method")
        params = p.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        # Target.* / Browser.* 走浏览器级 session，其余走页级 session
        if method.startswith(("Target.", "Browser.", "SystemInfo.")):
            sess = await ctx.browser.browser_cdp()
        else:
            sess = await ctx.cdp()
        try:
            result = await sess.send(method, params)
        except Exception as e:
            raise ToolError(f"CDP {method} 失败: {e}")
        return ToolResult.ok(json.dumps(result, ensure_ascii=False, default=str))

    reg.add("browser_cdp_call", "原始 CDP 协议调用（Target.*/Browser.* 走浏览器级会话，其余走页级）",
            {"method": {"type": "string", "description": "CDP 方法名，如 DOM.getDocument（必填）"},
             "params": {"type": "object", "description": "CDP 参数"},
             "tab_id": {"type": "integer"}},
            kind="read", handler=browser_cdp_call)

    async def browser_clipboard(ctx: ToolContext, p: dict) -> ToolResult:
        op = get_str(p, "op", "read").lower()
        try:
            bcdp = await ctx.browser.browser_cdp()
            await bcdp.send("Browser.grantPermissions", {
                "permissions": ["clipboardReadWrite", "clipboardSanitizedWrite"]})
        except Exception:
            pass
        if op in ("read", "get"):
            try:
                text = await ctx.page.evaluate("navigator.clipboard.readText()")
            except Exception:
                text = ""
            return ToolResult.ok(text if text else "(empty)")
        text = require_str(p, "text")
        try:
            await ctx.page.evaluate("(t) => navigator.clipboard.writeText(t)", text)
        except Exception as e:
            raise ToolError(f"写入剪贴板失败: {e}")
        return ToolResult.ok("已写入剪贴板")

    reg.add("browser_clipboard", "读写剪贴板",
            {"op": {"type": "string", "description": "read(默认)/write"},
             "text": {"type": "string", "description": "write 时要写入的文本"},
             "tab_id": {"type": "integer"}},
            kind="read", handler=browser_clipboard)

    async def browser_console_logs(ctx: ToolContext, p: dict) -> ToolResult:
        limit = int(p.get("limit", 50))
        buf = ctx.browser._console_buffers.get(ctx.tab_id, [])
        lines = buf[-limit:]
        if not lines:
            return ToolResult.ok("(无 console 日志)")
        return ToolResult.ok("\n".join(lines))

    reg.add("browser_console_logs", "读取页面 console 日志",
            {"limit": {"type": "integer", "description": "返回条数（默认 50）"},
             "tab_id": {"type": "integer"}},
            kind="read", handler=browser_console_logs)

    async def browser_download_media(ctx: ToolContext, p: dict) -> ToolResult:
        url = p.get("url")
        if not url:
            # 列出页面媒体资源
            items = await ctx.page.evaluate(
                """() => Array.from(document.querySelectorAll('img, video, audio, source'))
                     .map(e => e.currentSrc || e.src).filter(Boolean).slice(0, 50)""")
            return ToolResult.ok(json.dumps(list(set(items)), ensure_ascii=False))
        # 抓取指定 URL 保存到数据目录 downloads/
        data_dir = ctx.cfg._default_data_dir() if hasattr(ctx.cfg, "_default_data_dir") else Path.home() / ".easybrowser"
        out_dir = Path(data_dir) / "downloads"
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = Path(url.split("?")[0]).name or "download"
        out = out_dir / fname
        try:
            resp = await ctx.page.context.request.get(url)
            body = await resp.body()
            out.write_bytes(body)
        except Exception as e:
            raise ToolError(f"下载失败: {e}")
        return ToolResult.ok(f"已下载到 {out}（{len(body)} 字节）")

    reg.add("browser_download_media", "下载媒体资源（给 url 下载，否则列出页面媒体 URL）",
            {"url": {"type": "string", "description": "可选，要下载的 URL"},
             "tab_id": {"type": "integer"}},
            kind="read", handler=browser_download_media)

    async def browser_name_session(ctx: ToolContext, p: dict) -> ToolResult:
        name = require_str(p, "name")
        ctx.browser.set_session_name(name)
        return ToolResult.ok(f"会话已命名: {name}")

    reg.add("browser_name_session", "为当前会话命名（便于区分多会话）",
            {"name": {"type": "string", "description": "会话名（必填）"}},
            kind="write", handler=browser_name_session)

    async def browser_finish_session(ctx: ToolContext, p: dict) -> ToolResult:
        ctx.browser.clear_refmap(ctx.tab_id)
        ctx.browser.clear_nodemap(ctx.tab_id)
        ctx.browser._console_buffers.pop(ctx.tab_id, None)
        return ToolResult.ok(f"会话已结束（清空 ref/node/console 状态），tab_id={ctx.tab_id}")

    reg.add("browser_finish_session", "结束会话：清空该页的 ref/node/console 状态",
            {"tab_id": {"type": "integer"}},
            kind="write", handler=browser_finish_session)
