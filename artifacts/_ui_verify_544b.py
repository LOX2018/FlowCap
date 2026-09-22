"""§5.4 补充真机验收 v2（精确选择器，读控件实现后重写）。

判据：
  A. risk 三项（sink_*）带 .is-risk + ⚠；非 risk 四项不带
  B. 通过 UI 改「沉淀池冷却（天）」=45 → 该字段 .is-dirty 出现 → 点同卡「保存此分组」
     → 后端真值变 45.0（证明 UI 保存路径端到端生效，非假成功）
  C. 再改回 90 并保存 → 后端回 90.0（无残留副作用）
"""
from __future__ import annotations

import json
import time
import urllib.request

import websocket

DBG = "http://127.0.0.1:9222"
API = "http://127.0.0.1:8000"
_id = [0]


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


def backend_cfg():
    with open(r"C:\temp\dyautodm_design\members\.session.json", encoding="utf-8") as f:
        tok = json.load(f)["token"]
    req = urllib.request.Request(API + "/api/settings", headers={"X-Member-Token": tok})
    return json.loads(urllib.request.urlopen(req).read().decode("utf-8"))["config"]["live_orchestration"]


targets = json.loads(urllib.request.urlopen(DBG + "/json/list").read().decode("utf-8"))
page = next(t for t in targets if t.get("type") == "page")
ws = websocket.create_connection(page["webSocketDebuggerUrl"],
                                origin="http://127.0.0.1:9222", timeout=30)
cdp(ws, "Runtime.enable")

print("=== 前态（后端）===", json.dumps(backend_cfg(), ensure_ascii=False))

print("\n=== A) risk 标记逐字段核对 ===")
print(evaluate(ws, """
(() => {
  const t = document.body.innerText || '';
  return [...document.querySelectorAll('.set-field')].map(f => ({
    label: (f.querySelector('span')?.textContent || '').replace(/[●⚠]/g,'').trim().slice(0,28),
    isRisk: f.classList.contains('is-risk'),
    warnMark: /⚠/.test(f.textContent || ''),
  }));
})()"""))

print("\n=== B1) 改「沉淀池冷却（天）」= 45 ===")
print(evaluate(ws, """
(() => {
  const f = [...document.querySelectorAll('.set-field')]
    .find(e => (e.textContent||'').includes('沉淀池冷却（天）'));
  if (!f) return 'field-not-found';
  const inp = f.querySelector('input[type=number]');
  if (!inp) return 'input-not-found';
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
  setter.call(inp, '45');
  inp.dispatchEvent(new Event('input', {bubbles:true}));
  return {before: inp.value, nowTouched: true};
})()"""))
time.sleep(1)

print("B2) dirty 标记 + 点同卡保存:")
print(evaluate(ws, """
(() => {
  const f = [...document.querySelectorAll('.set-field')]
    .find(e => (e.textContent||'').includes('沉淀池冷却（天）'));
  if (!f) return 'field-not-found';
  const dirty = f.classList.contains('is-dirty');
  // 向上找含「保存此分组」按钮的那个卡片容器
  let card = f;
  let btn = null;
  while (card && !btn) {
    card = card.parentElement;
    if (!card) break;
    btn = [...card.querySelectorAll('button')]
      .find(b => (b.textContent||'').trim() === '保存此分组') || null;
  }
  if (!btn) return {dirty, saveButton: 'not-found'};
  const disabled = btn.disabled;
  btn.click();
  return {dirty, saveButton:'clicked', wasDisabled: disabled};
})()"""))
time.sleep(3)
cfg = backend_cfg()
print("B3) 后端真值 =", json.dumps(cfg, ensure_ascii=False))
print(">>> sink_cooldown_days == 45.0 ?", cfg["sink_cooldown_days"] == 45.0)

print("\n=== C) 改回 90 并保存 ===")
print(evaluate(ws, """
(() => {
  const f = [...document.querySelectorAll('.set-field')]
    .find(e => (e.textContent||'').includes('沉淀池冷却（天）'));
  if (!f) return 'field-not-found';
  const inp = f.querySelector('input[type=number]');
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
  setter.call(inp, '90');
  inp.dispatchEvent(new Event('input', {bubbles:true}));
  let card = f, btn = null;
  while (card && !btn) {
    card = card.parentElement; if (!card) break;
    btn = [...card.querySelectorAll('button')]
      .find(b => (b.textContent||'').trim() === '保存此分组') || null;
  }
  if (btn) { btn.click(); return 'saved-back'; }
  return 'save-button-not-found';
})()"""))
time.sleep(3)
cfg2 = backend_cfg()
print("C2) 后端真值 =", json.dumps(cfg2, ensure_ascii=False))
print(">>> 已还原 90.0 ?", cfg2["sink_cooldown_days"] == 90.0)

ws.close()
print("\n[done]")
