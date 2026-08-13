#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 Chrome 9222 的登录态 cookies 复制到 9223（独立新 Chrome）。

走 page target 的 WebSocket（9222 的 browser 调试口被旧 easybrowser 占用，
不能 connect_over_cdp 整个 browser，但 page 级 WebSocket 可正常调用
Network.getAllCookies / Network.setCookies）。

用法:
  python scripts/copy_cookies.py [--src 9222] [--dst 9223] [--verbose]

验证:
  # 复制后用 easybrowser(58086) 打开 OA，看是否免登录
  curl -X POST http://127.0.0.1:58086/api/tool/browser_navigate -d '{"url":"http://oa.company.com"}'
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

from websocket import create_connection


def get_page_ws(port: int) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5) as resp:
        targets = json.load(resp)
    for t in targets:
        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
            return t["webSocketDebuggerUrl"]
    raise SystemExit(f"端口 {port} 没有可用的 page target（请先在该 Chrome 打开一个普通网页）")


def send(ws, method: str, params: dict) -> tuple[dict, dict]:
    ws.send(json.dumps({"id": 1, "method": method, "params": params}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == 1:
            return msg.get("result", {}), msg.get("error") or {}


def copy_cookies(src_port: int, dst_port: int, verbose: bool = False) -> int:
    print(f"连接源 Chrome 922{src_port - 9220}…")
    src_ws = create_connection(get_page_ws(src_port), timeout=15)
    print(f"连接目标 Chrome 922{dst_port - 9220}…")
    dst_ws = create_connection(get_page_ws(dst_port), timeout=15)

    try:
        result, err = send(src_ws, "Network.getAllCookies", {})
        if err:
            raise SystemExit(f"读取源 cookies 失败: {err}")
        cookies = result.get("cookies", [])
        print(f"读取到 {len(cookies)} 条 cookie")

        if not cookies:
            return 1

        # 过滤明显无用的（partitionKey 分区 cookie 无法直接 set，跳过避免报错）
        migratable = []
        skipped = 0
        for c in cookies:
            if c.get("partitionKey"):
                skipped += 1
                continue
            migratable.append(c)

        # setCookies 分批（Chrome 单次限制约 600 条）
        set_ok = 0
        for i in range(0, len(migratable), 500):
            batch = migratable[i:i + 500]
            _, err2 = send(dst_ws, "Network.setCookies", {"cookies": batch})
            if err2:
                # 逐条失败会中断整批，退化为逐条
                for c in batch:
                    _, e3 = send(dst_ws, "Network.setCookies", {"cookies": [c]})
                    if not e3:
                        set_ok += 1
            else:
                set_ok += len(batch)

        print(f"写入目标 {set_ok} 条 cookie" + (f"（跳过 {skipped} 条分区 cookie）" if skipped else ""))
        print("完成。可到 9223 打开 OA 验证登录态。")

        if verbose:
            domains = sorted({c["domain"] for c in migratable})
            print("覆盖域名:", ", ".join(domains[:30]) + ("…" if len(domains) > 30 else ""))
        return 0
    finally:
        try:
            src_ws.close()
        except Exception:
            pass
        try:
            dst_ws.close()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="复制 Chrome 登录态 cookies")
    parser.add_argument("--src", type=int, default=9222, help="源调试口（默认 9222）")
    parser.add_argument("--dst", type=int, default=9223, help="目标调试口（默认 9223）")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    sys.exit(copy_cookies(args.src, args.dst, args.verbose))


if __name__ == "__main__":
    main()
