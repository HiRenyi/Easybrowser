"""微博适配器 —— 示例站点适配器。

能力：
- get_user_posts: 抓取某账号最近 N 天微博（结构化：date + text）
  实现：在已登录的 weibo.com 页面里走同源 ajax API（/ajax/statuses/mymblog），
  等同正常翻页，风控风险低。

示例：
  POST /api/adapter/weibo/get_user_posts  {"uid":"2606218210","days":7}
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

_FETCH_PAGE_JS = r"""([uid, page]) => (async()=>{
  const r = await fetch('/ajax/statuses/mymblog?uid='+uid+'&feature=0&page='+page, {credentials:'include'});
  const j = await r.json();
  if (j.ok !== 1 || !j.data) return {ok:false, msg:(j.msg||'api error'), list:[]};
  const list = j.data.list || [];
  return {ok:true, list: list.map(b => ({
    t: b.created_at || '',
    text: (b.text_raw || b.text || '').replace(/<[^>]+>/g,' ').replace(/\s+/g,' ').trim()
  }))};
})(uid, page)"""


def _parse_created(s: str) -> datetime | None:
    try:
        return parsedate_to_datetime(s.strip()).replace(tzinfo=None)
    except Exception:
        return None


async def get_user_posts(browser, page, params: dict) -> dict:
    uid = str(params.get("uid") or "2606218210")
    days = int(params.get("days", 30))
    limit = int(params.get("limit", 500))

    # 确保在 weibo.com 域（登录态 cookie 作用域）
    if "weibo.com" not in page.url:
        await page.goto(f"https://weibo.com/u/{uid}", wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)

    cutoff = datetime.combine(date.today(), datetime.min.time()) - timedelta(days=days)
    posts: list[dict] = []
    max_page = 60
    for pageno in range(1, max_page + 1):
        try:
            data = await page.evaluate(_FETCH_PAGE_JS, [uid, pageno])
        except Exception:
            break
        if not data.get("ok"):
            break
        lst = data.get("list") or []
        if not lst:
            break
        page_min = None
        for it in lst:
            dt = _parse_created(it["t"])
            if not dt:
                continue
            if page_min is None or dt < page_min:
                page_min = dt
            if dt < cutoff:
                continue
            posts.append({"date": dt.date().isoformat(), "text": it["text"]})
            if len(posts) >= limit:
                break
        if len(posts) >= limit:
            break
        if page_min and page_min < cutoff:
            break
        if len(posts) >= limit:
            break

    # 去重 + 截断
    seen, dedup = set(), []
    for p in posts:
        if p["text"] in seen:
            continue
        seen.add(p["text"])
        dedup.append(p)
    return {"uid": uid, "days": days, "total": len(dedup),
            "start_date": cutoff.date().isoformat(),
            "posts": dedup[:limit]}


ADAPTER = {
    "name": "weibo",
    "title": "微博",
    "match_domains": ["weibo.com", "weibo.cn", "s.weibo.com"],
    "capabilities": {
        "get_user_posts": {
            "description": "抓取某微博账号最近 N 天微博（返回 date+text 列表）。需已登录微博。",
            "schema": {"uid": "integer 必填，账号 uid", "days": "integer 默认30",
                       "limit": "integer 默认500"},
            "handler": get_user_posts,
        },
    },
}
