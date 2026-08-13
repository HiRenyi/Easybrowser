"""企业 OA 适配器（示例）。

能力：
- list_todos: 抓取待办列表（待处理/费控对公/费控对私/待阅），返回 requestid+标题+发起人+时间
- approve  : 审批一个待办。dry_run=true（默认）只探测意见框/提交按钮，不真实提交；
             dry_run=false 才会填意见并提交 —— 请确认后显式传参。

注意：approve 是真实写操作，默认 dry_run 防误操作。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from easybrowser.errors import ToolError

LIST_URL = ("http://oa.company.com/spa/workflow/static/index.html"
            "#/main/workflow/listDoing?tab=0&_key=mine1")

#: 待办列表页提取脚本：每行 requestid/标题/类型/部门/发起人/时间/审批人
EXTRACT_ROWS_JS = r"""() => {
  const rows = document.querySelectorAll('.ant-table-row');
  const out = [];
  for (const r of rows) {
    const a = r.querySelector('a[onclick]');
    const oc = a ? (a.getAttribute('onclick') || '') : '';
    const m = oc.match(/requestid=(-?\d+)/);
    const tds = r.querySelectorAll('td');
    const cells = Array.from(tds).map(td => (td.innerText || '').replace(/\s+/g, ' ').trim());
    const title = cells[1] || '';
    let type = '其他';
    const tm = title.match(/^\[(.*?)\]/);
    if (tm) type = tm[1];
    out.push({
      requestid: m ? m[1] : null,
      title: title,
      type: type,
      category: cells[2] || '',
      requester: cells[3] || '',
      time: cells[4] || '',
      approvers: cells[5] || '',
    });
  }
  return out;
}"""

SWITCH_TAB_JS = r"""(name) => {
  const t = [].slice.call(document.querySelectorAll('.ant-tabs-tab'));
  for (let i = 0; i < t.length; i++) {
    if ((t[i].innerText || '').replace(/\s/g, '') === name) { t[i].click(); return true; }
  }
  return false;
}"""

#: 审批/详情页表单提取：td[class*=etype_2] 标签 / etype_3 值。
#: 单选/多选字段从 .ant-radio-group/.ant-checkbox-group 提取每个选项，用 // 连接（对齐历史格式）
EXTRACT_FORM_JS = r"""() => {
  const r = {};
  const rows = document.querySelectorAll('tr');
  for (const row of rows) {
    const ls = row.querySelectorAll('td[class*=etype_2]');
    const vs = row.querySelectorAll('td[class*=etype_3]');
    for (let j = 0; j < ls.length && j < vs.length; j++) {
      const l = ls[j].innerText.trim();
      if (!l || (l in r)) continue;
      const cell = vs[j];
      const rg = cell.querySelector('.ant-radio-group, .ant-checkbox-group');
      if (rg) {
        const opts = [];
        const labels = rg.querySelectorAll('label');
        for (const lb of labels) {
          const t = (lb.innerText || '').trim();
          if (t && !opts.includes(t)) opts.push(t);
        }
        if (opts.length) { r[l] = opts.join('//'); continue; }
      }
      const v = cell.innerText.trim();
      if (v) r[l] = v;
    }
  }
  return r;
}"""

PROBE_APPROVE_JS = r"""() => {
  const out = {url: location.href, hasCKEditor: typeof CKEDITOR !== 'undefined'};
  if (typeof CKEDITOR !== 'undefined') {
    out.ckeditor_instances = Object.keys(CKEDITOR.instances);
  }
  // 找提交按钮（文本可能带空格如"提 交"；顶部工具栏优先）
  const btns = [];
  const cands = document.querySelectorAll('button, [role=button], a, input[type=button]');
  for (const b of cands) {
    const t = ((b.innerText || b.value || '').replace(/\s+/g, '') || '');
    if (t === '提交') {
      const r = b.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) {
        btns.push({tag: b.tagName, x: Math.round(r.x + r.width / 2),
                   y: Math.round(r.y + r.height / 2), top: r.y < 300});
      }
    }
  }
  out.submit_buttons = btns;
  return out;
}"""


#: 在 frame 里探测附件下载链接（a[href] 指向文件 / download 属性 / attach 特征）
FIND_ATTACH_JS = r"""() => {
  const out = {};
  const as = document.querySelectorAll('a');
  for (const a of as) {
    const href = a.getAttribute('href') || '';
    const oc = a.getAttribute('onclick') || '';
    const dd = a.getAttribute('download') || '';
    const text = (a.innerText || '').trim();
    const isFile = /\.(xls|xlsx|docx?|pdf|rar|zip|txt)/i.test(href + ' ' + dd + ' ' + oc);
    const isLink = /download|attach|fileid|DownLoad|UploadFile/i.test(href + ' ' + oc);
    if (text && text.length < 80 && (isFile || isLink)) {
      if (!(text in out)) {
        out[text.slice(0, 60)] = {href: href.slice(0, 300), onclick: oc.slice(0, 150), download: dd};
      }
    }
  }
  return out;
}"""


async def get_attachment_links(browser, page, params: dict) -> dict:
    """在详情页所有 frame（含 iframe）里探测附件下载链接。"""
    frames = [page.main_frame] + list(page.frames)
    links: dict = {}
    for f in frames:
        try:
            res = await f.evaluate(FIND_ATTACH_JS)
            if isinstance(res, dict):
                for k, v in res.items():
                    links.setdefault(k, v)
        except Exception:
            continue
    return {"frame_count": len(frames), "attachments": links,
            "url": page.url}


#: 劫持 window.open 捕获附件下载 URL（附件点击走 index2file.jsp 下载）
HIJACK_OPEN_JS = r"""(name) => new Promise((resolve) => {
  const orig = window.open;
  let done = false;
  const finish = (u) => { if (!done) { done = true; window.open = orig; resolve(u); } };
  window.open = function (u) { finish(u || ''); return null; };
  const a = document.querySelector('a[title="' + name + '"]');
  if (!a) { finish(''); return; }
  try { a.click(); } catch (e) { finish(''); }
  setTimeout(() => finish(''), 3000);
})"""


async def download_attachment(browser, page, params: dict) -> dict:
    """下载附件到 ~/.easybrowser/downloads/attachments/。

    流程：详情表单页点附件 a[title=name] 劫持 window.open 拿预览 URL
    → 导航到预览页（index.jsp#/main/document/detail）→ 点"下 载"→ save_as。
    """
    from pathlib import Path
    name = str(params.get("name") or "").strip()
    requestid = str(params.get("requestid") or "").strip()
    if not name:
        raise ToolError("缺少必填参数 name（附件名）")

    data_dir = Path.home() / ".easybrowser"
    out_dir = data_dir / "downloads" / "attachments"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / name

    # 1. 确保在详情表单页（附件 a[title] 在此）
    if requestid and ("ViewRequestForward" in page.url or "static4form" not in page.url):
        pass
    if requestid:
        detail_url = (f"http://oa.company.com/workflow/request/"
                      f"ViewRequestForwardSPA.jsp?requestid={requestid}")
        await page.goto(detail_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(4500)

    # 2. 点击附件 a[title=name]，劫持 window.open 拿预览 URL
    preview_url = await page.evaluate(HIJACK_OPEN_JS, name)
    if not preview_url:
        raise ToolError(f"未捕获附件预览 URL：{name}（可能不在当前表单页）")

    # 3. 导航预览页（index2file.jsp 会 JS 跳转 index.jsp 预览页）
    await page.goto(_abs_url(page.url, preview_url), wait_until="domcontentloaded")
    await page.wait_for_timeout(4000)

    # 4. 点"下 载"按钮，Playwright 捕获真实文件
    dl_btn = page.locator('text=下 载').first
    try:
        async with page.expect_download(timeout=30000) as dl_info:
            await dl_btn.click(timeout=10000)
        dl = await dl_info.value
        await dl.save_as(str(out))
    except Exception as e:
        raise ToolError(f"下载失败（点击下载未产生文件）: {e}")

    return {"name": name, "bytes": out.stat().st_size if out.exists() else 0,
            "saved": str(out)}


def _abs_url(base: str, rel: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base, rel)


#: RAT 入库：单选选项字段（取第一个值）vs 文本字段（原样）
RAT_SINGLE_OPT = ["需求是否接纳", "是否分派成员评估", "数据中台处理",
                  "信息系统", "立项情况", "流程流转建议", "标注",
                  "是否包含个人金融信息"]
RAT_TEXT_FIELDS = ["需求编号", "需求状态", "需求简述", "需求详细描述",
                   "需求提出人", "需求所属部门", "需求提出时间", "期望上线时间",
                   "需求归属", "RAT负责人"]


def _clean_radio(v):
    """对齐原项目 extract_rat.py：单选选项字段把 \\t\\n 替换为 /（存全选项）。"""
    if not v:
        return ""
    return str(v).replace("\t", "/").replace("\n", "/").strip("/")


async def rat_ingest(browser, page, params: dict) -> dict:
    """RAT 需求入库：提取详情字段写入 rat_tasks.db（request_id UNIQUE 去重）。"""
    requestid = str(params.get("requestid") or "").strip()
    if not requestid:
        raise ToolError("缺少必填参数 requestid")
    db_path = (params.get("db_path") or os.environ.get("RAT_DB")
               or str(Path.home() / ".easybrowser" / "rat_tasks.db"))

    detail = await get_detail(browser, page, {"requestid": requestid})
    fields = detail.get("fields", {})
    row = {"request_id": requestid}
    for k in RAT_TEXT_FIELDS:
        row[k] = str(fields.get(k, "")).strip()
    for k in RAT_SINGLE_OPT:
        row[k] = _clean_radio(fields.get(k, ""))
    row["所属业务域"] = ""
    row["raw_fields"] = json.dumps(fields, ensure_ascii=False)

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """INSERT OR REPLACE INTO rat_tasks
            (request_id, 需求编号, 需求状态, 需求简述, 需求详细描述, 需求提出人,
             需求所属部门, 需求提出时间, 期望上线时间, 需求是否接纳,
             是否分派成员评估, 数据中台处理, 需求归属, 信息系统, 立项情况,
             流程流转建议, 标注, 是否包含个人金融信息, RAT负责人, 所属业务域,
             raw_fields, closed)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
            (row["request_id"], row["需求编号"], row["需求状态"], row["需求简述"],
             row["需求详细描述"], row["需求提出人"], row["需求所属部门"],
             row["需求提出时间"], row["期望上线时间"], row["需求是否接纳"],
             row["是否分派成员评估"], row["数据中台处理"], row["需求归属"],
             row["信息系统"], row["立项情况"], row["流程流转建议"], row["标注"],
             row["是否包含个人金融信息"], row["RAT负责人"], row["所属业务域"],
             row["raw_fields"]))
        conn.commit()
        inserted = cur.rowcount
        total = conn.execute("SELECT COUNT(*) FROM rat_tasks").fetchone()[0]
    finally:
        conn.close()
    return {"requestid": requestid, "需求编号": row["需求编号"],
            "需求状态": row["需求状态"], "db": db_path,
            "inserted": inserted,  # 1=新入库, 0=已存在跳过
            "total_in_db": total}


def _parse_attachments(att_text: str) -> list[dict]:
    """从表单"附件"字段文本解析附件清单：'文件名\\n大小K\\n文件名2\\n16.1K...'。"""
    if not att_text:
        return []
    parts = [p.strip() for p in att_text.split("\n") if p.strip()]
    out = []
    i = 0
    while i < len(parts):
        name = parts[i]
        size = ""
        if i + 1 < len(parts) and re.match(r"^[\d.]+[KMG]?$", parts[i + 1]):
            size = parts[i + 1]
            i += 2
        else:
            i += 1
        # 跳过纯数字/大小串（避免把大小当文件名）
        if re.match(r"^[\d.]+[KMG]?$", name):
            continue
        out.append({"name": name, "size": size})
    return out


async def get_detail(browser, page, params: dict) -> dict:
    requestid = str(params.get("requestid") or "").strip()
    if not requestid:
        raise ToolError("缺少必填参数 requestid")
    url = (f"http://oa.company.com/workflow/request/"
           f"ViewRequestForwardSPA.jsp?requestid={requestid}")
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(5000)  # 等跳转审批页

    fields = await page.evaluate(EXTRACT_FORM_JS)
    # 补：若表单为空（iframe 加载慢）再等一次
    if not fields:
        await page.wait_for_timeout(3000)
        fields = await page.evaluate(EXTRACT_FORM_JS)

    attachments = _parse_attachments(fields.get("附件", ""))
    return {"requestid": requestid, "url": page.url,
            "fields_count": len(fields), "fields": fields,
            "attachments": attachments}


async def list_todos(browser, page, params: dict) -> dict:
    tab = str(params.get("tab") or "待处理")
    limit = int(params.get("limit", 50))
    if "listDoing" not in page.url:
        await page.goto(LIST_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
    if tab and tab != "待处理":
        await page.evaluate(SWITCH_TAB_JS, tab)
        await page.wait_for_timeout(2500)
    rows = await page.evaluate(EXTRACT_ROWS_JS)
    if not rows:
        # 等待一下再试（SPA 渲染）
        await page.wait_for_timeout(2000)
        rows = await page.evaluate(EXTRACT_ROWS_JS)
    return {"tab": tab, "count": len(rows), "todos": rows[:limit],
            "url": page.url}


async def approve(browser, page, params: dict) -> dict:
    requestid = str(params.get("requestid") or "").strip()
    if not requestid:
        raise ValueError("缺少必填参数 requestid（用 list_todos 获取）")
    comment = str(params.get("comment") or "同意，请领导批示")
    dry_run = params.get("dry_run", True)
    if not isinstance(dry_run, bool):
        dry_run = str(dry_run).lower() not in ("0", "false", "no")

    url = (f"http://oa.company.com/workflow/request/"
           f"ViewRequestForwardSPA.jsp?requestid={requestid}")
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(5000)  # 等跳转到审批页（可能跨域）

    probe = await page.evaluate(PROBE_APPROVE_JS)

    if dry_run:
        return {
            "dry_run": True,
            "requestid": requestid,
            "comment": comment,
            "url": probe["url"],
            "ckeditor_instances": probe.get("ckeditor_instances", []),
            "submit_buttons": probe.get("submit_buttons", []),
            "hint": "dry_run=true 未执行任何提交。确认无误后传 dry_run=false 真实审批。",
        }

    # ---- 真实提交（需显式 dry_run=false）----
    # 1. 填意见到 CKEditor
    if probe.get("ckeditor_instances"):
        ck = probe["ckeditor_instances"][0] if "wea_rich_text_remark" not in probe["ckeditor_instances"] \
            else "wea_rich_text_remark"
        await page.evaluate(
            "(args) => { const [name, val] = args; CKEDITOR.instances[name].setData(val); }",
            [ck, comment])
        await page.wait_for_timeout(300)

    # 2. 点提交按钮（优先顶部工具栏的"提交"）
    btns = probe.get("submit_buttons") or []
    submit = next((b for b in btns if b.get("top")), None) or (btns[0] if btns else None)
    if not submit:
        return {"ok": False, "requestid": requestid,
                "error": "未找到提交按钮（dry_run 探测过，请人工确认页面）"}
    await page.mouse.click(submit["x"], submit["y"])
    await page.wait_for_timeout(3000)

    # 3. 验证：URL 出现 isRefresh 或按钮消失
    final = await page.evaluate("location.href")
    return {"ok": True, "requestid": requestid, "comment": comment,
            "submitted_at": submit, "final_url": final[:120],
            "verify": "提交按钮已点击；请确认页面出现'操作成功'或跳转"}


ADAPTER = {
    "name": "oa",
    "title": "企业OA（示例）",
    "match_domains": ["oa.company.com"],
    "capabilities": {
        "list_todos": {
            "description": "抓取 OA 待办列表（tab: 待处理/费控对公/费控对私/待阅），返回 requestid+标题+发起人+时间。只读。",
            "schema": {"tab": "string 默认待处理", "limit": "int 默认50"},
            "handler": list_todos,
        },
        "get_detail": {
            "description": "获取审批单详情：表单字段（需求描述/预算/时间/方案/附件清单等）+ 附件列表。只读。用于立项评估。",
            "schema": {"requestid": "必填"},
            "handler": get_detail,
        },
        "get_attachment_links": {
            "description": "探测详情页所有 frame 里的附件下载链接（返回 附件名->下载URL）。",
            "schema": {},
            "handler": get_attachment_links,
        },
        "download_attachment": {
            "description": "下载指定附件到 ~/.easybrowser/downloads/attachments/（走同源带登录态）。",
            "schema": {"name": "必填，附件名（如 TDP一期设备服务.xls）"},
            "handler": download_attachment,
        },
        "rat_ingest": {
            "description": "RAT 需求入库：提取详情字段写入 rat_tasks.db（request_id 去重）。db 可用 env RAT_DB 或 db_path 覆盖。",
            "schema": {"requestid": "必填", "db_path": "可选，sqlite 路径（默认 ~/.easybrowser/rat_tasks.db）"},
            "handler": rat_ingest,
        },
        "approve": {
            "description": "审批一个待办。dry_run=true(默认)只探测意见框/提交按钮；dry_run=false 填意见并提交（真实操作！）。",
            "schema": {"requestid": "必填", "comment": "意见文本 默认'同意，请领导批示'",
                       "dry_run": "bool 默认 true"},
            "handler": approve,
        },
    },
}
