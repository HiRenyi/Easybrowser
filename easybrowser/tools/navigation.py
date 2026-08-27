"""导航层（5）+ 等待层（3）工具。"""
from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlparse

from playwright.async_api import TimeoutError as PWTimeoutError

from ..errors import ToolError
from ._util import get_bool, get_int, get_str, require_str, resolve_locator
from .registry import ToolContext, ToolResult

logger = logging.getLogger(__name__)

# URL 安全白名单和黑名单
ALLOWED_PROTOCOLS = {"http", "https", "about", "data", "file"}
BLOCKED_PROTOCOLS = {"javascript", "vbscript", "ws", "wss"}


def _validate_url(url: str) -> None:
    """验证 URL 协议安全性。

    - 白名单：http, https, about
    - 黑名单：file, javascript, data, vbscript, ws, wss
    - 本地地址（127.0.0.1, localhost）记录警告日志

    Args:
        url: 待验证的 URL

    Raises:
        ToolError: 协议不在白名单或在黑名单时
    """
    parsed = urlparse(url)
    protocol = parsed.scheme.lower() if parsed.scheme else ""

    # 拒绝空协议
    if not protocol:
        raise ToolError("不允许的 URL 协议: (空)（必须使用 http:// 或 https://）")

    # 检查黑名单协议（优先级高）
    if protocol in BLOCKED_PROTOCOLS:
        raise ToolError(f"不允许的 URL 协议: {protocol}://（安全限制）")

    # 检查白名单协议（兜底）
    if protocol not in ALLOWED_PROTOCOLS:
        raise ToolError(f"不允许的 URL 协议: {protocol}://（仅允许 http/https/about）")

    # 本地地址警告
    hostname = parsed.hostname or ""
    if hostname in ("127.0.0.1", "localhost", "::1") or hostname.startswith("127."):
        logger.warning(f"导航到本地地址: {url}")


def build_navigation(reg):
    # ---------------------------------------------------------------- 导航
    async def browser_navigate(ctx: ToolContext, p: dict) -> ToolResult:
        url = require_str(p, "url")
        _validate_url(url)  # 安全验证
        timeout = get_int(p, "timeout", ctx.cfg.nav_timeout_ms)
        try:
            resp = await ctx.page.goto(url, wait_until="load", timeout=timeout)
        except PWTimeoutError:
            # load 可能永不触发（长轮询/流式页面），退回 domcontentloaded 结果仍可用
            try:
                resp = await ctx.page.goto(url, wait_until="domcontentloaded",
                                           timeout=timeout)
            except PWTimeoutError:
                raise ToolError(f"导航超时（{timeout}ms）: {url}")
        status = resp.status if resp is not None else None
        return ToolResult.ok(f"已导航到 {ctx.page.url}" + (f"（HTTP {status}）" if status else ""))

    reg.add("browser_navigate", "跳转到指定 URL（自动等待页面加载完成）",
            {"url": {"type": "string", "description": "目标地址"},
             "timeout": {"type": "integer", "description": "超时毫秒（默认 30000）"},
             "tab_id": {"type": "integer"}},
            kind="nav", handler=browser_navigate)

    async def browser_go_back(ctx: ToolContext, p: dict) -> ToolResult:
        try:
            resp = await ctx.page.go_back(wait_until="load", timeout=ctx.cfg.nav_timeout_ms)
        except PWTimeoutError:
            await ctx.page.go_back(wait_until="domcontentloaded", timeout=ctx.cfg.nav_timeout_ms)
            resp = None
        return ToolResult.ok(f"已后退到 {ctx.page.url}")

    reg.add("browser_go_back", "浏览器后退",
            {"tab_id": {"type": "integer"}}, kind="nav", handler=browser_go_back)

    async def browser_go_forward(ctx: ToolContext, p: dict) -> ToolResult:
        try:
            resp = await ctx.page.go_forward(wait_until="load", timeout=ctx.cfg.nav_timeout_ms)
        except PWTimeoutError:
            await ctx.page.go_forward(wait_until="domcontentloaded", timeout=ctx.cfg.nav_timeout_ms)
            resp = None
        return ToolResult.ok(f"已前进到 {ctx.page.url}")

    reg.add("browser_go_forward", "浏览器前进",
            {"tab_id": {"type": "integer"}}, kind="nav", handler=browser_go_forward)

    async def browser_reload(ctx: ToolContext, p: dict) -> ToolResult:
        try:
            await ctx.page.reload(wait_until="load", timeout=ctx.cfg.nav_timeout_ms)
        except PWTimeoutError:
            await ctx.page.reload(wait_until="domcontentloaded", timeout=ctx.cfg.nav_timeout_ms)
        return ToolResult.ok(f"已刷新: {ctx.page.url}")

    reg.add("browser_reload", "刷新当前页面",
            {"tab_id": {"type": "integer"}}, kind="nav", handler=browser_reload)

    async def browser_new_tab(ctx: ToolContext, p: dict) -> ToolResult:
        url = p.get("url") or ""
        if url:
            _validate_url(url)  # 安全验证
        info = await ctx.browser.new_tab(url if url else None)
        # 自动设为 active，无需手动 switch_tab（根治原版 bug）
        return ToolResult.ok(f"已打开新标签页 tab_id={info['tab_id']}: {info['url']}")

    reg.add("browser_new_tab", "新开标签页（自动设为活动页，无需再手动切换）",
            {"url": {"type": "string", "description": "可选，要打开的地址"},
             "force": {"type": "boolean", "description": "兼容参数，忽略"}},
            kind="nav", handler=browser_new_tab)

    # ---------------------------------------------------------------- 等待
    async def browser_wait_for_element(ctx: ToolContext, p: dict) -> ToolResult:
        state = get_str(p, "state", "visible").lower()
        timeout = get_int(p, "timeout", 10000)
        if state not in ("visible", "hidden", "attached", "detached"):
            raise ToolError(f"state 必须是 visible/hidden/attached/detached，得到 {state}")
        desc, loc = await resolve_locator(ctx, p)
        try:
            await loc.wait_for(state=state, timeout=timeout)
        except PWTimeoutError:
            raise ToolError(f"等待元素超时（{timeout}ms, state={state}）: {desc}")
        return ToolResult.ok(f"元素已满足 {state}（{desc}）")

    reg.add("browser_wait_for_element",
            "等待元素出现/消失（selector 或 ref）。state: visible/hidden/attached/detached",
            {"selector": {"type": "string", "description": "CSS 选择器"},
             "ref": {"type": "string", "description": "snapshot_ax 的 ref"},
             "state": {"type": "string", "description": "visible(默认)/hidden/attached/detached"},
             "timeout": {"type": "integer", "description": "超时毫秒（默认 10000）"},
             "tab_id": {"type": "integer"}},
            kind="read", handler=browser_wait_for_element)

    async def browser_wait_for_url(ctx: ToolContext, p: dict) -> ToolResult:
        url = require_str(p, "url")
        timeout = get_int(p, "timeout", 15000)
        mode = get_str(p, "match", "contains")  # contains | exact | glob
        try:
            await ctx.page.wait_for_url(_url_pattern(url, mode), timeout=timeout)
        except PWTimeoutError:
            raise ToolError(f"等待 URL 超时（{timeout}ms, match={mode}）: {url}（当前 {ctx.page.url}）")
        return ToolResult.ok(f"URL 已匹配: {ctx.page.url}")

    reg.add("browser_wait_for_url", "等待 URL 匹配指定模式",
            {"url": {"type": "string", "description": "目标 URL 或匹配模式"},
             "match": {"type": "string", "description": "contains(默认)/exact/glob"},
             "timeout": {"type": "integer", "description": "超时毫秒（默认 15000）"},
             "tab_id": {"type": "integer"}},
            kind="read", handler=browser_wait_for_url)

    async def browser_wait_for_timeout(ctx: ToolContext, p: dict) -> ToolResult:
        ms = get_int(p, "ms", 1000)
        await asyncio.sleep(ms / 1000)
        return ToolResult.ok(f"已等待 {ms}ms")

    reg.add("browser_wait_for_timeout", "固定等待指定毫秒",
            {"ms": {"type": "integer", "description": "等待毫秒（默认 1000）"},
             "tab_id": {"type": "integer"}},
            kind="read", handler=browser_wait_for_timeout)


def _url_pattern(url: str, mode: str) -> str | re.Pattern:
    if mode == "exact":
        return url
    if mode == "glob":
        # 用户给 * 通配，否则包成含通配的匹配
        return url if "*" in url else f"*{url}*"
    # contains: 转义为 glob 前缀
    return f"*{url}*"
