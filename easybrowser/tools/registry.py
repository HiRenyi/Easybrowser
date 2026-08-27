"""工具注册框架 —— ToolContext / ToolResult / 注册表 / 读写锁执行器。

统一工具签名: async handler(ctx: ToolContext, params: dict) -> ToolResult
工具按 kind 分类执行:
- read  : 只读，不加锁，遇导航边界自动重试
- write : 写操作，per-page 写锁串行
- nav   : 导航操作，写锁 + 导航独立锁
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from playwright.async_api import Page

from ..browser import RETRYABLE_READ_ERRORS, BrowserManager
from ..errors import EasyBrowserError, TabBusy, ToolError

log = logging.getLogger("easybrowser.registry")

#: 读操作重试次数（应对导航边界 execution context 失效）
READ_RETRIES = 3


@dataclass
class ToolResult:
    text: str = ""
    is_error: bool = False
    #: 结构化数据（screenshot base64 等），随响应一并返回
    data: dict | None = None

    @classmethod
    def ok(cls, text: str = "", data: dict | None = None) -> "ToolResult":
        return cls(text=text, data=data)

    @classmethod
    def err(cls, message: str) -> "ToolResult":
        return cls(text=message, is_error=True)


@dataclass
class ToolContext:
    """工具执行上下文。"""

    browser: BrowserManager
    tab_id: int
    page: Page
    params: dict

    async def cdp(self):
        """当前标签页的 CDPSession（惰性创建）。"""
        return await self.browser.cdp(self.tab_id)

    @property
    def cfg(self):
        return self._cfg if (_cfg := getattr(self, "_cfg", None)) is not None else self.browser._cfg


@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict
    kind: str  # read | write | nav
    handler: Callable[[ToolContext, dict], Awaitable[ToolResult]]
    params: dict = field(default_factory=dict)  # 本次调用的原始参数


class ToolRegistry:
    def __init__(self, browser: BrowserManager):
        self._browser = browser
        self._tools: dict[str, ToolDef] = {}
        self._order: list[str] = []

    # ------------------------------------------------------------- 注册
    def add(self, name: str, description: str, schema: dict,
            kind: str = "read", handler=None):
        if handler is None:
            raise ValueError(f"工具 {name} 缺少 handler")
        self._tools[name] = ToolDef(name, description, schema, kind, handler)
        self._order.append(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> ToolDef:
        return self._tools[name]

    def all(self) -> list[ToolDef]:
        return [self._tools[n] for n in self._order]

    def spec(self) -> list[dict]:
        out = []
        for n in self._order:
            t = self._tools[n]
            out.append({"name": t.name, "description": t.description,
                        "input_schema": t.input_schema, "kind": t.kind})
        return out

    # ------------------------------------------------------------- 执行
    async def run(self, name: str, params: dict) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.err(f"未知工具: {name}（GET /api/tools 查看列表）")

        tab_id = params.get("tab_id")

        try:
            if tool.kind in ("write", "nav"):
                # 总超时须覆盖最坏路径：write+nav 两层锁排队 + 导航 load 失败退
                # domcontentloaded 的双次 goto（此前写死 30s < 导航预算，导致把
                # 元素/导航超时误报成"锁等待超时请重启服务"）
                cfg = self._browser._cfg
                total_s = (cfg.lock_timeout_ms * 2
                           + cfg.nav_timeout_ms * 2) / 1000
                try:
                    async with asyncio.timeout(total_s):
                        async with self._browser.write_lock(tab_id) as tid:
                            if tool.kind == "nav":
                                async with self._browser.nav_lock(tid):
                                    return await self._execute(tool, tid, params)
                            return await self._execute(tool, tid, params)
                except asyncio.TimeoutError:
                    raise ToolError(f'操作总超时（{int(total_s)}秒，含锁等待）。'
                                    f'若该页面持续无响应可重启服务后重试')
            else:
                tid = self._browser.resolve_tab_id(tab_id)
                return await self._execute_read(tool, tid, params)
        except TabBusy as e:
            return ToolResult.err(str(e))
        except EasyBrowserError as e:
            return ToolResult.err(str(e))
        except Exception as e:
            log.exception("工具 %s 未预期异常", name)
            return ToolResult.err(f"{name} 内部错误: {e}")

    async def _execute(self, tool: ToolDef, tid: int, params: dict) -> ToolResult:
        page = self._browser.get_page(tid)
        ctx = ToolContext(browser=self._browser, tab_id=tid, page=page, params=params)
        return await tool.handler(ctx, params)

    async def _execute_read(self, tool: ToolDef, tid: int, params: dict) -> ToolResult:
        """只读工具：导航边界自动重试。"""
        last_err: Optional[Exception] = None
        for _ in range(READ_RETRIES):
            try:
                page = self._browser.get_page(tid)
                ctx = ToolContext(browser=self._browser, tab_id=tid, page=page, params=params)
                return await tool.handler(ctx, params)
            except EasyBrowserError:
                raise
            except Exception as e:
                last_err = e
                msg = str(e)
                if not any(r in msg for r in RETRYABLE_READ_ERRORS):
                    raise
                log.info("只读工具 %s 遇导航边界，重试: %s", tool.name, msg[:100])
                await asyncio.sleep(0.3)
        raise last_err or RuntimeError("read retry exhausted")
