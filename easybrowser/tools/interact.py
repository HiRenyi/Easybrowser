"""操作层（20 个）工具：DOM 操作 L1 + CUA 坐标 L3 + JS 兜底 L4 + click_node L2。"""
from __future__ import annotations

import logging
from pathlib import Path

from playwright.async_api import TimeoutError as PWTimeoutError

from ..errors import ToolError
from ._util import (get_bool, get_int, get_str, normalize_key, require_int,
                    require_str, resolve_locator)
from .registry import ToolContext, ToolResult

logger = logging.getLogger(__name__)

# 敏感路径黑名单（系统目录、密钥目录）
SENSITIVE_PATH_PREFIXES = (
    "/etc/",
    "/var/",
    "/usr/",
    "/bin/",
    "/sbin/",
    "/.ssh/",
    "/.aws/",
    "/.gnupg/",
    "/root/",
    "/System/",  # macOS
    "C:\\Windows\\",  # Windows
    "C:\\Program Files\\",
)

# 豁免：macOS 每用户临时目录（tempfile 默认落点）。虽然以 /var/ 开头，
# 但它是用户级临时区而非系统敏感目录，不豁免会让"临时文件→上传"流程整条挂掉
EXEMPT_PATH_PREFIXES = (
    "/var/folders/",
    "/private/var/folders/",
)


def _validate_upload_path(file_path: str) -> Path:
    """
    验证文件上传路径的安全性

    安全检查：
    1. 解析为绝对路径
    2. 黑名单过滤（系统目录、密钥目录）
    3. 检查文件存在性

    Args:
        file_path: 用户提供的文件路径

    Returns:
        Path: 验证通过的绝对路径对象

    Raises:
        ToolError: 路径不合法或不安全
    """
    try:
        # 解析为绝对路径
        abs_path = Path(file_path).resolve()
        abs_path_str = str(abs_path)

        # 黑名单检查：原始路径与 resolve 后路径都查
        # （macOS 上 /etc /var /tmp 是 symlink，resolve 后变 /private/* 会让前缀失配）
        is_exempt = (file_path.startswith(EXEMPT_PATH_PREFIXES)
                     or abs_path_str.startswith(EXEMPT_PATH_PREFIXES))
        if not is_exempt:
            for prefix in SENSITIVE_PATH_PREFIXES:
                if file_path.startswith(prefix) or abs_path_str.startswith(prefix):
                    logger.warning(f"Blocked upload attempt to sensitive path: {abs_path_str}")
                    raise ToolError(
                        f"不允许上传系统敏感目录中的文件：{prefix}* 路径被禁止访问"
                    )

        # 检查文件存在性
        if not abs_path.exists():
            raise ToolError(f"文件不存在：{abs_path_str}")

        if not abs_path.is_file():
            raise ToolError(f"路径不是文件：{abs_path_str}")

        # 审计日志
        logger.info(f"File upload validated: {abs_path_str}")

        return abs_path

    except ToolError:
        raise
    except Exception as e:
        logger.error(f"Path validation error: {file_path} - {e}")
        raise ToolError(f"文件路径验证失败：{str(e)}")


async def _resolve_loc(ctx: ToolContext, p: dict) -> tuple[str, object]:
    """解析 locator；node_id 走坐标路径则返回 ("coords", (x,y))。"""
    node_id = p.get("node_id", p.get("nodeId"))
    if node_id is not None:
        nm = ctx.browser.get_nodemap(ctx.tab_id)
        entry = nm.get(int(node_id))
        if not entry:
            raise ToolError(f"node_id={node_id} 已失效，请重新 browser_snapshot")
        return "coords", (entry["x"], entry["y"])
    return await resolve_locator(ctx, p)


async def _click_with_fallback(ctx: ToolContext, desc: str, loc) -> None:
    """Playwright 点击；超时（遮挡/不稳定）时 JS .click() 兜底（React 合成事件）。"""
    try:
        await loc.click(timeout=ctx.cfg.action_timeout_ms)
    except PWTimeoutError:
        await loc.evaluate("el => el.click()")
        # 等一轮渲染
        await ctx.page.wait_for_timeout(200)


def build_interact(reg):
    # ================================================================ L1 DOM
    async def browser_click(ctx: ToolContext, p: dict) -> ToolResult:
        kind, target = await _resolve_loc(ctx, p)
        if kind == "coords":
            x, y = target
            await ctx.page.mouse.click(x, y)
            return ToolResult.ok(f"已点击坐标 ({x},{y})")
        await _click_with_fallback(ctx, kind, target)
        return ToolResult.ok(f"已点击（{kind}）")

    reg.add("browser_click", "点击元素（ref / node_id / selector / text）。遮挡/不稳定时自动 JS 兜底。",
            {"ref": {"type": "string"}, "selector": {"type": "string"},
             "node_id": {"type": "integer"}, "text": {"type": "string"},
             "tab_id": {"type": "integer"}},
            kind="write", handler=browser_click)

    async def browser_double_click(ctx: ToolContext, p: dict) -> ToolResult:
        kind, target = await _resolve_loc(ctx, p)
        if kind == "coords":
            x, y = target
            await ctx.page.mouse.dblclick(x, y)
            return ToolResult.ok(f"已双击坐标 ({x},{y})")
        try:
            await target.dblclick(timeout=ctx.cfg.action_timeout_ms)
        except PWTimeoutError:
            await target.evaluate("el => el.click(); el.click()")
        return ToolResult.ok(f"已双击（{kind}）")

    reg.add("browser_double_click", "双击元素",
            {"ref": {"type": "string"}, "selector": {"type": "string"},
             "node_id": {"type": "integer"}, "text": {"type": "string"},
             "tab_id": {"type": "integer"}},
            kind="write", handler=browser_double_click)

    async def browser_fill(ctx: ToolContext, p: dict) -> ToolResult:
        kind, target = await _resolve_loc(ctx, p)
        value = require_str(p, "value")
        if kind == "coords":
            x, y = target
            await ctx.page.mouse.click(x, y)
            await ctx.page.keyboard.type(value, delay=10)
            return ToolResult.ok(f"已填入 ({x},{y}): {value[:50]}")
        await target.fill(value, timeout=ctx.cfg.action_timeout_ms)
        return ToolResult.ok(f"已填入（{kind}）: {value[:50]}")

    reg.add("browser_fill", "填写输入框（自动聚焦/清空/输入，触发 React onChange）",
            {"ref": {"type": "string"}, "selector": {"type": "string"},
             "node_id": {"type": "integer"}, "text": {"type": "string"},
             "value": {"type": "string", "description": "要填入的内容（必填）"},
             "tab_id": {"type": "integer"}},
            kind="write", handler=browser_fill)

    async def browser_type(ctx: ToolContext, p: dict) -> ToolResult:
        keys = p.get("keys")
        text = p.get("text")
        if keys and isinstance(keys, list):
            for k in keys:
                await ctx.page.keyboard.press(normalize_key(str(k)))
            return ToolResult.ok(f"已派发 {len(keys)} 个按键")
        if text:
            try:
                await ctx.page.keyboard.type(str(text))
            except PWTimeoutError:
                pass
            return ToolResult.ok(f"已输入文本: {str(text)[:50]}")
        raise ToolError("需要 keys（按键数组）或 text（文本）")

    reg.add("browser_type", "派发按键序列（keys 数组）或输入文本（text）",
            {"keys": {"type": "array", "description": "按键名数组，如 [\"Enter\"]"},
             "text": {"type": "string", "description": "要输入的文本"},
             "tab_id": {"type": "integer"}},
            kind="write", handler=browser_type)

    async def browser_press_key(ctx: ToolContext, p: dict) -> ToolResult:
        key = normalize_key(require_str(p, "key"))
        await ctx.page.keyboard.press(key)
        return ToolResult.ok(f"已按键: {key}")

    reg.add("browser_press_key", "按下单个按键（Enter/Escape/Tab/ArrowUp 等）",
            {"key": {"type": "string", "description": "按键名（必填）"},
             "tab_id": {"type": "integer"}},
            kind="write", handler=browser_press_key)

    async def browser_select_option(ctx: ToolContext, p: dict) -> ToolResult:
        kind, target = await _resolve_loc(ctx, p)
        if kind == "coords":
            raise ToolError("select_option 需要 ref/selector 定位的下拉框，请用 browser_snapshot_ax 取 ref")
        if "value" in p:
            await target.select_option(value=str(p["value"]), timeout=ctx.cfg.action_timeout_ms)
        elif "label" in p:
            await target.select_option(label=str(p["label"]), timeout=ctx.cfg.action_timeout_ms)
        elif "index" in p:
            await target.select_option(index=get_int(p, "index"), timeout=ctx.cfg.action_timeout_ms)
        else:
            raise ToolError("需要 value / label / index 之一")
        return ToolResult.ok(f"已选择（{kind}）")

    reg.add("browser_select_option", "选择下拉框选项（value / label / index）",
            {"ref": {"type": "string"}, "selector": {"type": "string"},
             "text": {"type": "string"}, "value": {"type": "string"},
             "label": {"type": "string"}, "index": {"type": "integer"},
             "tab_id": {"type": "integer"}},
            kind="write", handler=browser_select_option)

    async def browser_set_checked(ctx: ToolContext, p: dict) -> ToolResult:
        kind, target = await _resolve_loc(ctx, p)
        if kind == "coords":
            raise ToolError("set_checked 需要 ref/selector 定位，请用 browser_snapshot_ax 取 ref")
        checked = get_bool(p, "checked", True)
        await target.set_checked(checked, timeout=ctx.cfg.action_timeout_ms)
        return ToolResult.ok(f"已{'勾选' if checked else '取消勾选'}（{kind}）")

    reg.add("browser_set_checked", "勾选/取消复选框",
            {"ref": {"type": "string"}, "selector": {"type": "string"},
             "text": {"type": "string"},
             "checked": {"type": "boolean", "description": "true 勾选 / false 取消（默认 true）"},
             "tab_id": {"type": "integer"}},
            kind="write", handler=browser_set_checked)

    async def browser_scroll(ctx: ToolContext, p: dict) -> ToolResult:
        if "ref" in p or "selector" in p or "text" in p or "node_id" in p:
            kind, target = await _resolve_loc(ctx, p)
            if kind == "coords":
                x, y = target
                await ctx.page.mouse.move(x, y)
                await ctx.page.mouse.wheel(0, get_int(p, "amount", 400))
                return ToolResult.ok(f"已在 ({x},{y}) 滚动")
            await target.scroll_into_view_if_needed(timeout=5000)
            return ToolResult.ok(f"已滚动到元素（{kind}）")
        direction = get_str(p, "direction", "down").lower()
        amount = get_int(p, "amount", 400)
        dx = dy = 0
        if direction in ("up", "down"):
            dy = -amount if direction == "up" else amount
        elif direction in ("left", "right"):
            dx = -amount if direction == "left" else amount
        else:
            raise ToolError(f"direction 必须是 up/down/left/right，得到 {direction}")
        await ctx.page.mouse.wheel(dx, dy)
        return ToolResult.ok(f"已{direction}滚动 {amount}px")

    reg.add("browser_scroll", "滚动页面（direction+amount）或滚动到元素（ref/selector）",
            {"direction": {"type": "string", "description": "up/down/left/right（默认 down）"},
             "amount": {"type": "integer", "description": "滚动量 px（默认 400）"},
             "ref": {"type": "string"}, "selector": {"type": "string"},
             "node_id": {"type": "integer"}, "text": {"type": "string"},
             "tab_id": {"type": "integer"}},
            kind="write", handler=browser_scroll)

    async def browser_hover(ctx: ToolContext, p: dict) -> ToolResult:
        kind, target = await _resolve_loc(ctx, p)
        if kind == "coords":
            x, y = target
            await ctx.page.mouse.move(x, y)
            return ToolResult.ok(f"已悬停 ({x},{y})")
        await target.hover(timeout=ctx.cfg.action_timeout_ms)
        return ToolResult.ok(f"已悬停（{kind}）")

    reg.add("browser_hover", "鼠标悬停元素",
            {"ref": {"type": "string"}, "selector": {"type": "string"},
             "node_id": {"type": "integer"}, "text": {"type": "string"},
             "tab_id": {"type": "integer"}},
            kind="write", handler=browser_hover)

    async def browser_drag(ctx: ToolContext, p: dict) -> ToolResult:
        points = p.get("points")
        if not isinstance(points, list) or len(points) < 2:
            raise ToolError("drag 需要 points 坐标数组（至少 2 个点）")
        pts = [(int(pt.get("x", 0)), int(pt.get("y", 0))) for pt in points[:20]]
        await ctx.page.mouse.move(*pts[0])
        await ctx.page.mouse.down()
        for pt in pts[1:]:
            await ctx.page.mouse.move(*pt)
        await ctx.page.mouse.up()
        return ToolResult.ok(f"已拖拽 {len(pts)} 个坐标点")

    reg.add("browser_drag", "沿坐标路径拖拽鼠标",
            {"points": {"type": "array", "description": "坐标数组 [{\"x\":..,\"y\":..}, ...]"}},
            kind="write", handler=browser_drag)

    async def browser_file_upload(ctx: ToolContext, p: dict) -> ToolResult:
        """
        上传文件到指定的文件输入框（必须是 <input type="file">）

        安全机制：
        - 路径解析为绝对路径并验证合法性
        - 黑名单过滤系统敏感目录（/etc/, /.ssh/, /.aws/ 等）
        - 检查文件存在性
        - 记录审计日志
        """
        kind, target = await _resolve_loc(ctx, p)
        if kind == "coords":
            raise ToolError("file_upload 需要 ref/selector 定位 input[type=file]")

        files = p.get("files")
        if not isinstance(files, list) or not files:
            raise ToolError("需要 files 文件路径数组")

        # 安全验证：路径检查
        validated_paths = []
        try:
            for file_path in files:
                validated_path = _validate_upload_path(str(file_path))
                validated_paths.append(str(validated_path))
        except ToolError as e:
            return ToolResult.err(str(e))

        # 执行上传
        try:
            await target.set_input_files(validated_paths,
                                         timeout=ctx.cfg.action_timeout_ms)
            return ToolResult.ok(f"已上传 {len(validated_paths)} 个文件（{kind}）")
        except Exception as e:
            logger.error(f"File upload failed: {validated_paths} - {e}")
            return ToolResult.err(f"文件上传失败：{str(e)}")

    reg.add("browser_file_upload", "上传文件（需要 ref/selector 定位 input[type=file]）",
            {"ref": {"type": "string"}, "selector": {"type": "string"},
             "text": {"type": "string"},
             "files": {"type": "array", "description": "文件路径数组（必填）"},
             "tab_id": {"type": "integer"}},
            kind="write", handler=browser_file_upload)

    # ================================================================ L2 click_node
    async def browser_click_node(ctx: ToolContext, p: dict) -> ToolResult:
        kind, target = await _resolve_loc(ctx, p)
        count = get_int(p, "clickCount", 1)
        if kind == "coords":
            x, y = target
            for _ in range(max(count, 1)):
                await ctx.page.mouse.click(x, y)
            return ToolResult.ok(f"已点击 node 坐标 ({x},{y}) ×{count}")
        await _click_with_fallback(ctx, kind, target)
        return ToolResult.ok(f"已点击节点（{kind}）")

    reg.add("browser_click_node", "点击元素（ref 或 node_id，坐标解析路径）",
            {"ref": {"type": "string"}, "node_id": {"type": "integer"},
             "clickCount": {"type": "integer", "description": "点击次数（默认 1）"},
             "tab_id": {"type": "integer"}},
            kind="write", handler=browser_click_node)

    # ================================================================ L3 坐标
    async def browser_click_at(ctx: ToolContext, p: dict) -> ToolResult:
        x, y = get_int(p, "x", 0), get_int(p, "y", 0)
        await ctx.page.mouse.click(x, y)
        return ToolResult.ok(f"已点击坐标 ({x},{y})")

    reg.add("browser_click_at", "坐标单击",
            {"x": {"type": "integer", "description": "X 坐标（必填）"},
             "y": {"type": "integer", "description": "Y 坐标（必填）"},
             "tab_id": {"type": "integer"}},
            kind="write", handler=browser_click_at)

    async def browser_double_click_at(ctx: ToolContext, p: dict) -> ToolResult:
        x, y = get_int(p, "x", 0), get_int(p, "y", 0)
        await ctx.page.mouse.dblclick(x, y)
        return ToolResult.ok(f"已双击坐标 ({x},{y})")

    reg.add("browser_double_click_at", "坐标双击",
            {"x": {"type": "integer"}, "y": {"type": "integer"},
             "tab_id": {"type": "integer"}},
            kind="write", handler=browser_double_click_at)

    async def browser_move_mouse(ctx: ToolContext, p: dict) -> ToolResult:
        x, y = get_int(p, "x", 0), get_int(p, "y", 0)
        await ctx.page.mouse.move(x, y)
        return ToolResult.ok(f"鼠标已移动 ({x},{y})")

    reg.add("browser_move_mouse", "移动鼠标到坐标",
            {"x": {"type": "integer"}, "y": {"type": "integer"},
             "tab_id": {"type": "integer"}},
            kind="write", handler=browser_move_mouse)

    async def browser_scroll_at(ctx: ToolContext, p: dict) -> ToolResult:
        x, y = get_int(p, "x", 0), get_int(p, "y", 0)
        dx = get_int(p, "deltaX", 0)
        dy = get_int(p, "deltaY", 300)
        await ctx.page.mouse.move(x, y)
        await ctx.page.mouse.wheel(dx, dy)
        return ToolResult.ok(f"已在 ({x},{y}) 滚轮 ({dx},{dy})")

    reg.add("browser_scroll_at", "在坐标处滚动（滚轮）",
            {"x": {"type": "integer"}, "y": {"type": "integer"},
             "deltaX": {"type": "integer", "description": "水平滚动（默认 0）"},
             "deltaY": {"type": "integer", "description": "垂直滚动（默认 300）"},
             "tab_id": {"type": "integer"}},
            kind="write", handler=browser_scroll_at)

    async def browser_drag_path(ctx: ToolContext, p: dict) -> ToolResult:
        return await browser_drag(ctx, p)

    reg.add("browser_drag_path", "路径拖拽（坐标数组）",
            {"points": {"type": "array", "description": "坐标数组"}},
            kind="write", handler=browser_drag_path)

    async def browser_type_at(ctx: ToolContext, p: dict) -> ToolResult:
        x, y = get_int(p, "x", 0), get_int(p, "y", 0)
        text = require_str(p, "text")
        await ctx.page.mouse.click(x, y)
        await ctx.page.keyboard.type(text)
        return ToolResult.ok(f"已点击 ({x},{y}) 并输入: {text[:50]}")

    reg.add("browser_type_at", "坐标点击聚焦后输入文本",
            {"x": {"type": "integer"}, "y": {"type": "integer"},
             "text": {"type": "string", "description": "要输入的文本（必填）"},
             "tab_id": {"type": "integer"}},
            kind="write", handler=browser_type_at)

    async def browser_press_key_combo(ctx: ToolContext, p: dict) -> ToolResult:
        combo = require_str(p, "combo")
        await ctx.page.keyboard.press(combo)
        return ToolResult.ok(f"已按下组合键: {combo}")

    reg.add("browser_press_key_combo", "组合键（如 \"Control+Shift+A\"）",
            {"combo": {"type": "string", "description": "组合键表达式，如 Control+Shift+I（必填）"},
             "tab_id": {"type": "integer"}},
            kind="write", handler=browser_press_key_combo)

    # ================================================================ L4 JS 兜底
    async def browser_js_click(ctx: ToolContext, p: dict) -> ToolResult:
        kind, target = await _resolve_loc(ctx, p)
        if kind == "coords":
            x, y = target
            await ctx.page.evaluate(
                f"document.elementFromPoint({x}, {y})?.click()")
            return ToolResult.ok(f"JS 点击坐标 ({x},{y})")
        await target.evaluate("el => el.click()")
        await ctx.page.wait_for_timeout(200)
        return ToolResult.ok(f"JS 点击完成（{kind}，触发 React 合成事件）")

    reg.add("browser_js_click", "JS .click() 触发 React 合成事件（L1 click 失败时兜底）",
            {"ref": {"type": "string"}, "selector": {"type": "string"},
             "node_id": {"type": "integer"}, "text": {"type": "string"},
             "tab_id": {"type": "integer"}},
            kind="write", handler=browser_js_click)

    async def browser_js_fill(ctx: ToolContext, p: dict) -> ToolResult:
        kind, target = await _resolve_loc(ctx, p)
        value = require_str(p, "value")
        if kind == "coords":
            raise ToolError("js_fill 需要 ref/selector 定位的输入框")
        await target.evaluate(
            """(el, value) => {
              const proto = Object.getPrototypeOf(el);
              const desc = Object.getOwnPropertyDescriptor(proto, 'value') ||
                           Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
              if (desc && desc.set) desc.set.call(el, value);
              else el.value = value;
              el.dispatchEvent(new Event('input', {bubbles: true}));
              el.dispatchEvent(new Event('change', {bubbles: true}));
            }""", value)
        return ToolResult.ok(f"JS 填入完成（{kind}，触发 React onChange）: {value[:50]}")

    reg.add("browser_js_fill", "JS 设值+事件分发触发 React onChange（L1 fill 失败时兜底）",
            {"ref": {"type": "string"}, "selector": {"type": "string"},
             "node_id": {"type": "integer"}, "text": {"type": "string"},
             "value": {"type": "string", "description": "值（必填）"},
             "tab_id": {"type": "integer"}},
            kind="write", handler=browser_js_fill)
