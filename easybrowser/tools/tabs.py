"""Tab 管理（5 个）工具。"""
from __future__ import annotations

from ..errors import ToolError
from ._util import get_bool, require_int
from .registry import ToolContext, ToolResult


def build_tabs(reg):
    async def browser_list_tabs(ctx: ToolContext, p: dict) -> ToolResult:
        tabs = await ctx.browser.list_tabs()
        if not tabs:
            return ToolResult.ok("0 个标签页")
        lines = [f"{len(tabs)} 个标签页:"]
        for t in tabs:
            mark = "*" if t["active"] else " "
            lines.append(
                f"{mark} [{t['tab_id']}] id={t['tab_id']} target={t['target_id'][:8]} "
                f"url={t['url']} title={t['title']!r} {t['status']}"
            )
        return ToolResult.ok("\n".join(lines))

    reg.add("browser_list_tabs", "列出所有打开标签页（tab_id / target_id / url / title / active）",
            {"tab_id": {"type": "integer"}}, kind="read", handler=browser_list_tabs)

    async def browser_switch_tab(ctx: ToolContext, p: dict) -> ToolResult:
        tid = require_int(p, "tabId")
        await ctx.browser.select_tab(tid, focus=True)
        page = ctx.browser.get_page(tid)
        return ToolResult.ok(f"已切换并激活 tab_id={tid}: {page.url}（可 browser_snapshot 确认）")

    reg.add("browser_switch_tab", "切换并前台激活指定标签页（重定向后续操作到该页）",
            {"tabId": {"type": "integer", "description": "browser_list_tabs 返回的 tab_id"}},
            kind="write", handler=browser_switch_tab)

    async def browser_select_tab(ctx: ToolContext, p: dict) -> ToolResult:
        tid = require_int(p, "tabId")
        await ctx.browser.select_tab(tid, focus=False)
        page = ctx.browser.get_page(tid)
        return ToolResult.ok(f"已选中 tab_id={tid} 作为操作目标（不抢焦点）: {page.url}")

    reg.add("browser_select_tab", "选中指定标签页作为后续操作目标（不抢焦点）",
            {"tabId": {"type": "integer", "description": "browser_list_tabs 返回的 tab_id"}},
            kind="write", handler=browser_select_tab)

    async def browser_adopt_tab(ctx: ToolContext, p: dict) -> ToolResult:
        # Playwright 已自动登记所有 page；adopt = 确认并选中。
        tid = require_int(p, "tabId")
        await ctx.browser.select_tab(tid, focus=False)
        page = ctx.browser.get_page(tid)
        return ToolResult.ok(f"已接管 tab_id={tid} 为操作目标: {page.url}")

    reg.add("browser_adopt_tab", "接管现有标签页为 AI 操作目标（Playwright 已自动登记，此工具等价选中）",
            {"tabId": {"type": "integer", "description": "browser_list_tabs 返回的 tab_id"}},
            kind="write", handler=browser_adopt_tab)

    async def browser_close_tab(ctx: ToolContext, p: dict) -> ToolResult:
        tid = require_int(p, "tabId") if "tabId" in p else ctx.tab_id
        await ctx.browser.close_tab(tid)
        return ToolResult.ok(f"已关闭 tab_id={tid}")

    reg.add("browser_close_tab", "关闭指定标签页（默认关闭当前活动页）",
            {"tabId": {"type": "integer", "description": "可选，要关闭的 tab_id，缺省关闭当前页"}},
            kind="write", handler=browser_close_tab)
