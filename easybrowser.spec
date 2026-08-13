# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把 EasyBrowser（Python 服务）打成 Windows exe。

- onedir：dist/easybrowser/EasyBrowser.exe（比 onefile 稳，Playwright 驱动文件多）
- collect playwright（含 node driver）+ uvicorn/fastapi/pydantic 等动态导入
- 浏览器复用系统 Edge（channel=msedge），不打包 Chromium
"""
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for pkg in ("playwright", "uvicorn", "fastapi", "starlette",
            "pydantic", "anyio", "multipart"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# uvicorn / FastAPI 运行时的动态导入
hiddenimports += [
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "python_multipart",
]

a = Analysis(
    ["easybrowser/__main__.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "test", "tests"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EasyBrowser",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # 保留控制台便于查看日志；正式发布可改 False
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="easybrowser",
)
