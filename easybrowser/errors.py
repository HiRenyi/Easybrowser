"""EasyBrowser 异常定义。"""


class EasyBrowserError(Exception):
    """服务内部错误基类。"""


class TabError(EasyBrowserError):
    """标签页不存在 / 没有可用标签页。"""


class TabBusy(EasyBrowserError):
    """目标标签页忙（写锁排队超时）。"""


class BrowserNotConnected(EasyBrowserError):
    """浏览器未连接或已断开。"""


class ToolError(EasyBrowserError):
    """工具执行错误（参数错误 / 元素未找到 / 执行失败）。"""
