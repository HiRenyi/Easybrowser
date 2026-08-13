"""感知层（11 个）工具：snapshot_ax / snapshot / snapshot_visible / screenshot / 只读查询。"""
from __future__ import annotations

import base64
import json

from playwright.async_api import TimeoutError as PWTimeoutError

from ..a11y import get_ax_snapshot
from ..errors import ToolError
from ._util import get_bool, get_int, get_str, resolve_locator
from .registry import ToolContext, ToolResult

#: DOM 快照收集脚本：收集可交互元素 + 视口坐标
_DOM_COLLECT_JS = r"""
() => {
  const sel = 'button, a[href], input, textarea, select, [role="button"], [role="link"], [role="checkbox"], [role="radio"], [role="menuitem"], [role="tab"], [contenteditable="true"], [tabindex]:not([tabindex="-1"]), summary, label';
  const out = [];
  const els = document.querySelectorAll(sel);
  for (const el of els) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 && r.height < 2) continue;
    const tag = el.tagName.toLowerCase();
    let text = (el.innerText || el.getAttribute('aria-label') || el.title || el.value || '')
      .trim().replace(/\s+/g, ' ').slice(0, 120);
    if (!text && tag === 'input') text = el.placeholder || '';
    const attrs = {};
    for (const a of ['id','name','type','placeholder','href','value','aria-label','title','role']) {
      const v = el.getAttribute(a);
      if (v) attrs[a] = v.slice(0, 120);
    }
    const cs = getComputedStyle(el);
    const visible = r.top < innerHeight && r.bottom > 0 &&
      cs.display !== 'none' && cs.visibility !== 'hidden' && Number(cs.opacity || 1) > 0;
    out.push({
      tag, text, attrs,
      visible,
      x: Math.round(r.left + r.width / 2),
      y: Math.round(r.top + r.height / 2),
      w: Math.round(r.width), h: Math.round(r.height),
    });
  }
  return out;
}
"""


def build_perception(reg):
    # ---------------------------------------------------------------- snapshot_ax
    async def browser_snapshot_ax(ctx: ToolContext, p: dict) -> ToolResult:
        mode = get_str(p, "mode", "compact")
        if get_bool(p, "full", False):
            mode = "full"
        cdp = await ctx.cdp()
        text, refmap = await get_ax_snapshot(cdp, mode)
        # 存 refmap（per-tab），供后续工具按 ref 定位
        ctx.browser.set_refmap(ctx.tab_id, refmap)
        return ToolResult.ok(text)

    reg.add("browser_snapshot_ax",
            "AX 可访问性树快照（带稳定 ref，如 [ref=e1]）。ref 可直接用于 browser_click/browser_fill 等。"
            "compact 模式只保留含 ref 或值的行；full:true 返回完整树。",
            {"mode": {"type": "string", "description": "compact(默认)/full/interactive"},
             "full": {"type": "boolean", "description": "true 时返回完整 AX 树"},
             "force": {"type": "boolean", "description": "兼容参数，忽略"},
             "tab_id": {"type": "integer"}},
            kind="read", handler=browser_snapshot_ax)

    # ---------------------------------------------------------------- snapshot
    async def _collect_dom(ctx: ToolContext, viewport_only: bool) -> tuple[str, dict]:
        items = await ctx.page.evaluate(_DOM_COLLECT_JS)
        if not items:
            return "(no interactive elements)", {}
        nodemap = {}
        lines = []
        nid = 1
        for it in items:
            if viewport_only and not it["visible"]:
                continue
            nodemap[nid] = {"x": it["x"], "y": it["y"], "tag": it["tag"],
                            "text": it["text"], "visible": it["visible"],
                            "w": it["w"], "h": it["h"]}
            attrs = ""
            if it["attrs"]:
                attrs = " [" + ", ".join(f"{k}={v!r}" for k, v in list(it["attrs"].items())[:6]) + "]"
            mark = "" if it["visible"] else " (offscreen)"
            label = it["text"] or f"<{it['tag']}>"
            lines.append(f"[{nid}] {it['tag']} {label!r} at=({it['x']},{it['y']}){attrs}{mark}")
            nid += 1
        ctx.browser.set_nodemap(ctx.tab_id, nodemap)
        return "\n".join(lines), nodemap

    async def browser_snapshot(ctx: ToolContext, p: dict) -> ToolResult:
        text, _ = await _collect_dom(ctx, viewport_only=False)
        return ToolResult.ok(text)

    reg.add("browser_snapshot",
            "DOM 快照：列出页面全部可交互元素（带 node_id 与视口坐标）。"
            "node_id 可用于 browser_click_node / browser_fill_node 等。",
            {"full": {"type": "boolean", "description": "兼容参数，忽略"},
             "tab_id": {"type": "integer"}},
            kind="read", handler=browser_snapshot)

    async def browser_snapshot_visible(ctx: ToolContext, p: dict) -> ToolResult:
        text, _ = await _collect_dom(ctx, viewport_only=True)
        return ToolResult.ok(text)

    reg.add("browser_snapshot_visible",
            "可见 DOM 快照：只列视口内的可交互元素（带 node_id 与坐标）。",
            {"tab_id": {"type": "integer"}},
            kind="read", handler=browser_snapshot_visible)

    # ---------------------------------------------------------------- screenshot
    async def browser_screenshot(ctx: ToolContext, p: dict) -> ToolResult:
        fmt = get_str(p, "format", "jpeg").lower()
        if fmt not in ("jpeg", "png"):
            fmt = "jpeg"
        quality = get_int(p, "quality", 80)
        full_page = get_bool(p, "full_page", False)
        inline = get_bool(p, "inline_base64", False)
        try:
            raw = await ctx.page.screenshot(type=fmt, quality=quality, full_page=full_page)
        except PWTimeoutError:
            raise ToolError("截图超时")
        b64 = base64.b64encode(raw).decode()
        if inline:
            mime = "image/png" if fmt == "png" else "image/jpeg"
            return ToolResult.ok(f"data:{mime};base64,{b64}")
        return ToolResult.ok("截图完成", data={"base64": b64, "format": fmt,
                                               "full_page": full_page})

    reg.add("browser_screenshot", "截取当前页面（JPEG/PNG base64）",
            {"format": {"type": "string", "description": "jpeg(默认)/png"},
             "quality": {"type": "integer", "description": "JPEG 质量 0-100（默认 80）"},
             "full_page": {"type": "boolean", "description": "整页截图（默认 false 仅视口）"},
             "inline_base64": {"type": "boolean", "description": "true 时 result 直接返回 data URI"},
             "tab_id": {"type": "integer"}},
            kind="read", handler=browser_screenshot)

    # ---------------------------------------------------------------- 只读查询
    async def browser_get_text(ctx: ToolContext, p: dict) -> ToolResult:
        desc, loc = await resolve_locator(ctx, p)
        try:
            text = await loc.inner_text(timeout=3000)
        except PWTimeoutError:
            text = await loc.text_content(timeout=3000)
        return ToolResult.ok((text or "").strip() or "(empty)")

    reg.add("browser_get_text", "读取元素文本（ref / node_id / selector / text）",
            {"ref": {"type": "string"}, "selector": {"type": "string"},
             "node_id": {"type": "integer"}, "text": {"type": "string"},
             "tab_id": {"type": "integer"}},
            kind="read", handler=browser_get_text)

    async def browser_get_attribute(ctx: ToolContext, p: dict) -> ToolResult:
        desc, loc = await resolve_locator(ctx, p)
        attr = get_str(p, "attribute", p.get("name", ""))
        if not attr:
            raise ToolError("缺少必填参数: attribute")
        val = await loc.get_attribute(attr)
        return ToolResult.ok(val if val is not None else "(null)")

    reg.add("browser_get_attribute", "读取元素属性",
            {"ref": {"type": "string"}, "selector": {"type": "string"},
             "node_id": {"type": "integer"}, "text": {"type": "string"},
             "attribute": {"type": "string", "description": "属性名（必填）"},
             "tab_id": {"type": "integer"}},
            kind="read", handler=browser_get_attribute)

    async def browser_get_url(ctx: ToolContext, p: dict) -> ToolResult:
        return ToolResult.ok(ctx.page.url)

    reg.add("browser_get_url", "获取当前页面 URL",
            {"tab_id": {"type": "integer"}}, kind="read", handler=browser_get_url)

    async def browser_get_title(ctx: ToolContext, p: dict) -> ToolResult:
        try:
            title = await ctx.page.title()
        except Exception:
            title = ""
        return ToolResult.ok(title)

    reg.add("browser_get_title", "获取页面标题",
            {"tab_id": {"type": "integer"}}, kind="read", handler=browser_get_title)

    async def browser_is_visible(ctx: ToolContext, p: dict) -> ToolResult:
        desc, loc = await resolve_locator(ctx, p)
        try:
            visible = await loc.is_visible()
        except PWTimeoutError:
            visible = False
        return ToolResult.ok(f"{'visible' if visible else 'not visible'} ({desc})")

    reg.add("browser_is_visible", "检查元素是否可见",
            {"ref": {"type": "string"}, "selector": {"type": "string"},
             "node_id": {"type": "integer"}, "text": {"type": "string"},
             "tab_id": {"type": "integer"}},
            kind="read", handler=browser_is_visible)

    async def browser_is_enabled(ctx: ToolContext, p: dict) -> ToolResult:
        desc, loc = await resolve_locator(ctx, p)
        try:
            enabled = await loc.is_enabled()
        except PWTimeoutError:
            enabled = False
        return ToolResult.ok(f"{'enabled' if enabled else 'disabled'} ({desc})")

    reg.add("browser_is_enabled", "检查元素是否可用",
            {"ref": {"type": "string"}, "selector": {"type": "string"},
             "node_id": {"type": "integer"}, "text": {"type": "string"},
             "tab_id": {"type": "integer"}},
            kind="read", handler=browser_is_enabled)

    async def browser_count(ctx: ToolContext, p: dict) -> ToolResult:
        desc, loc = await resolve_locator(ctx, p)
        n = await loc.count()
        return ToolResult.ok(f"{n} ({desc})")

    reg.add("browser_count", "统计匹配元素数量",
            {"ref": {"type": "string"}, "selector": {"type": "string"},
             "node_id": {"type": "integer"}, "text": {"type": "string"},
             "tab_id": {"type": "integer"}},
            kind="read", handler=browser_count)
