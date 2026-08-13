#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""收集中国妇女报最近一个月的微博（滚动加载 + 去重），存为 JSON。

用法:
  python scripts/collect_weibo.py [--max-scroll 100] [--days 30]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta

import httpx

BASE = "http://127.0.0.1:58086"
EXTRACT_JS = """() => Array.from(document.querySelectorAll('.wbpro-scroller-item')).map(x => {
  const t = (x.innerText || '').replace(/\\s+/g, ' ').trim();
  return t.slice(0, 800);
})"""


def tool(client, name: str, params: dict) -> dict:
    r = client.post(f"{BASE}/api/tool/{name}", json=params, timeout=90)
    return r.json()


TIME_RE = re.compile(r"(\d+)分钟前|(\d+)小时前|昨天|前天|(\d+)天前|(\d{1,2})-(\d{1,2})")


def parse_time(today: date, s: str) -> date | None:
    m = TIME_RE.search(s)
    if not m:
        return None
    if m.group(1):  # 分钟前
        return today
    if m.group(2):  # 小时前
        return today
    if m.group(3):  # 天前
        return today - timedelta(days=int(m.group(3)))
    if "昨天" in m.group(0) or "前天" in m.group(0):
        return today - timedelta(days=2 if "前天" in m.group(0) else 1)
    if m.group(4) and m.group(5):  # M-D
        mon, day = int(m.group(4)), int(m.group(5))
        return date(today.year, mon, day)
    return None


def collect(max_scroll: int, days: int, out: str) -> int:
    today = date.today()
    cutoff = today - timedelta(days=days)
    seen: dict[str, dict] = {}
    old_hits = 0
    found_old = False

    with httpx.Client(timeout=90) as client:
        for i in range(max_scroll):
            r = tool(client, "browser_evaluate_js", {"expression": EXTRACT_JS})
            try:
                items = json.loads(r["result"])
            except Exception:
                items = []
            new = 0
            for text in items:
                if not text:
                    continue
                if text in seen:
                    continue
                # 时间解析
                d = parse_time(today, text)
                if d is not None:
                    if d < cutoff:
                        old_hits += 1
                    seen[text] = {"date": d.isoformat(), "text": text}
                    new += 1
            if new:
                print(f"[{i}] 本轮新增 {new}，累计 {len(seen)}，旧帖 {old_hits}")
            else:
                print(f"[{i}] 无新增")
            # 滚动加载
            tool(client, "browser_evaluate_js",
                 {"expression": "window.scrollTo(0, document.body.scrollHeight)"})
            time.sleep(1.2)
            # 若连续多轮无新增且出现旧帖，说明到底了
            if new == 0 and old_hits > 5 and i > 10:
                # 再滚几次确认
                tool(client, "browser_evaluate_js",
                     {"expression": "window.scrollTo(0, document.body.scrollHeight)"})
                time.sleep(1.5)
                r2 = tool(client, "browser_evaluate_js", {"expression": EXTRACT_JS})
                try:
                    items2 = json.loads(r2["result"])
                except Exception:
                    items2 = []
                if not any(t not in seen for t in items2):
                    print("已到达时间范围底部")
                    break

    # 保存
    posts = sorted(seen.values(), key=lambda x: x["date"], reverse=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"today": today.isoformat(), "cutoff": cutoff.isoformat(),
                   "total": len(posts), "posts": posts}, f, ensure_ascii=False, indent=1)
    print(f"已保存 {len(posts)} 条微博到 {out}")
    return len(posts)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-scroll", type=int, default=100)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--out", default="weibo_posts.json")
    args = ap.parse_args()
    sys.exit(collect(args.max_scroll, args.days, args.out))
