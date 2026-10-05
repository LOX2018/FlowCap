# -*- coding: utf-8 -*-
"""CDP 实测：采集悬浮窗三个下拉能否点击（★ 2026-10-03）。

## ⚠️ 前置条件（三条，缺一即白跑 —— 我实测踩了 3 次）

1. **必须先起后端**：`C:/temp/flowcap_design/flowcap-backend-*.exe`
   （否则页面 `ERR_CONNECTION_REFUSED`）。
2. **必须先起应用本体**（`川流_*.exe`）并完成登录 —— 悬浮窗与页面数据都
   依赖已登录会话；未登录只会看到登录页，探针报 `count: 0`。
3. **Edge 启动必须带** `--remote-allow-origins=*`，
   否则 WebSocket 握手被拒（实测 403）：
   `msedge.exe --remote-debugging-port=9333 --remote-allow-origins=* \
        --user-data-dir=<临时目录>`
   ⚠️ `--user-data-dir` **必须用一次性目录**：复用真实 profile 会带上
   已登录 cookie（可省步骤 2），但也可能污染用户自己的浏览器数据。

## 为什么不用 browser 工具

实测 `browser.use_real_profile` 指向的 brave profile 不存在，且本项目历史上
「每次启动浏览器都卡死」⇒ 改用 **CDP 直连 Edge**（无插件、无 profile 依赖）。

## 判据（机械、可复跑）

对每个 `[role=combobox]`：
  ① 元素存在且 **未被 disabled**；
  ② **命中测试**：`document.elementFromPoint(中心点)` 必须落在该元素或其后代
     —— 这是「点了没反应」的决定性证据（被遮挡/被 overlay 盖住都会红）；
  ③ 真实派发点击后，`[role=listbox]` 是否出现。

## 2026-10-03 实测结论（未跑通，如实记录）

三条路径都失败，**下拉定位修复未获实机证据**：
  A. `browser` 工具 → brave profile 缺失；
  B. vite dev（:5199）→ origin 与后端不同 ⇒ 停在唤醒/登录页；
  C. CDP + Edge → 能连上，但拿不到已登录页面（`count: 0`）。
⇒ 该修复目前的证据是「根因推理 + test_crawl_panel_fixes 门禁」，
  **需部署后由用户确认**。本脚本留存，供环境可用时复跑。
"""
import json
import time
import urllib.request


def http(path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:9333{path}", timeout=8) as r:
        return json.loads(r.read().decode())


def targets():
    return http("/json/list")


def new_page(url: str) -> str:
    r = urllib.request.Request(
        "http://127.0.0.1:9333/json/new?" + url,
        method="PUT")
    with urllib.request.urlopen(r, timeout=10) as resp:
        return json.loads(resp.read().decode())["webSocketDebuggerUrl"]


class CDP:
    def __init__(self, ws_url: str):
        import websocket  # websocket-client
        self.ws = websocket.create_connection(ws_url, timeout=20)
        self._id = 0

    def send(self, method: str, **params):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method,
                                 "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr: str):
        r = self.send("Runtime.evaluate", expression=expr,
                      returnByValue=True, awaitPromise=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(r["exceptionDetails"].get("text"))
        return r.get("result", {}).get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


PROBE = r"""
(() => {
  const out = [];
  const boxes = document.querySelectorAll('[role="combobox"]');
  boxes.forEach((el, i) => {
    const r = el.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const hit = document.elementFromPoint(cx, cy);
    out.push({
      i,
      id: el.id || '',
      text: (el.innerText || '').trim().slice(0, 24),
      disabled: !!el.disabled,
      ariaDisabled: el.getAttribute('aria-disabled'),
      rect: { x: Math.round(r.left), y: Math.round(r.top),
              w: Math.round(r.width), h: Math.round(r.height) },
      inViewport: r.width > 0 && r.height > 0
                   && r.top >= 0 && r.bottom <= innerHeight,
      // 🔴 命中测试：中心点最上层是谁
      hitTag: hit ? hit.tagName : null,
      hitRole: hit ? hit.getAttribute('role') : null,
      hitId: hit ? (hit.id || '') : null,
      hitIsSelfOrChild: hit ? (el === hit || el.contains(hit)) : false,
      // 祖先里有没有 pointer-events:none
      blockedByAncestor: (() => {
        let n = el.parentElement;
        while (n && n !== document.body) {
          const cs = getComputedStyle(n);
          if (cs.pointerEvents === 'none') return n.className || n.tagName;
          n = n.parentElement;
        }
        return null;
      })(),
    });
  });
  return { url: location.pathname, count: boxes.length, boxes: out };
})()
"""


def main():
    url = "http://127.0.0.1:5199/platform"
    ws_url = new_page(url)
    c = CDP(ws_url)
    c.send("Page.enable")
    c.send("Runtime.enable")
    time.sleep(6)          # 等 React 挂载 + 数据

    # 🔴 悬浮窗是**点入口才出现**的（默认不在 DOM 里）⇒ 必须先打开。
    #   入口按 data-od-id / 文案「采集」双通道找，找不到就报读数不猜。
    opened = c.js("""(() => {
      const tries = [
        () => document.querySelector('[data-od-id*="crawl"]'),
        () => Array.from(document.querySelectorAll('button')).find(b => /采集/.test(b.innerText||'')),
      ];
      for (const f of tries) {
        const el = f();
        if (el) { el.click(); return el.tagName + ':' + (el.innerText||'').slice(0,20); }
      }
      return null;
    })()""")
    print("=== 打开悬浮窗 ===")
    print("  入口点击:", opened)
    time.sleep(1.5)
    print("  panel in DOM:", c.js("!!document.querySelector('[data-od-id=\"crawl-floating-panel\"]')"))
    print("  panel 可见:", c.js("""(() => { const p=document.querySelector('[data-od-id="crawl-floating-panel"]');
        if(!p) return false; const r=p.getBoundingClientRect();
        return r.width>0 && r.height>0 && getComputedStyle(p).display!=='none'; })()"""))

    print("=== 首屏 combobox 总览 ===")
    info = c.js(PROBE)
    print(json.dumps(info, ensure_ascii=False, indent=2)[:3000])

    print("\n=== 逐个真实点击，看 listbox 是否弹出 ===")
    n = info.get("count", 0)
    for i in range(n):
        # 先滚动到可见
        c.js(f"""(() => {{
          const el = document.querySelectorAll('[role="combobox"]')[{i}];
          if (el) el.scrollIntoView({{block:'center'}});
        }})()""")
        time.sleep(0.4)
        before = c.js("!!document.querySelector('[role=listbox]')")
        c.js(f"""(() => {{
          const el = document.querySelectorAll('[role="combobox"]')[{i}];
          if (!el) return 'missing';
          const r = el.getBoundingClientRect();
          const cx = r.left + r.width/2, cy = r.top + r.height/2;
          for (const t of ['pointerdown','mousedown','pointerup','mouseup','click']) {{
            el.dispatchEvent(new MouseEvent(t, {{bubbles:true, cancelable:true, view:window,
                                              clientX:cx, clientY:cy}}));
          }}
          return 'sent';
        }})()""")
        time.sleep(0.8)
        after = c.js("!!document.querySelector('[role=listbox]')")
        opts = c.js("""(() => Array.from(
            document.querySelectorAll('[role=listbox] [role=option]'))
            .map(o => (o.innerText||'').trim()).slice(0,6))()""")
        print(f"  combobox[{i}]: listbox 弹出 = {after}  选项 = {opts}")
        if after:
            c.js("document.body.click()")
            time.sleep(0.3)
    c.close()


if __name__ == "__main__":
    main()
