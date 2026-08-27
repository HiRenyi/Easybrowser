# EasyBrowser

浏览器自动化本地 HTTP API —— Playwright 托管，52 个浏览器操作工具，录制方式 + 廉价模型实现浏览器自动化。

用一条 `curl` 或一次脚本重放，就能驱动真实浏览器完成 OA 表单填写、待办审批、报表提交等日常工作，天然支持 **cron 定时调度**。

## 架构

```
┌─────────────────────────────────────────────┐
│  EasyBrowser 服务 (FastAPI, 默认 127.0.0.1:58086) │
│                                             │
│  GET  /api/tools         工具清单(52个)       │
│  POST /api/tool/{name}   调用单个工具         │
│  POST /api/batch         顺序执行步骤         │
│  POST /api/record/*      录制/重放/轨迹       │
│  GET  /health            健康检查             │
│  GET  /docs              OpenAPI 文档        │
│                                             │
│  BrowserManager (Playwright)                │
│   ├── cdp 模式: 连接已启动 Chrome 复用登录态    │
│   └── launch 模式: 自启动独立浏览器            │
└─────────────────────────────────────────────┘
```

## 快速开始

### 前置

- Python 3.10+
- Chrome / Chromium

```bash
pip install -e .
playwright install chromium   # 或用系统 Chrome（见 launch 模式）
```

### 模式 A：连接已启动的 Chrome（复用登录态，推荐日常使用）

```bash
# 1. 启动 Chrome（带远程调试端口，登录你的 OA/各平台）
google-chrome --remote-debugging-port=9222 --user-data-dir=/path/to/profile &

# 2. 启动 EasyBrowser
easybrowser serve --mode cdp --cdp-url http://127.0.0.1:9222 --port 58086
```

### 模式 B：自启动独立浏览器

```bash
easybrowser serve --mode launch --headless --executable-path /usr/bin/google-chrome
```

### 模式 C：一次性执行脚本（cron 友好）

```bash
easybrowser run my_script.json --mode cdp        # 连常驻 Chrome 复用登录态
easybrowser run my_script.json --mode launch --headless
# exit code：0=全部成功，1=有步骤失败，2=用法/脚本格式错误
```

## API 速查

```bash
BASE=http://127.0.0.1:58086

# 列出全部工具
curl -s $BASE/api/tools

# 调用工具（参数见工具 schema）
curl -s -X POST $BASE/api/tool/browser_navigate -d '{"url":"https://example.com"}'
curl -s -X POST $BASE/api/tool/browser_snapshot_ax -d '{"mode":"compact"}'
curl -s -X POST $BASE/api/tool/browser_click     -d '{"ref":"e12"}'
curl -s -X POST $BASE/api/tool/browser_fill      -d '{"ref":"e7","value":"张三"}'

# 批量顺序执行
curl -s -X POST $BASE/api/batch -d '{
  "steps": [
    {"tool":"browser_navigate","params":{"url":"http://oa.example.com"}},
    {"tool":"browser_wait_for_element","params":{"selector":".ant-table-row","state":"visible"}},
    {"tool":"browser_evaluate_js","params":{"expression":"document.title"}}
  ]
}'
```

响应格式：

```json
{"ok": true, "result": "文本结果", "is_error": false, "duration_ms": 12, "data": {}}
```

## 52 个工具

分层对齐原 EasyBrowser 设计，工具名与参数兼容经典版：

| 层 | 工具 |
|----|------|
| 感知 11 | `browser_snapshot_ax`（带 ref 的 AX 树）`browser_snapshot` `browser_snapshot_visible` `browser_screenshot` `browser_get_text` `browser_get_attribute` `browser_get_url` `browser_get_title` `browser_is_visible` `browser_is_enabled` `browser_count` |
| DOM 操作 11 | `browser_click` `browser_double_click` `browser_fill` `browser_type` `browser_press_key` `browser_select_option` `browser_set_checked` `browser_scroll` `browser_hover` `browser_drag` `browser_file_upload` |
| 坐标 8 | `browser_click_node` `browser_click_at` `browser_double_click_at` `browser_move_mouse` `browser_scroll_at` `browser_drag_path` `browser_type_at` `browser_press_key_combo` |
| JS 兜底 2 | `browser_js_click`（触发 React 合成事件）`browser_js_fill` |
| 导航 5 | `browser_navigate` `browser_go_back` `browser_go_forward` `browser_reload` `browser_new_tab` |
| Tab 5 | `browser_list_tabs` `browser_switch_tab` `browser_select_tab` `browser_adopt_tab` `browser_close_tab` |
| 等待 3 | `browser_wait_for_element` `browser_wait_for_url` `browser_wait_for_timeout` |
| 其他 7 | `browser_evaluate_js` `browser_cdp_call` `browser_clipboard` `browser_console_logs` `browser_download_media` `browser_name_session` `browser_finish_session` |

### ref 体系（廉价模型友好）

`browser_snapshot_ax` 返回可访问性树，交互元素带稳定 ref：

```
- button "提交" [ref=e12]
- link "我的待办" [ref=e3]
```

廉价模型只需从 ref 清单里选择「点哪个/填什么」，复杂的定位、坐标、点击、验证都由工具完成——不依赖强模型的推理能力。

## 录制 → 重放 → cron

录制页面操作生成可重放脚本，配合 cron 实现日常自动化。

```bash
# 1. 开始录制（capture_user=true 时注入脚本捕获真实用户点击/输入）
curl -s -X POST $BASE/api/record/start -d '{"capture_user":true}'

# 2. AI 驱动的操作也会自动记录（每次 /api/tool/* 调用）
# 3. 停止录制，得到脚本
curl -s -X POST $BASE/api/record/stop -d '{"record_id":1}'
# → {"steps":[{"tool":"browser_click","params":{"ref":"e12"}}, ...]}

# 4. 重放
curl -s -X POST $BASE/api/record/replay -d '{"record_id":1}'

# 5. 存成脚本文件，cron 每天执行
curl -s $BASE/api/record/1 > ~/tasks/daily_approve.json
# crontab:
# 30 9 * * * easybrowser run ~/tasks/daily_approve.json --mode cdp
```

录制轨迹（`GET /api/record/{id}`）包含每步的工具/参数/URL 与周期截图证据帧，便于核对自动化结果。

### 从录制到稳定执行的 RPA 流水线

录制得到的是「原始轨迹」。让它稳定执行、最终脱离 AI，推荐两步：

1. **AI 重写优化脚本**：把录制脚本交给 Claude 等模型重写成更健壮的版本——
   - 用**稳定 selector** 替换临时 `ref`（页面变化后 ref 会失效）
   - 增加**显式等待**（`browser_wait_for_element` / `browser_wait_for_url`）
   - 增加**结果验证**（`evaluate_js` 断言、截图核对）
   - 处理元素缺失等分支
2. **稳定执行**：优化后的脚本就是 RPA 步骤文件，cron 调 `easybrowser run script.json` 稳定执行、无需 AI；也可由子 Agent 按需触发。

脚本是纯 JSON（`{"version":1, "steps":[{"tool","params"}]}`），AI 极易生成和改写，EasyBrowser 只负责稳定执行。

### 安全

- 录制用户输入时，`type=password` 的值自动掩码为 `[PASSWORD_MASKED]`，不落盘明文。
- 默认只绑定 `127.0.0.1`，请勿对外暴露。

## Tab 管理

- **稳定 tab_id**：自增整数 registry + CDP targetId，tab 顺序变化不会错乱。
- **new_tab 自动激活**：新开标签页立即成为操作目标，无需手动 `switch_tab`。
- **关闭活动页不断连**：`page.close()` 由 Playwright 正确处理，无需重启服务。
- **跨 tab 并发**：per-page 写锁串行化同页写操作，不同页可并行。
- **`target=_blank` 自动登记**：点击网页里新开标签页的链接，自动纳入管理。

## 再抽象一层：结构化提取 + 站点适配器

52 工具是「浏览器指令」，而日常任务往往需要「数据」或「固定操作」。往上抽象两层：

### 1. 结构化数据提取器 `POST /api/extract`

声明式规则，替代手写 fetch JS。两种来源：

```bash
# 从已加载页面提取 DOM 元素
curl -X POST $BASE/api/extract -d '{
  "from":"page",
  "items_selector":".wbpro-scroller-item",
  "fields":{"text":".wbpro-feed-ogText","href":"a@href"},
  "limit":20
}'

# 同源接口提取（凭登录态，等同浏览器正常请求）
curl -X POST $BASE/api/extract -d '{
  "from":"api",
  "api_url":"/ajax/statuses/mymblog?uid=2606218210&feature=0&page=1",
  "rules":{"items_path":"data.list","fields":{"date":".created_at","text":".text_raw"}}
}'
```

### 2. 站点适配器 `GET /api/adapters` + `POST /api/adapter/{name}/{cap}`

按站点组织的「能力」集合，把固定操作封装成可调用能力：

```bash
# 列出所有适配器能力
curl -s $BASE/api/adapters

# 微博：抓某账号最近 N 天微博
curl -X POST $BASE/api/adapter/weibo/get_user_posts \
  -d '{"uid":"2606218210","days":30}'
```

已内置适配器：`weibo`（get_user_posts）。

新增适配器：在 `easybrowser/adapters/` 下建 `<name>.py`，定义 `ADAPTER` dict（名称/匹配域名/能力清单），自动加载：

```python
ADAPTER = {
    "name": "my_site", "title": "某站点",
    "match_domains": ["example.com"],
    "capabilities": {
        "do_something": {
            "description": "...",
            "schema": {...},
            "handler": async def fn(browser, page, params): ...
        },
    },
}
```

这三层的关系：**52 工具（浏览器指令）→ extract/适配器（业务能力）→ 录制脚本 / cron（稳定执行）**。业务逻辑从散落脚本变成 easybrowser 原生管理的能力，AI 和用户都按名调用。

## Windows 打包分发（给非技术用户免安装）

把 EasyBrowser 打成**免安装分发包**：exe + 全部 Python 依赖 + 自带 Chromium 浏览器，老板**整个文件夹拷走即用、零下载、零系统依赖**。

```bash
# 1. 在 Windows 机器上（PyInstaller 不能交叉编译，须在 Windows 打）
#    项目根执行：
powershell -ExecutionPolicy Bypass -File build_windows.ps1

# 2. 产物：dist/easybrowser/
#    EasyBrowser.exe   （已含全部依赖，双击即启动）
#    start.bat         （一键启动器）
#    chromium/         （自带浏览器，exe 自动识别）
#    README-安装说明.md

# 3. 把整个 easybrowser/ 文件夹拷给用户 → 双击 start.bat → 登录一次即可
```

- `build_windows.ps1`：自动装 PyInstaller → 下载 Chromium（走 npmmirror 镜像）→ 打 exe → 整理分发包
- `easybrowser.spec`：PyInstaller 配置（collect playwright 驱动 + uvicorn/fastapi hidden imports）
- 浏览器识别顺序：exe 旁自带 Chromium → 系统 Edge（Windows）/Chrome → 兜底
- 详见 `dist/README-安装说明.md`

## 开发与测试

```bash
pip install -e ".[dev]"
pytest tests/ -q          # 30 个测试（tab 管理 / 工具 / API / 录制）
```

## 开源

MIT License。核心依赖：FastAPI + Playwright + Pydantic。

## 参考

- 录制脚本格式：`{"version":1, "steps":[{"tool","params"}]}`，可直接喂给 `/api/batch` 或 `easybrowser run`
