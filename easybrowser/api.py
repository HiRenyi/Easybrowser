"""FastAPI 应用。"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .adapters import list_capabilities, load_adapters
from .browser import BrowserManager
from .config import Config
from .errors import ToolError
from .extract import run_extract
from .recorder import RecorderManager
from .tools import build_registry

log = logging.getLogger("easybrowser.api")


def create_app(cfg: Config) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        bm = BrowserManager(cfg)
        await bm.connect()
        app.state.browser = bm
        app.state.registry = build_registry(bm)
        app.state.recorder = RecorderManager(bm, app.state.registry)
        app.state.adapters = load_adapters()
        log.info("EasyBrowser 就绪: http://%s:%d （%d 个工具，%d 个适配器）",
                 cfg.host, cfg.port, len(app.state.registry.all()), len(app.state.adapters))
        yield
        await bm.close()

    app = FastAPI(title="EasyBrowser", version="0.1.0",
                  description="浏览器自动化本地 HTTP API：52 个浏览器操作工具 + 录制/重放。"
                              "详见 /api/tools。",
                  lifespan=lifespan)

    # ------------------------------------------------------------- 通用
    @app.get("/health")
    async def health(request: Request):
        bm: BrowserManager = request.app.state.browser
        return {
            "status": "ok" if bm.connected else "error",
            "mode": cfg.mode,
            "tabs": len(bm._pages) if bm.connected else 0,
            "active_tab": bm._active_tab_id if bm.connected else None,
            "tools": len(request.app.state.registry.all()),
        }

    @app.get("/api/tools")
    async def list_tools(request: Request):
        tools = request.app.state.registry.spec()
        return {"ok": True, "count": len(tools), "tools": tools}

    # ------------------------------------------------------------- 工具调用
    @app.post("/api/tool/{name}")
    async def call_tool(name: str, request: Request):
        reg = request.app.state.registry
        if not reg.has(name):
            return JSONResponse(status_code=404,
                                content={"ok": False, "error": f"未知工具: {name}",
                                         "hint": "GET /api/tools 查看工具列表"})
        try:
            params = await request.json() or {}
        except Exception:
            params = {}
        if not isinstance(params, dict):
            return JSONResponse(status_code=400,
                                content={"ok": False, "error": "请求体必须是 JSON 对象"})
        start = time.time()
        result = await reg.run(name, params)
        elapsed = int((time.time() - start) * 1000)
        # 录制：AI 步骤自动记录 + 证据帧
        app.state.recorder.record_ai_step(name, params, not result.is_error)
        await app.state.recorder.after_ai_step()
        return {
            "ok": not result.is_error,
            "result": result.text,
            "error": result.text if result.is_error else "",
            "is_error": result.is_error,
            "duration_ms": elapsed,
            "data": result.data,
        }

    # ------------------------------------------------------------- 批量
    @app.post("/api/batch")
    async def batch(request: Request):
        reg = request.app.state.registry
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "请求体必须是 JSON")
        steps = body.get("steps") if isinstance(body, dict) else None
        if not isinstance(steps, list) or not steps:
            raise HTTPException(400, "steps 必须是非空数组 [{\"tool\":...,\"params\":{...}}]")
        stop_on_error = body.get("stop_on_error", True)
        results = []
        all_ok = True
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                results.append({"step": i, "tool": "?", "ok": False, "error": "step 格式错误"})
                all_ok = False
                if stop_on_error:
                    break
                continue
            tool = step.get("tool", "")
            params = step.get("params") or {}
            if not reg.has(tool):
                results.append({"step": i, "tool": tool, "ok": False,
                                "error": f"未知工具: {tool}"})
                all_ok = False
                if stop_on_error:
                    break
                continue
            start = time.time()
            r = await reg.run(tool, params)
            elapsed = int((time.time() - start) * 1000)
            # 录制：批量步骤也记录
            app.state.recorder.record_ai_step(tool, params, not r.is_error)
            await app.state.recorder.after_ai_step()
            ok = not r.is_error
            results.append({"step": i, "tool": tool, "ok": ok,
                            "result": r.text, "error": r.text if r.is_error else "",
                            "is_error": r.is_error, "duration_ms": elapsed})
            if not ok and stop_on_error:
                all_ok = False
                break
            if not ok:
                all_ok = False
        return {"ok": all_ok, "results": results,
                "total": len(steps), "executed": len(results)}

    # ------------------------------------------------------------- 录制/重放
    @app.post("/api/record/start")
    async def record_start(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        sess = await request.app.state.recorder.start(
            tab_id=body.get("tab_id"), capture_user=body.get("capture_user", False))
        return {"ok": True, "record_id": sess.id,
                "capture_user": sess.capture_user, "tab_id": sess.tab_id}

    @app.post("/api/record/stop")
    async def record_stop(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        rid = (body or {}).get("record_id")
        if not rid:
            return JSONResponse(status_code=400,
                                content={"ok": False, "error": "缺少 record_id"})
        script = await request.app.state.recorder.stop(int(rid))
        return {"ok": True, **script}

    @app.get("/api/record/{record_id}")
    async def record_get(record_id: int, request: Request):
        sess = request.app.state.recorder.get(record_id)
        if sess is None:
            return JSONResponse(status_code=404,
                                content={"ok": False, "error": f"录制会话 {record_id} 不存在"})
        return {"ok": True, "record_id": sess.id, "tab_id": sess.tab_id,
                "capture_user": sess.capture_user, "active": sess.active,
                "steps": sess.steps, "evidence": sess.evidence,
                "started": sess.started}

    @app.post("/api/record/replay")
    async def record_replay(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        rec = request.app.state.recorder
        if body.get("record_id") is not None:
            source: dict | int = int(body["record_id"])
        elif body.get("script"):
            source = body["script"]
        else:
            return JSONResponse(status_code=400,
                                content={"ok": False,
                                         "error": "需要 record_id 或 script"})
        try:
            result = await rec.replay(source)
        except Exception as e:
            return JSONResponse(status_code=400,
                                content={"ok": False, "error": str(e)})
        return {"ok": result["ok"], "executed": result["executed"],
                "results": result["results"]}

    # ------------------------------------------------------------- 结构化提取
    @app.post("/api/extract")
    async def extract(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        page = request.app.state.browser.get_page(body.get("tab_id"))
        try:
            result = await run_extract(page, body)
            return {"ok": True, **result}
        except ToolError as e:
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------- 站点适配器
    @app.get("/api/adapters")
    async def list_adapters(request: Request):
        adapters = request.app.state.adapters
        return {
            "ok": True,
            "count": len(adapters),
            "adapters": [
                {"name": a, "title": ad.get("title", ""),
                 "match_domains": ad.get("match_domains", []),
                 "capabilities": list((ad.get("capabilities") or {}).keys())}
                for a, ad in adapters.items()
            ],
            "capabilities": list_capabilities(adapters),
        }

    @app.post("/api/adapter/{name}/{capability}")
    async def call_adapter(name: str, capability: str, request: Request):
        adapters = request.app.state.adapters
        ad = adapters.get(name)
        if not ad:
            return JSONResponse(status_code=404,
                                content={"ok": False, "error": f"适配器 {name} 不存在"})
        cinfo = (ad.get("capabilities") or {}).get(capability)
        if not cinfo:
            return JSONResponse(status_code=404,
                                content={"ok": False, "error": f"能力 {name}.{capability} 不存在"})
        handler = cinfo.get("handler")
        if not callable(handler):
            return JSONResponse(status_code=400,
                                content={"ok": False, "error": "handler 不可调用"})
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        page = request.app.state.browser.get_page(body.get("tab_id"))
        try:
            result = await handler(request.app.state.browser, page, body)
            return {"ok": True, "data": result}
        except ToolError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            log.exception("适配器 %s.%s 异常", name, capability)
            return {"ok": False, "error": f"内部错误: {e}"}

    return app
