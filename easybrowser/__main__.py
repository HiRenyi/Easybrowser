"""EasyBrowser CLI 入口：python -m easybrowser [serve|run] [options]

子命令：
  serve                 启动 HTTP API 服务（默认，常驻）
  run <script.json>     一次性执行录制脚本后退出（cron 友好，exit code 反映成败）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import socket
import sys

import uvicorn


def find_free_port(start: int, host: str = "127.0.0.1", max_try: int = 20) -> int:
    """从 start 开始探测空闲端口（被占自动 +1）。"""
    for port in range(start, start + max_try):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    return start


def _base_parser(cfg):
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--mode", choices=["cdp", "launch"], default=cfg.mode,
                   help="cdp=连接已启动 Chrome（默认）；launch=自启动浏览器")
    p.add_argument("--cdp-url", default=cfg.cdp_url,
                   help="cdp 模式 Chrome 调试地址（默认 http://127.0.0.1:9222）")
    p.add_argument("--profile-dir", default=cfg.profile_dir,
                   help="launch 模式用户数据目录（复用登录态）")
    p.add_argument("--executable-path", default=None,
                   help="launch 模式 Chrome 可执行文件路径")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--log-level", default="INFO")
    return p


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    from easybrowser.config import Config

    cfg = Config().env_overrides()

    # 顶层：识别子命令
    if argv and argv[0] == "run":
        return _run_script(argv[1:], cfg)
    if argv and argv[0] == "serve":
        argv = argv[1:]
    return _serve(argv, cfg)


def _serve(argv, cfg):
    parser = argparse.ArgumentParser(prog="easybrowser",
                                     description="浏览器自动化本地 HTTP API 服务")
    parser.add_argument("--host", default=cfg.host)
    parser.add_argument("--port", type=int, default=cfg.port)
    parser.add_argument("--log-level", default="INFO")
    _base_parser(cfg)  # 复用公共参数（避免重复声明）
    # 手动合并公共参数
    args = _parse_serve_args(parser, argv, cfg)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    final_port = find_free_port(args.port, args.host)
    if final_port != args.port:
        print(f"端口 {args.port} 被占用，改用 {final_port}", file=sys.stderr)
    args.port = final_port

    print(f"EasyBrowser 启动中: mode={args.mode} port={args.port} "
          f"cdp={args.cdp_url if args.mode == 'cdp' else args.profile_dir}")
    from easybrowser.api import create_app
    app = create_app(_cfg_from_args(cfg, args))
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())
    return 0


def _parse_serve_args(parser, argv, cfg):
    # 简单起见：解析已知参数，未知忽略
    known = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--host" and i + 1 < len(argv):
            known["host"], i = argv[i + 1], i + 2
        elif a == "--port" and i + 1 < len(argv):
            known["port"], i = int(argv[i + 1]), i + 2
        elif a == "--mode" and i + 1 < len(argv):
            known["mode"], i = argv[i + 1], i + 2
        elif a == "--cdp-url" and i + 1 < len(argv):
            known["cdp_url"], i = argv[i + 1], i + 2
        elif a == "--profile-dir" and i + 1 < len(argv):
            known["profile_dir"], i = argv[i + 1], i + 2
        elif a == "--executable-path" and i + 1 < len(argv):
            known["executable_path"], i = argv[i + 1], i + 2
        elif a == "--log-level" and i + 1 < len(argv):
            known["log_level"], i = argv[i + 1], i + 2
        elif a == "--headless":
            known["headless"], i = True, i + 1
        else:
            i += 1
    return argparse.Namespace(host=known.get("host", cfg.host),
                              port=known.get("port", cfg.port),
                              mode=known.get("mode", cfg.mode),
                              cdp_url=known.get("cdp_url", cfg.cdp_url),
                              profile_dir=known.get("profile_dir", cfg.profile_dir),
                              executable_path=known.get("executable_path"),
                              headless=known.get("headless", False),
                              log_level=known.get("log_level", "INFO"))


def _cfg_from_args(cfg, args):
    cfg.cli_overrides({
        "host": args.host, "port": args.port, "mode": args.mode,
        "cdp_url": args.cdp_url, "profile_dir": args.profile_dir,
        "executable_path": args.executable_path, "headless": args.headless,
    })
    return cfg


def _run_script(argv, cfg):
    """easybrowser run <script.json> [--mode cdp|launch] [--headless]"""
    if not argv or argv[0].startswith("-"):
        print("用法: easybrowser run <script.json> [--mode cdp|launch] [--headless]",
              file=sys.stderr)
        return 2
    script_path = argv[0]
    parser = argparse.ArgumentParser(prog="easybrowser run")
    _base_parser(cfg)
    args = _parse_serve_args(parser, argv[1:], cfg)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        with open(script_path, encoding="utf-8") as f:
            script = json.load(f)
    except Exception as e:
        print(f"读取脚本失败: {e}", file=sys.stderr)
        return 2
    if not isinstance(script, dict) or not isinstance(script.get("steps"), list):
        print("脚本格式错误：需要 {version, steps:[{tool,params}]}", file=sys.stderr)
        return 2

    final_cfg = _cfg_from_args(cfg, args)

    async def _go() -> int:
        from easybrowser.browser import BrowserManager
        from easybrowser.recorder import RecorderManager
        from easybrowser.tools import build_registry
        bm = BrowserManager(final_cfg)
        await bm.connect()
        try:
            reg = build_registry(bm)
            rm = RecorderManager(bm, reg)
            result = await rm.replay(script)
            print(json.dumps({"ok": result["ok"], "executed": result["executed"],
                              "results": result["results"]}, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 1
        finally:
            await bm.close()

    return asyncio.run(_go())


if __name__ == "__main__":
    sys.exit(main())
