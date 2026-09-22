"""真机 UI 验证：H-10 直播间管理页（ADR-003 §5 第 6 条）。

驱动**运行中的** Tauri 主程序的 WebView2（CDP 9222）——不是 dev 浏览器、不是模拟。
判据（可机械判定）：
  1. 「直播间管理」按钮与「管理策略」并排存在（同容器、同 toolbar）
  2. 点击按钮 → 弹窗 data-od-id="live-room-modal" 挂载
  3. 弹窗内四个控件齐备（链接/备注/策略绑定/脱敏开关）
  4. 已登记房间（迁移来的 992931212705）在列表中渲染出「房间号 + 备注 + 策略名」
  5. 脱敏文案含「仅统计」「房间归属」（诚实标注未被简化掉）
  6. 样式真渲染：弹窗有非零尺寸 + 圆角（不是被 CSS 隐藏）
"""
from __future__ import annotations

import json
import time

import websocket

DBG = "http://127.0.0.1:9222"
_id = [0]


def cdp(ws, method, **params):
    _id[0] += 1
    ws.send(json.dumps({"id": _id[0], "method": method, "params": params}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == _id[0]:
            return msg


def evaluate(ws, expr):
    r = cdp(ws, "Runtime.evaluate",
            expression=expr, returnByValue=True, awaitPromise=True)
    res = r.get("result", {})
    if "exceptionDetails" in res:
        raise RuntimeError(res["exceptionDetails"])
    return res.get("result", {}).get("value")


targets = json.loads(
    __import__("urllib.request", fromlist=["urlopen"])
    .urlopen(DBG + "/json/list").read().decode("utf-8"))
page = next((t for t in targets if t.get("type") == "page"), None)
if not page:
    raise SystemExit("未找到 WebView2 page target —— 主程序没开 remote debugging？")
print("target:", page.get("url"), "|", page.get("title"))

ws = websocket.create_connection(page["webSocketDebuggerUrl"],
                                origin="http://127.0.0.1:9222", timeout=30)
cdp(ws, "Runtime.enable")

# 切到「直播监听」页（若不在）—— 点侧栏 span 文本恰为「直播」的按钮
evaluate(ws, """
(() => {
  const e = [...document.querySelectorAll('button span.truncate')]
    .find(s => (s.textContent||'').trim() === '直播');
  if (e) { (e.closest('button')||e).click(); return 'nav-live'; }
  return 'no-nav';
})()""")
time.sleep(4)

print("\n=== 1) 两个入口按钮 ===")
print(evaluate(ws, """
(() => {
  const q = s => document.querySelector('[data-od-id="'+s+'"]');
  const cfg = q('live-room-configs'), reg = q('live-room-registry');
  if (!cfg || !reg) return {cfg: !!cfg, reg: !!reg};
  const pc = cfg.parentElement, pr = reg.parentElement;
  const rc = cfg.getBoundingClientRect(), rr = reg.getBoundingClientRect();
  return {
    cfgText: cfg.textContent.trim(), regText: reg.textContent.trim(),
    sameParent: pc === pr,
    gapX: Math.round(rr.left - rc.right),
    bothVisible: rc.width>0 && rr.width>0,
  };
})()"""))

print("\n=== 2) 点击「直播间管理」→ 弹窗挂载 ===")
print(evaluate(ws, """
(() => {
  const b = document.querySelector('[data-od-id="live-room-registry"]');
  if (!b) return 'no-button';
  b.click();
  return 'clicked';
})()"""))
time.sleep(2)
print(evaluate(ws, """
(() => {
  const m = document.querySelector('[data-od-id="live-room-modal"]');
  if (!m) return {mounted:false};
  const r = m.getBoundingClientRect();
  const st = getComputedStyle(m);
  return {mounted:true, w:Math.round(r.width), h:Math.round(r.height),
          radius:st.borderRadius, display:st.display};
})()"""))

print("\n=== 3) 四个控件齐备 ===")
print(evaluate(ws, """
(() => {
  const has = s => !!document.querySelector('[data-od-id="'+s+'"]');
  return {live_url:has('room-live-url'), name:has('room-name'),
          strategy:has('room-strategy'), desensitized:has('room-desensitized'),
          draftRow:has('room-draft-row')};
})()"""))

print("\n=== 4) 已登记房间在列表渲染 ===")
print(evaluate(ws, """
(() => {
  const rows = [...document.querySelectorAll('[data-od-id^="room-row-"]')];
  return rows.map(r => ({
    id: r.getAttribute('data-od-id'),
    text: (r.innerText||'').replace(/\\n/g,' | ').slice(0,120),
  }));
})()"""))

print("\n=== 5) 脱敏诚实文案 ===")
print(evaluate(ws, """
(() => {
  const m = document.querySelector('[data-od-id="live-room-modal"]');
  const t = m ? (m.innerText||'') : '';
  return {hasJinTongji: /仅统计/.test(t), hasFangjian: /房间归属/.test(t),
          hasNoNicknameClaim: !/能拿.*昵称|可获取.*昵称/.test(t)};
})()"""))

print("\n=== 6) 迁移入口按钮存在 ===")
print(evaluate(ws, """
(() => ({
  dry: !!document.querySelector('[data-od-id="room-migrate-dry"]'),
  apply: !!document.querySelector('[data-od-id="room-migrate-apply"]'),
}))()"""))

ws.close()
print("\n[done]")
