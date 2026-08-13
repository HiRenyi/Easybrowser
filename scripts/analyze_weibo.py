#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析中国妇女报最近微博的分类：孩子非亲生 / 男方出轨 / 女方出轨。"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys

NON_BIOLOGICAL_KW = [
    "非亲生", "亲子鉴定", "非婚生", "私生子", "非婚生子", "亲生父亲",
    "滴血认亲", "亲子关系", "身世", "不是亲生", "养子", "DNA鉴定",
]
MALE_MARKER = ["男方", "丈夫", "老公", "已婚男", "男方当事人", "男性", "男子", "男人", "爸"]
FEMALE_MARKER = ["女方", "妻子", "老婆", "已婚女", "女方当事人", "女性", "女子", "女人", "妈"]
CHEAT_KW = ["出轨", "婚外情", "外遇", "婚内出轨", "背叛", "小三", "插足", "不忠", "劈腿", "背叛婚姻"]


def clean(t: str) -> str:
    t = html.unescape(t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def classify(text: str) -> tuple[bool, bool, bool]:
    """返回 (孩子非亲生, 男方出轨, 女方出轨)。"""
    non_bio = any(k in text for k in NON_BIOLOGICAL_KW)
    has_cheat = any(k in text for k in CHEAT_KW)
    male = any(m in text for m in MALE_MARKER)
    female = any(m in text for m in FEMALE_MARKER)
    male_cheat = has_cheat and male and not (female and not male)
    female_cheat = has_cheat and female and not (male and not female)
    return non_bio, male_cheat, female_cheat


def main(path: str, days: int):
    d = json.load(open(path, encoding="utf-8"))
    posts = d["posts"]
    # 过滤最近 days 天（用 date 字符串比较，今天减 days）
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    recent = [p for p in posts if p["date"] >= cutoff]

    cats = {"non_bio": [], "male": [], "female": [], "cheat_unspecified": []}
    for p in recent:
        non_bio, male, female = classify(p["text"])
        if non_bio:
            cats["non_bio"].append(p)
        if male:
            cats["male"].append(p)
        if female:
            cats["female"].append(p)
        # 只有出轨词但无法判断男女方
        if not male and not female and any(k in p["text"] for k in CHEAT_KW):
            cats["cheat_unspecified"].append(p)

    print(f"=== 中国妇女报最近 {days} 天微博分析 ===")
    print(f"总微博数（{cutoff} 起）: {len(recent)}")
    print(f"\n【孩子非亲生】相关: {len(cats['non_bio'])} 条")
    print(f"【男方出轨】相关: {len(cats['male'])} 条")
    print(f"【女方出轨】相关: {len(cats['female'])} 条")
    print(f"【出轨但未指明男女方】: {len(cats['cheat_unspecified'])} 条")

    for key, label in [("non_bio", "孩子非亲生"), ("male", "男方出轨"), ("female", "女方出轨")]:
        print(f"\n--- {label} 示例（最多 8 条）---")
        for p in cats[key][:8]:
            t = clean(p["text"])
            print(f"  [{p['date']}] {t[:90]}")
        if len(cats[key]) > 8:
            print(f"  ... 共 {len(cats[key])} 条")

    # 保存分类结果
    out = path.replace(".json", "_classified.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({k: [{"date": p["date"], "text": clean(p["text"])} for p in v]
                   for k, v in cats.items()}, f, ensure_ascii=False, indent=1)
    print(f"\n分类结果已存: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="/tmp/weibo_posts.json")
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    main(args.path, args.days)
