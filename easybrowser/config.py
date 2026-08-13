"""EasyBrowser 配置。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_data_dir() -> Path:
    base = os.environ.get("EASY_BROWSER_DATA_DIR")
    if base:
        return Path(base)
    return Path.home() / ".easybrowser"


@dataclass
class Config:
    """服务配置。所有字段均可被 CLI 参数 / 环境变量覆盖。"""

    host: str = "127.0.0.1"
    #: HTTP API 监听端口。避开 MCP 三环境家族（58080 test / 58081 prod / 58082 dev）
    #: 及原 easybrowser 用的 58081。启动时若被占用会自动 +1 找空闲端口。
    port: int = 58086

    #: 连接模式：cdp = 连接已启动的 Chrome（--remote-debugging-port），launch = 自启动浏览器
    mode: str = "cdp"
    #: cdp 模式下 Chrome 调试地址
    cdp_url: str = "http://127.0.0.1:9222"
    #: launch 模式下使用的用户数据目录（复用登录态）
    profile_dir: str = str(_default_data_dir() / "profile")
    #: launch 模式下 Chrome 可执行文件路径（默认自动探测）
    executable_path: str | None = None
    headless: bool = False

    #: 单次动作超时（点击/输入/等待等），毫秒
    action_timeout_ms: int = 15000
    #: 导航超时，毫秒
    nav_timeout_ms: int = 30000
    #: 写锁排队超时，毫秒（防并发积压）
    lock_timeout_ms: int = 15000
    #: console 日志缓冲上限（条）
    console_buf_limit: int = 500

    def cli_overrides(self, args: dict) -> "Config":
        """用 CLI 解析结果覆盖字段。"""
        for k, v in args.items():
            if v is not None and hasattr(self, k):
                setattr(self, k, v)
        return self

    def env_overrides(self) -> "Config":
        env = {
            "host": os.environ.get("EASY_BROWSER_HOST"),
            "port": os.environ.get("EASY_BROWSER_PORT"),
            "mode": os.environ.get("EASY_BROWSER_MODE"),
            "cdp_url": os.environ.get("EASY_BROWSER_CDP_URL"),
            "profile_dir": os.environ.get("EASY_BROWSER_PROFILE_DIR"),
        }
        for k, v in env.items():
            if v is None:
                continue
            if k == "port":
                setattr(self, k, int(v))
            else:
                setattr(self, k, v)
        return self
