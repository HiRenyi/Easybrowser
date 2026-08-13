"""BrowserManager —— Playwright 托管的浏览器会话 + 多 tab 管理核心。

设计要点（根治原项目 tab 混乱问题）：
- tab_id 用自增整数 registry（非 index），杜绝 target 顺序变化导致的错乱
- 跨 context 自动登记 `target=_blank` 新开 tab，无需手动 switch_tab
- per-page 写锁串行化同页写操作；导航额外独立锁
- page close / browser disconnected 自动清理 registry，active 回退
- 每页缓存 CDPSession（供 cdp_call / snapshot_ax / targetId 复用）
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    CDPSession,
    Dialog,
    Download,
    Page,
    TimeoutError as PWTimeoutError,
    async_playwright,
)

from .config import Config
from .errors import BrowserNotConnected, TabBusy, TabError

log = logging.getLogger("easybrowser.browser")

# 导航/读操作遇到这些错误应重试（页面正在导航导致 execution context 失效）
RETRYABLE_READ_ERRORS = (
    "Execution context was destroyed",
    "Cannot find context with specified id",
    "Navigating to",
    "Cannot navigate to invalid URL",
    "Execution context is not available",
)


def _detect_browser_channel() -> str:
    """自动选择 Playwright 浏览器通道（免下载浏览器）：
    Windows → msedge（系统自带）；否则 → chrome。"""
    if sys.platform == "win32":
        return "msedge"
    if sys.platform == "darwin":
        # macOS 系统 Chrome 可通过 channel=chrome 用
        return "chrome"
    return ""


def _find_bundled_chromium() -> str:
    """PyInstaller 打包场景：优先用 exe 旁自带的 Chromium（完全自带浏览器，零系统依赖）。"""
    if not getattr(sys, "frozen", False):
        return ""
    base = Path(sys.executable).parent
    candidates = [
        base / "chromium" / "chrome-win" / "chrome.exe",          # Windows
        base / "chromium" / "chrome-linux" / "chrome",            # Linux
        base / "chromium" / "chrome-mac" / "Google Chrome for Testing.app"
              / "Contents" / "MacOS" / "Google Chrome for Testing",  # macOS
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return ""


def _find_system_chrome() -> str:
    """Linux 找系统 Chrome 可执行文件。"""
    if sys.platform != "linux":
        return ""
    for exe in ("/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
                "/usr/bin/chromium", "/usr/bin/chromium-browser",
                "/usr/bin/microsoft-edge"):
        if shutil.which(exe) or os.path.exists(exe):
            return exe
    return ""


class BrowserManager:
    """浏览器会话管理器：连接 / tab 注册 / 并发锁 / 页面事件。"""

    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._default_ctx: Optional[BrowserContext] = None

        self._pages: dict[int, Page] = {}          # tab_id -> Page
        self._page_to_tab: dict[int, int] = {}     # id(page) -> tab_id
        self._target_ids: dict[int, str] = {}      # tab_id -> CDP targetId
        self._next_tab_id = 1
        self._active_tab_id: Optional[int] = None

        self._locks: dict[int, asyncio.Lock] = {}          # per-page 写锁
        self._nav_locks: dict[int, asyncio.Lock] = {}      # per-page 导航锁
        self._cdp_sessions: dict[int, CDPSession] = {}     # per-page CDPSession
        self._console_buffers: dict[int, list[str]] = {}
        self._refmaps: dict[int, dict] = {}                # tab_id -> {ref: RefEntry}
        self._nodemaps: dict[int, dict] = {}               # tab_id -> {node_id: {x,y,tag,text}}
        self._closed = False
        self._session_name: str = ""

    # ------------------------------------------------------------------ 连接
    async def connect(self) -> None:
        self._playwright = await async_playwright().start()
        try:
            if self._cfg.mode == "cdp":
                await self._connect_cdp()
            elif self._cfg.mode == "launch":
                await self._connect_launch()
            else:
                raise ValueError(f"未知连接模式: {self._cfg.mode}")
        except Exception:
            await self._safe_stop()
            raise

    async def _connect_cdp(self) -> None:
        self._browser = await self._playwright.chromium.connect_over_cdp(self._cfg.cdp_url)
        if not self._browser.contexts:
            raise RuntimeError(
                "Chrome 没有可用 context。请先在 Chrome 打开一个普通网页（非 chrome:// 页）。"
            )
        self._default_ctx = self._browser.contexts[0]
        for ctx in self._browser.contexts:
            for page in ctx.pages:
                self._register(page)
            ctx.on("page", self._register)
        self._browser.on("disconnected", self._on_disconnected)
        log.info("cdp 连接成功: %s（已登记 %d 个标签页）", self._cfg.cdp_url, len(self._pages))

    async def _connect_launch(self) -> None:
        kwargs: dict = {
            "user_data_dir": self._cfg.profile_dir,
            "headless": self._cfg.headless,
        }
        if self._cfg.executable_path:
            kwargs["executable_path"] = self._cfg.executable_path
        else:
            # 浏览器探测顺序：exe 旁自带的 Chromium（PyInstaller 打包）→ 系统 Edge/Chrome
            bundled = _find_bundled_chromium()
            if bundled:
                kwargs["executable_path"] = bundled
            else:
                channel = _detect_browser_channel()
                if channel:
                    kwargs["channel"] = channel
                else:
                    exe = _find_system_chrome()
                    if exe:
                        kwargs["executable_path"] = exe
        ctx = await self._playwright.chromium.launch_persistent_context(**kwargs)
        self._default_ctx = ctx
        for page in ctx.pages:
            self._register(page)
        ctx.on("page", self._register)
        log.info("launch 模式启动成功: profile=%s (channel=%s, exe=%s)",
                 self._cfg.profile_dir, kwargs.get("channel"),
                 kwargs.get("executable_path"))

    async def _safe_stop(self) -> None:
        try:
            if self._playwright is not None:
                await self._playwright.stop()
        except Exception:
            pass

    @property
    def connected(self) -> bool:
        return not self._closed and self._default_ctx is not None

    def require_connected(self) -> None:
        if self._closed or self._default_ctx is None:
            raise BrowserNotConnected("浏览器未连接或已断开，请重启服务")

    async def close(self) -> None:
        self._closed = True
        try:
            if self._browser is not None:
                await self._browser.close()
            elif self._default_ctx is not None:
                await self._default_ctx.close()
        except Exception:
            pass
        await self._safe_stop()

    # ------------------------------------------------------------ tab 注册/注销
    def _register(self, page: Page) -> int:
        """登记一个 Page，返回其 tab_id。重复登记返回已有 id。"""
        existing = self._page_to_tab.get(id(page))
        if existing is not None:
            return existing
        tid = self._next_tab_id
        self._next_tab_id += 1
        self._pages[tid] = page
        self._page_to_tab[id(page)] = tid
        self._locks[tid] = asyncio.Lock()
        self._nav_locks[tid] = asyncio.Lock()
        if self._active_tab_id is None:
            self._active_tab_id = tid

        page.on("close", self._make_closer(tid))
        page.on("console", lambda m, _tid=tid: self._on_console(m, _tid))
        page.on("dialog", lambda d, _tid=tid: self._on_dialog(d, _tid))
        page.on("download", lambda d, _tid=tid: self._on_download(d, _tid))
        log.info("tab[%d] 登记: %s", tid, page.url[:80])
        return tid

    def _make_closer(self, tid: int):
        def closer(page: Page):
            self._unregister(tid)
        return closer

    def _unregister(self, tid: int) -> None:
        page = self._pages.pop(tid, None)
        if page is None:
            return
        self._page_to_tab.pop(id(page), None)
        self._target_ids.pop(tid, None)
        self._console_buffers.pop(tid, None)
        self._cdp_sessions.pop(tid, None)
        self._refmaps.pop(tid, None)
        self._nodemaps.pop(tid, None)
        self._locks.pop(tid, None)
        self._nav_locks.pop(tid, None)
        if self._active_tab_id == tid:
            self._active_tab_id = next(iter(self._pages), None)
        log.info("tab[%d] 注销，剩余 %d 个标签页", tid, len(self._pages))

    def _on_disconnected(self, *_) -> None:
        self._closed = True
        self._pages.clear()
        self._page_to_tab.clear()
        self._target_ids.clear()
        self._console_buffers.clear()
        self._cdp_sessions.clear()
        self._active_tab_id = None
        log.warning("浏览器连接已断开，registry 已清空")

    # ------------------------------------------------------------ 页面事件
    def _on_console(self, msg, tid: int) -> None:
        text = msg.text.strip()
        if not text:
            return
        buf = self._console_buffers.setdefault(tid, [])
        buf.append(text)
        if len(buf) > self._cfg.console_buf_limit:
            buf.pop(0)

    def _on_dialog(self, dialog: Dialog, tid: int) -> None:
        log.info("tab[%d] 自动关闭对话框: %s", tid, dialog.message[:80])
        asyncio.create_task(self._dismiss_dialog(dialog))

    async def _dismiss_dialog(self, dialog: Dialog) -> None:
        try:
            await dialog.dismiss()
        except Exception:
            pass

    def _on_download(self, download: Download, tid: int) -> None:
        log.info("tab[%d] 接受下载: %s", tid, download.suggested_filename)
        asyncio.create_task(self._save_download(download, tid))

    async def _save_download(self, download: Download, tid: int) -> None:
        try:
            path = await download.path()
            log.info("tab[%d] 下载已保存: %s", tid, path)
        except Exception:
            log.warning("tab[%d] 下载失败: %s", tid, download.suggested_filename)

    # ------------------------------------------------------------ CDP session
    async def cdp(self, tab_id: int) -> CDPSession:
        """获取某标签页的 CDPSession（惰性创建并缓存）。"""
        sess = self._cdp_sessions.get(tab_id)
        if sess is None:
            page = self.get_page(tab_id)
            sess = await self._default_ctx.new_cdp_session(page)
            self._cdp_sessions[tab_id] = sess
        return sess

    async def browser_cdp(self) -> CDPSession:
        """获取浏览器级 CDPSession（用于 Target.* / Browser.* 命令）。"""
        return await self._default_ctx.new_cdp_session(self._browser) \
            if self._browser is not None \
            else await self._default_ctx.new_cdp_session(self._default_ctx)

    async def target_id(self, tab_id: int) -> str:
        tid = self._target_ids.get(tab_id)
        if tid:
            return tid
        try:
            sess = await self.cdp(tab_id)
            info = await sess.send("Target.getTargetInfo")
            tid = info["targetInfo"]["targetId"]
            self._target_ids[tab_id] = tid
        except Exception:
            tid = ""
        return tid

    # ------------------------------------------------------------ tab 查询/操作
    def get_page(self, tab_id: Optional[int] = None) -> Page:
        self.require_connected()
        if tab_id is not None:
            page = self._pages.get(tab_id)
            if page is None:
                raise TabError(f"tab_id={tab_id} 不存在（用 browser_list_tabs 查看当前标签页）")
            return page
        if self._active_tab_id is None or self._active_tab_id not in self._pages:
            if not self._pages:
                raise TabError("当前没有打开的标签页（先 browser_new_tab 或打开网页）")
            self._active_tab_id = next(iter(self._pages))
        return self._pages[self._active_tab_id]

    def resolve_tab_id(self, tab_id: Optional[int]) -> int:
        """把可选 tab_id 解析为实际 tab_id（默认 active）。"""
        if tab_id is not None:
            self.get_page(tab_id)  # 校验存在
            return tab_id
        return self._active_tab_id if self._active_tab_id in self._pages else next(iter(self._pages))

    async def list_tabs(self) -> list[dict]:
        self.require_connected()
        out = []
        for tid, page in self._pages.items():
            try:
                title = await page.title()
            except Exception:
                title = ""
            out.append({
                "tab_id": tid,
                "target_id": await self.target_id(tid),
                "url": page.url,
                "title": title,
                "active": tid == self._active_tab_id,
                "window_id": None,
                "status": "complete",
            })
        return out

    async def new_tab(self, url: Optional[str] = None) -> dict:
        """新开标签页并自动设为 active（根治原版 new_tab 后需手动 switch 的问题）。"""
        self.require_connected()
        page = await self._default_ctx.new_page()
        tid = self._register(page)
        if url:
            try:
                await page.goto(url, wait_until="load", timeout=self._cfg.nav_timeout_ms)
            except PWTimeoutError:
                log.warning("tab[%d] 导航超时但仍继续: %s", tid, url)
        self._active_tab_id = tid
        info = await self.list_tabs()
        return next(t for t in info if t["tab_id"] == tid)

    async def select_tab(self, tab_id: int, focus: bool = False) -> None:
        """把某 tab 设为 active 控制目标。focus=True 同时前台激活。"""
        page = self.get_page(tab_id)
        if focus:
            try:
                await page.bring_to_front()
            except Exception as e:
                log.warning("bring_to_front 失败: %s", e)
        self._active_tab_id = tab_id

    async def close_tab(self, tab_id: int) -> None:
        """关闭标签页（Playwright 正确处理，无 broken pipe）。"""
        page = self.get_page(tab_id)
        await page.close()

    # ------------------------------------------------------------ 并发锁
    @asynccontextmanager
    async def write_lock(self, tab_id: Optional[int] = None):
        """per-page 写锁：同一标签页的写操作（click/fill/navigate）串行。"""
        tid = self.resolve_tab_id(tab_id)
        lock = self._locks.get(tid)
        if lock is None:
            raise TabError(f"tab_id={tid} 不存在")
        try:
            await asyncio.wait_for(lock.acquire(), timeout=self._cfg.lock_timeout_ms / 1000)
        except asyncio.TimeoutError:
            raise TabBusy(f"页面 tab_id={tid} 忙，等待写锁超时（{self._cfg.lock_timeout_ms}ms）")
        try:
            yield tid
        finally:
            lock.release()

    @asynccontextmanager
    async def nav_lock(self, tab_id: Optional[int] = None):
        """per-page 导航锁：导航操作额外串行，避免与读操作撞 navigation。"""
        tid = self.resolve_tab_id(tab_id)
        lock = self._nav_locks.get(tid)
        if lock is None:
            raise TabError(f"tab_id={tid} 不存在")
        try:
            await asyncio.wait_for(lock.acquire(), timeout=self._cfg.lock_timeout_ms / 1000)
        except asyncio.TimeoutError:
            raise TabBusy(f"页面 tab_id={tid} 正导航，等待超时")
        try:
            yield tid
        finally:
            lock.release()

    def session_name(self) -> str:
        return self._session_name

    def set_session_name(self, name: str) -> None:
        self._session_name = name

    # ------------------------------------------------------------ ref 映射
    def set_refmap(self, tab_id: int, refmap: dict) -> None:
        self._refmaps[tab_id] = refmap

    def get_refmap(self, tab_id: int) -> dict:
        return self._refmaps.get(tab_id, {})

    def clear_refmap(self, tab_id: int) -> None:
        self._refmaps.pop(tab_id, None)

    def set_nodemap(self, tab_id: int, nodemap: dict) -> None:
        self._nodemaps[tab_id] = nodemap

    def get_nodemap(self, tab_id: int) -> dict:
        return self._nodemaps.get(tab_id, {})

    def clear_nodemap(self, tab_id: int) -> None:
        self._nodemaps.pop(tab_id, None)
