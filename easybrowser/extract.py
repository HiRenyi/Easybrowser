"""通用结构化数据提取器。

两种来源（from 参数）：
- page: 在已加载页面用声明式规则（items_selector + fields）提取 DOM 元素
- api : 同源 fetch 接口（凭登录态），按 items_path + fields 从 JSON 提取

示例：
  POST /api/extract  {"from":"page", "items_selector":".wbpro-scroller-item",
                      "fields":{"text":".ogText","time":".from"}}
  POST /api/extract  {"from":"api", "api_url":"/ajax/statuses/mymblog?uid=xxx&page=1",
                      "rules":{"items_path":"data.list","fields":{"date":".created_at","text":".text_raw"}}}
"""
from __future__ import annotations

from .errors import ToolError

_DOM_EXTRACT_JS = r"""({items, fields, limit}) => {
  const nodes = document.querySelectorAll(items);
  const out = [];
  for (const n of Array.from(nodes).slice(0, limit || 100)) {
    const row = {};
    for (const [k, sel] of Object.entries(fields || {})) {
      const attrIdx = sel.indexOf('@');
      let s = sel, attr = null;
      if (attrIdx >= 0) { s = sel.slice(0, attrIdx); attr = sel.slice(attrIdx + 1); }
      const el = s ? n.querySelector(s) : n;
      if (!el) { row[k] = ''; continue; }
      row[k] = attr ? (el.getAttribute(attr) || '') : (el.innerText || '').trim();
    }
    out.push(row);
  }
  return out;
}"""

_FETCH_JS = r"""([url, method]) => (async()=>{
  const r = await fetch(url, {method: method || 'GET', credentials: 'include'});
  const ct = r.headers.get('content-type') || '';
  if (ct.includes('application/json')) { const j = await r.json(); return {ok:true, data:j}; }
  const t = await r.text(); return {ok:true, data:t};
})(url, method)"""


def _get_path(obj, path: str):
    """按点号路径取值：'data.list' / '.created_at' / 'list.0.text'（空段自动跳过）。"""
    if not path:
        return obj
    cur = obj
    for part in path.split("."):
        if not part:
            continue
        if isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)] if int(part) < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def _apply_rules(items, fields):
    """按 fields（输出key -> 输入路径）映射数组元素。"""
    if not fields:
        return items
    out = []
    for it in items:
        row = {}
        for k, path in fields.items():
            v = _get_path(it, path) if isinstance(path, str) else it.get(path)
            if isinstance(v, str):
                v = v.replace("<[^>]+>", "").strip()
            row[k] = v
        out.append(row)
    return out


async def run_extract(page, params: dict) -> dict:
    """执行提取。page 为已解析的 Playwright Page。"""
    source = (params.get("from") or "page").lower()

    if source == "api":
        api_url = params.get("api_url") or params.get("url")
        if not api_url:
            raise ToolError("from=api 需要 api_url（同源相对路径或完整 URL）")
        method = (params.get("method") or "GET").upper()
        result = await page.evaluate(_FETCH_JS, [api_url, method])
        if not result.get("ok"):
            raise ToolError(f"fetch 失败: {api_url}")
        data = result.get("data")
        rules = params.get("rules") or {}
        items_path = rules.get("items_path") or ""
        items = _get_path(data, items_path) if items_path else data
        if not isinstance(items, list):
            items = [items] if items is not None else []
        mapped = _apply_rules(items, rules.get("fields"))
        return {"source": "api", "url": api_url, "count": len(mapped), "items": mapped}

    # DOM 提取
    items_sel = params.get("items_selector") or params.get("rules", {}).get("items")
    if not items_sel:
        raise ToolError("from=page 需要 items_selector（要提取的重复元素选择器）")
    fields = params.get("fields") or params.get("rules", {}).get("fields") or {}
    limit = int(params.get("limit") or params.get("rules", {}).get("limit") or 100)
    items = await page.evaluate(_DOM_EXTRACT_JS, {
        "items": items_sel, "fields": fields, "limit": limit})
    return {"source": "page", "selector": items_sel,
            "count": len(items), "items": items}
