"""工具注册入口：build_registry(browser) 组装全部 52 个工具。"""
from __future__ import annotations

from ..browser import BrowserManager
from .registry import ToolRegistry


def build_registry(browser: BrowserManager) -> ToolRegistry:
    reg = ToolRegistry(browser)
    from .perception import build_perception
    from .interact import build_interact
    from .navigation import build_navigation
    from .tabs import build_tabs
    from .misc import build_misc
    build_perception(reg)     # 感知层 11
    build_interact(reg)       # 操作层 20（DOM + 坐标 + JS）
    build_navigation(reg)     # 导航层 5 + 等待层 3
    build_tabs(reg)           # Tab 管理 5
    build_misc(reg)           # 其他 7
    return reg
