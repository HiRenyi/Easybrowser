"""站点适配器注册表。

适配器 = 按站点组织的「能力」集合。每个适配器是一个 Python 模块，暴露 ADAPTER dict：
{
  "name": "weibo",
  "title": "微博",
  "match_domains": ["weibo.com"],
  "capabilities": {
    "cap_name": {"description": "...", "schema": {...}, "handler": async fn(browser, page, params) -> dict}
  }
}

新适配器：在 easybrowser/adapters/ 下新建 <name>.py，定义 ADAPTER，自动加载。
"""
from __future__ import annotations

import importlib
import pkgutil

_cache: dict | None = None


def load_adapters() -> dict:
    """加载全部适配器。返回 {adapter_name: ADAPTER_dict}。"""
    global _cache
    if _cache is not None:
        return _cache
    adapters = {}
    for mod in pkgutil.iter_modules(__path__):
        if mod.name == "__init__":
            continue
        try:
            m = importlib.import_module(f"{__name__}.{mod.name}")
        except Exception as e:
            print(f"[adapters] 加载 {mod.name} 失败: {e}")
            continue
        ad = getattr(m, "ADAPTER", None)
        if ad and isinstance(ad, dict):
            ad.setdefault("module", mod.name)
            adapters[ad["name"]] = ad
    _cache = adapters
    return adapters


def list_capabilities(adapters: dict) -> list[dict]:
    out = []
    for name, ad in adapters.items():
        caps = ad.get("capabilities") or {}
        for cname, cinfo in caps.items():
            out.append({
                "adapter": name,
                "name": cname,
                "full": f"{name}.{cname}",
                "description": cinfo.get("description", ""),
                "schema": cinfo.get("schema", {}),
            })
    return out
