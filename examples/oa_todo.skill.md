---
name: easybrowser-oa
description: 用 EasyBrowser HTTP API 自动化 OA 待办处理（示例）。通过本地 HTTP API 驱动已登录的 Chrome 浏览器完成待办查看、审批、填表。依赖 easybrowser 服务（127.0.0.1:58086）。触发词：OA、待办、审批、浏览器。
allowed-tools:
  - Bash
---

# EasyBrowser OA 自动化（示例 Skill）

通过 EasyBrowser 本地 HTTP API（`http://127.0.0.1:58086`）操控已登录的 Chrome 处理 OA 待办。

## 前提

- Chrome 带 `--remote-debugging-port=9222` 启动且已登录 OA
- EasyBrowser 服务运行中：`easybrowser serve --mode cdp --port 58086`
- 启动命令：`~/.venv/bin/python -m easybrowser serve --mode cdp --port 58086`

## API 基础

```bash
BASE=http://127.0.0.1:58086
# 单工具
curl -s -X POST $BASE/api/tool/{tool_name} -H 'Content-Type: application/json' -d '{参数}'
# 批量
curl -s -X POST $BASE/api/batch -H 'Content-Type: application/json' -d '{"steps":[...]}'
# 工具清单
curl -s $BASE/api/tools
```

## 查看待办

```bash
# 导航到待办页
curl -s -X POST $BASE/api/tool/browser_navigate \
  -d '{"url":"http://oa.example.com/spa/workflow/static/index.html#/main/workflow/listDoing"}'

# 等页面加载
curl -s -X POST $BASE/api/tool/browser_wait_for_element \
  -d '{"selector":".ant-table-row","state":"visible","timeout":8000}'

# 提取待办标题
curl -s -X POST $BASE/api/tool/browser_evaluate_js \
  -d '{"expression":"Array.from(document.querySelectorAll(\".ant-table-row td\")).map(td=>td.innerText.trim()).join(\"|\")"}'
```

## 操作表单（ref 定位，廉价模型友好）

```bash
# 拿可访问性快照（交互元素带 ref）
curl -s -X POST $BASE/api/tool/browser_snapshot_ax -d '{"mode":"compact"}'
# → - button "提交" [ref=e12] ...

# 填意见（ref 来自 snapshot_ax）
curl -s -X POST $BASE/api/tool/browser_fill -d '{"ref":"e7","value":"同意，请领导批示"}'

# 点提交
curl -s -X POST $BASE/api/tool/browser_click -d '{"ref":"e12"}'

# 验证（提交后按钮消失）
curl -s -X POST $BASE/api/tool/browser_wait_for_element \
  -d '{"selector":".ant-table-row","state":"detached","timeout":8000}'
```

## 录制 → 重放 → cron 定时

```bash
# 开始录制
curl -s -X POST $BASE/api/record/start -d '{"capture_user":true}'
# ... 你手动或让 AI 操作页面（操作被自动记录成步骤）...
# 停止并拿到脚本
curl -s -X POST $BASE/api/record/stop -d '{"record_id":1}'

# 存为脚本文件，cron 每天执行：
# crontab:
# 30 9 * * * cd ~/easybrowser && .venv/bin/python -m easybrowser run ~/tasks/daily_approve.json --mode cdp
```

`easybrowser run` 执行完自动退出，exit code 0=成功 / 1=有步骤失败，方便 cron 感知。

## 常用工具

| 场景 | 工具 |
|------|------|
| 打开页面 | `browser_navigate` |
| 看页面结构 | `browser_snapshot_ax`（带 ref）/ `browser_snapshot`（带 node_id 和坐标） |
| 截图 | `browser_screenshot`（`inline_base64:true` 返回 data URI） |
| 点击/填表 | `browser_click` / `browser_fill`（ref 或 selector） |
| React 兜底 | `browser_js_click` / `browser_js_fill` |
| 坐标兜底 | `browser_click_at` / `browser_click_node` |
| 跑 JS | `browser_evaluate_js`（返回原生 JSON，无 double-encoding） |
| Tab | `browser_list_tabs` / `browser_switch_tab` / `browser_new_tab`（新 tab 自动激活） |
| 等待 | `browser_wait_for_element` / `browser_wait_for_url` / `browser_wait_for_timeout` |

## 故障排查

| 现象 | 解决 |
|------|------|
| 服务未启动 | `curl $BASE/health`；用 `easybrowser serve --mode cdp` 启动 |
| 没有标签页 | 先 `browser_new_tab` 或 Chrome 打开普通网页 |
| ref 失效 | 页面变化后重新 `browser_snapshot_ax` |
| Chrome 调试口被占 | 确认只启动一个调试客户端（如有其他 easybrowser 实例需先停） |
| 登录态过期 | 重新扫码登录 Chrome，`keepalive` 脚本可定期保活各平台 session |
