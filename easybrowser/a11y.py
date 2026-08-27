"""AX 可访问性树快照 + 稳定 ref 生成（对齐原项目 axtree/snapshot.go）。

主路径：CDP Accessibility.getFullAXTree → 解析 AXNode → 分配稳定 ref（e1,e2...）
→ 渲染文本（compact/full）。ref 记录 role+name+nth，供工具经 Playwright
get_by_role 解析回元素。
"""
from __future__ import annotations

import json

from .errors import ToolError

#: 交互角色恒分配 ref（对齐原项目 INTERACTIVE_ROLES）
INTERACTIVE_ROLES = {
    "button", "link", "textbox", "checkbox", "radio", "combobox", "listbox",
    "menuitem", "menuitemcheckbox", "menuitemradio", "option", "searchbox",
    "slider", "spinbutton", "switch", "tab", "treeitem", "Iframe",
}

#: 内容角色有名字时分配 ref（对齐原项目 CONTENT_ROLES）
CONTENT_ROLES = {
    "heading", "cell", "gridcell", "columnheader", "rowheader", "listitem",
    "article", "region", "main", "navigation",
}

#: 渲染时透传不输出的角色
PASSTHROUGH_ROLES = {"RootWebArea", "WebArea", ""}


def _prop_value(p) -> str | bool | int | None:
    if not p:
        return None
    v = p.get("value")
    return v


class _Node:
    __slots__ = ("id", "role", "name", "value", "backend", "frame",
                 "children", "props", "ref", "nth")

    def __init__(self, n: dict):
        self.id = n.get("nodeId")
        self.role = (n.get("role") or {}).get("value", "")
        self.name = (n.get("name") or {}).get("value", "") or ""
        self.value = (n.get("value") or {}).get("value", "") or ""
        self.backend = n.get("backendDOMNodeId", 0) or 0
        self.frame = n.get("frameId", "") or ""
        self.children = list(n.get("childIds", []))
        self.props: dict = {}
        for p in n.get("properties", []) or []:
            v = _prop_value(p.get("value"))
            if v is not None:
                self.props[p.get("name")] = v
        self.ref = ""
        self.nth = -1

    def qualify(self) -> bool:
        if self.role in INTERACTIVE_ROLES:
            return True
        if self.role in CONTENT_ROLES and self.name:
            return True
        return False


def _build_nodes(raw_nodes: list[dict]) -> dict[int, _Node]:
    return {n["nodeId"]: _Node(n) for n in raw_nodes}


def _find_roots(nodes: dict[int, _Node]) -> list[int]:
    child_ids = set()
    for n in nodes.values():
        child_ids.update(n.children)
    return [nid for nid in nodes if nid not in child_ids]


def _assign_refs(nodes: dict[int, _Node], refmap: dict) -> None:
    """qualify + (role,name) nth 去重 + 分配 ref（对齐 assignRefs）。"""
    counts: dict[tuple, int] = {}
    provisional: dict[int, int] = {}
    for n in nodes.values():
        if not n.qualify():
            continue
        key = (n.role, n.name)
        counts[key] = counts.get(key, 0) + 1
        provisional[n.id] = counts[key] - 1  # 0-based position

    nref = 1
    for n in nodes.values():
        if not n.qualify():
            continue
        key = (n.role, n.name)
        nth = provisional[n.id] if counts[key] > 1 else 0  # 唯一时 0
        n.nth = nth
        n.ref = f"e{nref}"
        nref += 1
        refmap[n.ref] = {
            "ref": n.ref,
            "role": n.role,
            "name": n.name,
            "nth": nth,              # 0-based group position（Playwright locator.nth）
            "backend_node_id": n.backend,
            "frame_id": n.frame,
        }


# ------------------------------------------------------------------ 渲染
def _format_line(n: _Node, depth: int) -> str:
    indent = "  " * depth
    attrs: list[str] = []
    for key in ("level", "checked", "expanded", "selected", "disabled", "required"):
        v = n.props.get(key)
        if v is not None:
            attrs.append(f"{key}={v}")
    if n.ref:
        attrs.append("ref=" + n.ref)
    url = n.props.get("url")
    if url:
        attrs.append(f"url={url}")
    name = json.dumps(n.name, ensure_ascii=False)
    line = f"{indent}- {n.role} {name}"
    if attrs:
        line += " [" + ", ".join(attrs) + "]"
    if n.value and n.value != n.name:
        line += ": " + n.value
    return line


def _render(nodes: dict[int, _Node], roots: list[int], lines: list[str],
            idx: int, depth: int) -> None:
    n = nodes[idx]
    if n.role in PASSTHROUGH_ROLES:
        for c in n.children:
            _render(nodes, roots, lines, c, depth)
        return
    if n.role == "generic" and not n.ref and len(n.children) <= 1:
        for c in n.children:
            _render(nodes, roots, lines, c, depth)
        return
    if n.name.strip() == "" and n.role == "StaticText":
        for c in n.children:
            _render(nodes, roots, lines, c, depth)
        return
    line = _format_line(n, depth)
    if line:
        lines.append(line)
    for c in n.children:
        _render(nodes, roots, lines, c, depth)


def _compact(text: str) -> str:
    """保留含 ref= 或值（: ）的行及祖先（对齐 compactTree）。"""
    lines = text.split("\n")
    keep = [False] * len(lines)
    for i, l in enumerate(lines):
        if "ref=" in l or (": " in l and "[ref=" not in l):
            keep[i] = True
    for i, l in enumerate(lines):
        if not keep[i]:
            continue
        my_indent = _count_indent(l)
        for j in range(i - 1, -1, -1):
            if _count_indent(lines[j]) < my_indent:
                keep[j] = True
                my_indent = _count_indent(lines[j])
    out = [l for i, l in enumerate(lines) if l and keep[i]]
    if not out:
        return "(no interactive elements)"
    return "\n".join(out)


def _count_indent(line: str) -> int:
    # 注意括号：必须先算差值再除以缩进步长，否则优先级错误导致 compact 丢祖先行
    return (len(line) - len(line.lstrip(" "))) // 2


async def get_ax_snapshot(cdp, mode: str = "compact") -> tuple[str, dict]:
    """获取 AX 树快照。返回 (文本, refmap)。mode: compact | full | interactive。"""
    try:
        await cdp.send("Accessibility.enable")
    except Exception:
        pass
    resp = await cdp.send("Accessibility.getFullAXTree")
    raw = resp.get("nodes") or []
    if not raw:
        raise ToolError("AX 树为空（页面可能无可访问性内容）")
    nodes = _build_nodes(raw)
    roots = _find_roots(nodes)
    refmap: dict = {}
    _assign_refs(nodes, refmap)
    lines: list[str] = []
    for r in roots:
        _render(nodes, roots, lines, r, 0)
    text = "\n".join(lines)
    if mode in ("compact", "interactive", ""):
        text = _compact(text)
    return text, refmap
