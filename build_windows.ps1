# ============================================================
# EasyBrowser Windows 打包脚本（在 Windows 机器上运行）
# 作用：把 easybrowser + 全部 Python 依赖 + Chromium 浏览器
#       打成 exe 分发包（老板零下载，完全自带浏览器）
# 前提：本机已装 Python 3.10+（含 pip）
# 用法：powershell -ExecutionPolicy Bypass -File build_windows.ps1
# ============================================================
$ErrorActionPreference = "Stop"
$root = Split-Path $MyInvocation.MyCommand.Path
Set-Location $root

# 可换国内镜像（pip 源 + Playwright 浏览器下载源）
$PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
$env:PLAYWRIGHT_DOWNLOAD_HOST = "https://npmmirror.com/mirrors/playwright"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  EasyBrowser Windows 打包（自带 Chromium）" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# 1. 检查 Python
python --version
if ($LASTEXITCODE -ne 0) {
    Write-Host "[错误] 未找到 Python，请先安装 Python 3.10+ 并加入 PATH" -ForegroundColor Red
    exit 1
}

# 2. 安装构建依赖（PyInstaller）
Write-Host "`n[1/5] 安装 PyInstaller..." -ForegroundColor Yellow
python -m pip install --upgrade pip
python -m pip install pyinstaller -i $PIP_INDEX

# 3. 安装项目依赖（fastapi/uvicorn/playwright/pydantic）
Write-Host "`n[2/5] 安装项目依赖..." -ForegroundColor Yellow
python -m pip install . -i $PIP_INDEX

# 4. 下载 Chromium 浏览器（自带，老板零下载）
Write-Host "`n[3/5] 下载 Chromium（含镜像加速，约 150MB）..." -ForegroundColor Yellow
python -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
    Write-Host "[错误] Chromium 下载失败。可手动执行后重试：" -ForegroundColor Red
    Write-Host "  `$env:PLAYWRIGHT_DOWNLOAD_HOST='https://npmmirror.com/mirrors/playwright'; python -m playwright install chromium"
    exit 1
}

# 5. PyInstaller 打包 exe
Write-Host "`n[4/5] PyInstaller 打包 exe（约 2-5 分钟）..." -ForegroundColor Yellow
python -m PyInstaller easybrowser.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) {
    Write-Host "[错误] PyInstaller 打包失败，看上方日志" -ForegroundColor Red
    exit 1
}

# 6. 整理分发目录：exe + 启动器 + 说明 + Chromium
Write-Host "`n[5/5] 整理分发目录..." -ForegroundColor Yellow
$dist = "dist/easybrowser"
if (Test-Path "start.bat") { Copy-Item "start.bat" $dist -Force }
if (Test-Path "dist/README-安装说明.md") { Copy-Item "dist/README-安装说明.md" $dist -Force }

# 拷贝 Chromium 到分发目录（保持 chrome-win/chrome.exe 结构，供 exe 自动识别）
$msPlaywright = "$env:LOCALAPPDATA\ms-playwright"
$chromiumDir = Get-ChildItem "$msPlaywright\chromium-*" -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
if ($chromiumDir) {
    Write-Host "  拷贝 Chromium -> $dist\chromium" -ForegroundColor Gray
    Copy-Item $chromiumDir.FullName "$dist\chromium" -Recurse -Force
} else {
    Write-Host "[警告] 未找到 Chromium 目录（$msPlaywright），分发包将缺少浏览器" -ForegroundColor Red
}

Write-Host "`n========================================" -ForegroundColor Green
Write-Host " 打包完成！分发目录：$dist" -ForegroundColor Green
Write-Host "  - EasyBrowser.exe：主程序" -ForegroundColor Green
Write-Host "  - start.bat：一键启动器" -ForegroundColor Green
Write-Host "  - chromium/：自带浏览器（exe 自动识别）" -ForegroundColor Green
Write-Host "  - 把整个 easybrowser 文件夹拷给老板即可（零下载、零系统依赖）" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
