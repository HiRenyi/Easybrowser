#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 weibo.com 同源 ajax API 拉取中国妇女报最近 N 天微博，合并滚动收集结果。

用法:
  python scripts/collect_weibo_api.py [--days 30] [--out /tmp/weibo_posts.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx

BASE = "http://127.0.0.1:58086"
UID = "2606218210"

PAGE_JS_TPL = """(async()=>{
  const r = await fetch('/ajax/statuses/mymblog?uid=%s&feature=0&page=__PAGE__', {credentials:'include'});
  const j = await r.json();
  if (j.ok !== 1 || !j.data) return {ok:false, msg: (j.msg||'api error'), n:0, list:[]};
  const list = j.data.list || [];
  return {ok:true, n: list.length, list: list.map(b => ({
    t: b.created_at || '',
    text: (b.text_raw || b.text || '').replace(/<[^>]+>/g,' ').replace(/\\s+/g,' ').trim()
  }))};
})()""" % UID


def page_js(page: int) -> str:
    return PAGE_JS_TPL.replace("__PAGE__", str(page))


def tool(client, name, params):
    r = client.post(f"{BASE}/api/tool/{name}", json=params, timeout=90)
    return r.json()


def parse_created(s: str) -> datetime | None:
    s = s.strip()
    try:
        dt = parsedate_to_datetime(s)
        return dt.replace(tzinfo=None)  # 统一为墙钟时间（+0800 → naive）
    except Exception:
        return None


def fetch_page(client, page: int) -> dict:
    r = tool(client, "browser_evaluate_js", {"expression": page_js(page)})
    try:
        return json.loads(r.get("result", "{}"))
    except Exception:
        return {"ok": False, "msg": "parse error", "n": 0, "list": []}


def main(days: int, out: str) -> int:
    today = date.today()
    cutoff_dt = datetime.combine(today, datetime.min.time()) - timedelta(days=days)
    new_posts: dict[str, dict] = {}

    with httpx.Client(timeout=90) as client:
        page = 1
        while page <= 50:
            data = fetch_page(client, page)
            if not data.get("ok"):
                print(f"page {page}: API 异常 {data.get('msg')}，停止")
                break
            n = data.get("n", 0)
            if n == 0:
                print(f"page {page}: 空页，结束")
                break
            page_dt_min = None
            for it in data["list"]:
                dt = parse_created(it["t"])
                if not dt:
                    continue
                if page_dt_min is None or dt < page_dt_min:
                    page_dt_min = dt
                if dt < cutoff_dt:
                    continue  # 超 30 天，跳过（本页还有更早则继续翻到更早）
                new_posts[it["text"]] = {"date": dt.date().isoformat(),
                                         "text": it["text"]}
            print(f"page {page}: {n} 条，保留 {len(data['list'])} 中累计 {len(new_posts)}，"
                  f"页内最早 {page_dt_min}")
            # 若本页最早已早于 cutoff，说明已翻完 30 天范围
            if page_dt_min and page_dt_min < cutoff_dt:
                print("已翻过 30 天边界")
                break
            page += 1
            time.sleep(0.4)

    # 合并已有滚动数据
    try:
        old = json.load(open(out, encoding="utf-8"))
        old_posts = {p["text"]: p for p in old.get("posts", [])}
    except Exception:
        old_posts = {}
    merged = {**old_posts}
    added = 0
    for k, v in new_posts.items():
        if k not in merged:
            merged[k] = v
            added += 1
    posts = sorted(merged.values(), key=lambda x: x["date"], reverse=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"today": today.isoformat(), "cutoff": (today - timedelta(days=days)).isoformat(),
                   "total": len(posts), "posts": posts}, f, ensure_ascii=False, indent=1)
    print(f"合并完成：总 {len(posts)} 条（API 新增 {added}），已保存 {out}")
    return len(posts)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--out", default="/tmp/weibo_posts.json")
    args = ap.parse_args()
    sys.exit(main(args.days, args.out))
