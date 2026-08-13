"""工具辅助：参数解析 + ref/locator 解析。"""
from __future__ import annotations

from playwright.async_api import Locator, Page

from ..errors import ToolError


# ---------------------------------------------------------------- 参数解析
def require_str(params: dict, key: str) -> str:
    v = params.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ToolError(f"缺少必填参数: {key}")
    return v


def require_int(params: dict, key: str) -> int:
    v = params.get(key)
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ToolError(f"参数 {key} 必须是整数，得到: {v!r}")


def get_str(params: dict, key: str, default: str = "") -> str:
    v = params.get(key)
    return v if isinstance(v, str) else default


def get_int(params: dict, key: str, default: int = 0) -> int:
    v = params.get(key)
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def get_bool(params: dict, key: str, default: bool = False) -> bool:
    v = params.get(key)
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


# ---------------------------------------------------------------- ref/locator 解析
async def ref_to_locator(page: Page, refmap: dict, ref: str) -> Locator:
    """ref -> Playwright Locator（主路径 role/name/nth）。"""
    entry = refmap.get(str(ref))
    if not entry:
        raise ToolError(
            f"ref {ref} 已失效，请先重新调用 browser_snapshot_ax 获取最新 ref"
        )
    role = entry.get("role") or ""
    name = entry.get("name") or ""
    nth = int(entry.get("nth", 0) or 0)  # 0-based group position
    if role:
        loc = page.get_by_role(role, name=name, exact=True).nth(nth)
        if await loc.count() > 0:
            return loc
        # 容错：nth 不匹配时回退到首个
        loc0 = page.get_by_role(role, name=name, exact=True).first
        if await loc0.count() > 0:
            return loc0
    raise ToolError(
        f"ref {ref} 无法解析为当前页面元素（页面可能已变化，请重新 browser_snapshot_ax）"
    )


async def resolve_locator(ctx, params: dict) -> tuple[str, Locator]:
    """从 ref / node_id / selector / text 解析 Locator。

    返回 (定位方式描述, locator)。
    """
    page: Page = ctx.page
    ref = params.get("ref")
    selector = params.get("selector")
    text = params.get("text")
    node_id = params.get("node_id", params.get("nodeId"))

    if ref:
        loc = await ref_to_locator(page, ctx.browser.get_refmap(ctx.tab_id), str(ref))
        return f"ref={ref}", loc
    if selector:
        return f"selector={selector}", page.locator(selector)
    if text:
        return f"text={text}", page.get_by_text(str(text), exact=False).first
    if node_id is not None:
        # node_id（backendNodeId）无法直接映射 Playwright locator；
        # 优先用坐标路径（见 interact 层 click_at 兜底），这里尝试 refmap 记录的 selector
        rm = ctx.browser.get_refmap(ctx.tab_id)
        for e in rm.values():
            if e.get("backend_node_id") == int(node_id) and e.get("role"):
                loc = await ref_to_locator(page, rm, e.get("ref"))
                return f"node_id={node_id}(via ref)", loc
        raise ToolError(
            f"node_id={node_id} 无法直接定位元素。请改用 browser_snapshot_ax 的 ref，"
            f"或使用坐标操作（browser_click_at / browser_click_node 走坐标解析）。"
        )
    raise ToolError("缺少定位参数（ref / node_id / selector / text 四选一）")


# ---------------------------------------------------------------- 按键映射
#: 常见按键名 → Playwright key 语法
KEY_ALIASES = {
    "enter": "Enter", "return": "Enter", "esc": "Escape", "escape": "Escape",
    "tab": "Tab", "space": " ", "spacebar": " ", "backspace": "Backspace",
    "delete": "Delete", "del": "Delete", "arrowup": "ArrowUp", "up": "ArrowUp",
    "arrowdown": "ArrowDown", "down": "ArrowDown", "arrowleft": "ArrowLeft",
    "left": "ArrowLeft", "arrowright": "ArrowRight", "right": "ArrowRight",
    "home": "Home", "end": "End", "pageup": "PageUp", "pagedown": "PageDown",
    "control": "Control", "ctrl": "Control", "shift": "Shift", "alt": "Alt",
    "meta": "Meta", "capslock": "CapsLock", "f1": "F1", "f2": "F2",
    "f3": "F3", "f4": "F4", "f5": "F5", "f6": "F6", "f7": "F7",
    "f8": "F8", "f9": "F9", "f10": "F10", "f11": "F11", "f12": "F12",
}


def normalize_key(key: str) -> str:
    k = key.strip()
    if k in KEY_ALIASES:
        return KEY_ALIASES[k]
    return k
