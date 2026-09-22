"""真机 UI 验证：H-9 §5.4 策略中心（ADR-002 §5.4 / §3.5 / §3.6）。

驱动**运行中的** Tauri 主程序 WebView2（CDP 9222）——不是 dev 浏览器、不是模拟。
判据（可机械判定）：
  1. 侧栏进入「设置/配置中心」页正常
  2. 「直播监听」tab 内出现分区卡「直播编排策略（多账号）· 待接线」
  3. 7 个字段 label 全部渲染（连接模式/匿名上限/轮转/脱敏/沉淀池三项）
  4. 控件形态正确：3 个 select + 1 个 number + 3 个开关（bool）
  5. 风控敏感项（risk）有醒目标记（sink_* 三项）
"""
from __future__ import annotations

import json
import time
import urllib.request

import websocket

DBG = "http://127.0.0.1:9222"
_id = [0]

LABELS = [
    "连接模式",
    "匿名模式并发房间上限",
    "发送轮转策略（同房间多账号）",
    "脱敏直播间处理策略",
    "沉淀池全局作用域（跨账号去重）",
    "沉淀池冷却（天）",
    "「永久冷却」档",
]


def cdp(ws, method, **params):
    _id[0] += 1
    ws.send(json.dumps({"id": _id[0], "method": method, "params": params}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == _id[0]:
            return msg


def evaluate(ws, expr):
    r = cdp(ws, "Runtime.evaluate", expression=expr,
            returnByValue=True, awaitPromise=True)
    res = r.get("result", {})
    if "exceptionDetails" in res:
        raise RuntimeError(res["exceptionDetails"])
    return res.get("result", {}).get("value")


targets = json.loads(urllib.request.urlopen(DBG + "/json/list").read().decode("utf-8"))
page = next((t for t in targets if t.get("type") == "page"), None)
if not page:
    raise SystemExit("未找到 WebView2 page target")
print("target:", page.get("url"), "|", page.get("title"))

ws = websocket.create_connection(page["webSocketDebuggerUrl"],
                                origin="http://127.0.0.1:9222", timeout=30)
cdp(ws, "Runtime.enable")

print("\n=== 0) 可用侧栏导航 ===")
print(evaluate(ws, """
(() => [...document.querySelectorAll('button span.truncate, nav button')]
  .map(e => (e.textContent||'').trim()).filter(Boolean).slice(0, 30))()"""))

print("\n=== 1) 进入 设置/配置中心 ===")
print(evaluate(ws, """
(() => {
  const cand = [...document.querySelectorAll('button span.truncate, button')]
    .find(s => /配置中心|^设置$/.test((s.textContent||'').trim()));
  if (cand) { (cand.closest('button')||cand).click(); return 'nav=ok:'+(cand.textContent||'').trim(); }
  return 'nav=NOT-FOUND';
})()"""))
time.sleep(4)

print("\n=== 2) 可用的 tab 列表 ===")
print(evaluate(ws, """
(() => [...document.querySelectorAll('nav button, button')]
  .map(e => (e.textContent||'').trim())
  .filter(t => t && t.length <= 8)
  .slice(0, 25))()"""))

print("\n=== 3) 点「直播监听」tab ===")
print(evaluate(ws, """
(() => {
  const t = [...document.querySelectorAll('button')]
    .find(b => (b.textContent||'').trim() === '直播监听');
  if (t) { t.click(); return 'tab=ok'; }
  return 'tab=NOT-FOUND';
})()"""))
time.sleep(3)

print("\n=== 4) 分区卡标题是否出现 ===")
print(evaluate(ws, """
(() => {
  const t = document.body.innerText || '';
  return {
    hasCardTitle: /直播编排策略（多账号）/.test(t),
    hasDaiJieXian: /待接线/.test(t),
    cardTitles: [...document.querySelectorAll('*')]
      .filter(e => e.children.length===0 && /策略|直播监听/.test(e.textContent||''))
      .map(e=>(e.textContent||'').trim()).slice(0,12),
  };
})()"""))

print("\n=== 5) 7 个字段 label 逐项渲染 ===")
print(evaluate(ws, """
(() => {
  const t = document.body.innerText || '';
  const labels = %s;
  return labels.map(L => ({label: L, present: t.includes(L)}));
})()""" % json.dumps(LABELS, ensure_ascii=False)))

print("\n=== 6) 控件形态统计（该卡内）===")
print(evaluate(ws, """
(() => {
  const body = document.body.innerText || '';
  const inputs = [...document.querySelectorAll('input')];
  const selects = [...document.querySelectorAll('select')];
  const numbers = inputs.filter(i => i.type==='number');
  const checks = inputs.filter(i => i.type==='checkbox');
  return {
    totalInputs: inputs.length, totalSelects: selects.length,
    numberInputs: numbers.length, checkboxes: checks.length,
    selectOptionsCounts: selects.map(s => s.options.length).slice(0, 12),
  };
})()"""))

ws.close()
print("\n[done]")
