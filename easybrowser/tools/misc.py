"""其他（7 个）工具：evaluate_js / cdp_call / clipboard / console_logs / download_media / 会话。"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from ..errors import ToolError
from ._util import get_str, require_str
from .registry import ToolContext, ToolResult

# 审计日志记录器
_audit_logger = logging.getLogger("easybrowser.audit")

# CDP 方法白名单（只允许安全的读取操作，禁止调试/修改/崩溃等危险操作）
CDP_WHITELIST = {
    # 页面快照
    "Page.captureScreenshot",
    "Page.printToPDF",
    "Page.getNavigationHistory",
    "Page.getFrameTree",

    # DOM 结构读取
    "DOM.getDocument",
    "DOM.getFlattenedDocument",
    "DOM.getBoxModel",
    "DOM.getNodeForLocation",
    "DOM.describeNode",
    "DOM.resolveNode",

    # 可访问性树
    "Accessibility.getFullAXTree",
    "Accessibility.getPartialAXTree",
    "Accessibility.getChildAXNodes",

    # 性能指标
    "Performance.getMetrics",

    # 网络信息（只读）
    "Network.getCookies",
    "Network.getAllCookies",

    # 存储信息（只读）
    "Storage.getCookies",

    # 系统信息
    "SystemInfo.getInfo",
    "SystemInfo.getProcessInfo",

    # 目标管理（只读）
    "Target.getTargets",
    "Target.getTargetInfo",

    # 浏览器信息
    "Browser.getVersion",
    "Browser.getWindowBounds",

    # 权限管理（剪贴板需要）
    "Browser.grantPermissions",
}


def build_misc(reg):
    async def browser_evaluate_js(ctx: ToolContext, p: dict) -> ToolResult:
        expression = require_str(p, "expression")

        # 安全机制：审计日志记录执行的 JS 代码（前 200 字符）
        preview = expression[:200] + ("..." if len(expression) > 200 else "")
        _audit_logger.info(f"[JS_EXECUTE] tab_id={ctx.tab_id} code={preview}")

        # 安全机制：超时保护（15 秒），防止恶意脚本长时间占用资源
        try:
            result = await asyncio.wait_for(
                ctx.page.evaluate(expression),
                timeout=15.0
            )
        except asyncio.TimeoutError:
            raise ToolError("JS 执行超时（15 秒），请检查代码是否有无限循环或长时间阻塞")
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
            # write：JS 可能带副作用（点击/提交），须持页级写锁且禁止 read 层自动重试，
            # 否则导航边界重试会让同一段脚本执行两次
            kind="write", handler=browser_evaluate_js)

    async def browser_cdp_call(ctx: ToolContext, p: dict) -> ToolResult:
        method = require_str(p, "method")
        params = p.get("params") or {}
        if not isinstance(params, dict):
            params = {}

        # 安全机制：CDP 方法白名单检查
        if method not in CDP_WHITELIST:
            _audit_logger.warning(f"[CDP_BLOCKED] tab_id={ctx.tab_id} method={method} reason=not_in_whitelist")
            raise ToolError(
                f"CDP 方法 '{method}' 不在白名单中，已被拒绝执行。\n"
                f"允许的方法包括：Page.captureScreenshot, DOM.getDocument, Accessibility.getFullAXTree 等安全读取操作。\n"
                f"禁止的方法包括：Runtime.*, Debugger.*, Browser.crash, Target.createTarget 等危险操作。"
            )

        # 安全机制：审计日志记录 CDP 调用
        params_preview = json.dumps(params, ensure_ascii=False)[:100]
        _audit_logger.info(f"[CDP_CALL] tab_id={ctx.tab_id} method={method} params={params_preview}")

        # Target.* / Browser.* 走浏览器级 session，其余走页级 session
        if method.startswith(("Target.", "Browser.", "SystemInfo.")):
            sess = await ctx.browser.browser_cdp()
        else:
            sess = await ctx.cdp()

        # 安全机制：超时保护（10 秒）
        try:
            result = await asyncio.wait_for(
                sess.send(method, params),
                timeout=10.0
            )
        except asyncio.TimeoutError:
            raise ToolError(f"CDP {method} 执行超时（10 秒）")
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
            # 安全机制：剪贴板读取超时保护（5 秒）
            try:
                text = await asyncio.wait_for(
                    ctx.page.evaluate("navigator.clipboard.readText()"),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                raise ToolError("剪贴板读取超时（5 秒），请检查浏览器权限")
            except Exception:
                text = ""
            return ToolResult.ok(text if text else "(empty)")

        text = require_str(p, "text")
        # 安全机制：审计日志记录剪贴板写入（前 100 字符）
        preview = text[:100] + ("..." if len(text) > 100 else "")
        _audit_logger.info(f"[CLIPBOARD_WRITE] tab_id={ctx.tab_id} text={preview}")

        # 安全机制：剪贴板写入超时保护（5 秒）
        try:
            await asyncio.wait_for(
                ctx.page.evaluate("(t) => navigator.clipboard.writeText(t)", text),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            raise ToolError("剪贴板写入超时（5 秒），请检查浏览器权限")
        except Exception as e:
            raise ToolError(f"写入剪贴板失败: {e}")
        return ToolResult.ok("已写入剪贴板")

    reg.add("browser_clipboard", "读写剪贴板",
            {"op": {"type": "string", "description": "read(默认)/write"},
             "text": {"type": "string", "description": "write 时要写入的文本"},
             "tab_id": {"type": "integer"}},
            # write：op=write 有系统级副作用，统一走写锁避免读分类下的重试副作用
            kind="write", handler=browser_clipboard)

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
