# -*- coding: utf-8 -*-
"""真实实例端到端验证：ADR-018 F1-D1 / F1-D3（2026-09-27）。

## 与单测的区别（为什么还要这一步）

`test_f1_d1_d3.py` 是**直调函数**（`asyncio.run(save_policy(...))`），
证明的是**逻辑正确**。本脚本走**真实 ASGI 实例 + HTTP 客户端**，证明的是
**能力可达**（路由挂了、鉴权在、序列化对、状态真落库）—— 两者不可互替。

## 鉴权处理

后端全局中间件要求 `x-member-token`。**不绕过中间件**（那是自欺），
而是在测试进程内用项目自己的 `member_ctx.create_session` 造一个合法会话
—— 走的是与真实登录**同一条**会话创建路径，只是省去了口令校验。

## 隔离

`DY_APP_ROOT` 指向临时根 ⇒ 不污染真实数据根（本项目的铁律）。
"""
from __future__ import annotations

import os
import sys
import tempfile

_BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BACKEND)
_ROOT = os.path.join(tempfile.gettempdir(), "f1d13_live")
os.makedirs(os.path.join(_ROOT, "members"), exist_ok=True)   # ★ 必须先 mkdir
os.environ["DY_APP_ROOT"] = _ROOT

FAILED = []


def chk(label: str, cond: bool, detail: object = "") -> None:
    print(("  ✅ " if cond else "  ❌ ") + label + (f"  [{detail}]" if detail != "" else ""))
    if not cond:
        FAILED.append(label)


def main() -> int:
    from fastapi.testclient import TestClient
    from services import member_ctx
    import main as app_main

    # 造合法会话（与真实登录同一条 create_session 路径）
    token = member_ctx.create_session("m_verify", "verify_user", "k" * 32)
    H = {"x-member-token": token}
    c = TestClient(app_main.app)

    print("① 鉴权真的在（负控：不带 token 必须 401）")
    r_noauth = c.post("/api/crawl/policies", json={"name": "x"})
    chk("无 token → 401", r_noauth.status_code == 401, r_noauth.status_code)

    print("② 新建采集策略（真实 HTTP POST）")
    r = c.post("/api/crawl/policies", headers=H, json={
        "name": "联调-保守", "kind": "video", "num": 10,
        "sort_type": "2", "publish_time": "7", "max_rounds": 8})
    chk("POST 成功", r.status_code == 200 and r.json().get("ok"), r.status_code)
    pid = r.json().get("id", "")
    chk("返回策略 id", bool(pid), pid)

    print("③ 读回（验证真落库，非内存）")
    r2 = c.get("/api/crawl/policies", headers=H)
    items = r2.json().get("items", [])
    chk("列表含新策略", any(i.get("id") == pid for i in items), f"total={r2.json().get('total')}")
    one = next((i for i in items if i.get("id") == pid), {})
    chk("字段无损读回", one.get("num") == 10 and one.get("sort_type") == "2"
        and one.get("publish_time") == "7" and one.get("max_rounds") == 8,
        {k: one.get(k) for k in ("num", "sort_type", "publish_time", "max_rounds")})
    chk("审计字段 updated_at 已落库", bool(one.get("updated_at")))

    print("④ 非法枚举真被拒（HTTP 层，非静默改写）")
    r3 = c.post("/api/crawl/policies", headers=H,
                json={"name": "x", "kind": "video", "sort_type": "9"})
    chk("非法 sort_type → ok=False", r3.json().get("ok") is False, r3.json().get("error"))
    r3b = c.post("/api/crawl/policies", headers=H, json={"name": "x", "kind": "nonsense"})
    chk("非法 kind → ok=False", r3b.json().get("ok") is False, r3b.json().get("error"))
    # ⚠️ 不能写死 total==1：④ 之后的其它断言也会新建策略（顺序相关假失败，实测踩到）
    #    ⇒ 改为「两次拒绝**前后** total 不变」的顺序无关判据。
    total_a = c.get("/api/crawl/policies", headers=H).json().get("total")
    c.post("/api/crawl/policies", headers=H, json={"name": "y", "kind": "bad"})
    total_b = c.get("/api/crawl/policies", headers=H).json().get("total")
    chk("拒绝是**真拒绝**（total 不增）", total_a == total_b, f"{total_a} → {total_b}")

    print("⑤ 数值收敛")
    r5 = c.post("/api/crawl/policies", headers=H,
                json={"name": "边界", "kind": "video", "num": 999, "max_rounds": -3})
    it5 = r5.json().get("item", {})
    chk("num 收敛到 50", it5.get("num") == 50, it5.get("num"))
    chk("max_rounds 收敛到 1", it5.get("max_rounds") == 1, it5.get("max_rounds"))
    c.request("DELETE", f"/api/crawl/policies/{it5.get('id')}", headers=H)

    print("⑥ resolve（只读参数包 + tag_scope）")
    r6 = c.post(f"/api/crawl/policies/{pid}/resolve?account=nobody", headers=H)
    j6 = r6.json()
    chk("resolve 返回 params", j6.get("ok") is True and "params" in j6, list(j6.get("params", {})))
    chk("params 与策略一致", j6.get("params", {}).get("num") == 10)
    chk("tag_scope 字段存在（未绑则 None）", "tag_scope" in j6, j6.get("tag_scope"))

    print("⑦ 房间级标签：悬空引用必须被拒（F1-D1 引用完整性）")
    r7 = c.post("/api/live/rooms", headers=H,
                json={"room_id": "992931212705", "name": "联调房间", "tag_id": "t_ghost"})
    j7 = r7.json()
    chk("悬空 tag_id → 写入被拒", j7.get("ok") is False, j7.get("error"))

    print("⑧ 房间级标签：正常写入 + 读回 tag_id")
    r8 = c.post("/api/live/rooms", headers=H,
                json={"room_id": "992931212705", "name": "联调房间B", "tag_id": ""})
    chk("空 tag_id 允许（= 跟随账号/板块）", r8.json().get("ok") is True, r8.json().get("error"))
    # ⚠️ `save_room` 的返回是 `{"ok": True, "room": upd}`（**顶层无 id/item**）
    #    —— 首版脚本按 {"id": ...} 取 ⇒ rid=None ⇒ 断言假失败（实测踩到）。
    rid = (r8.json().get("room") or {}).get("id", "")
    chk("save_room 返回房间 id", bool(rid), rid)
    r8b = c.get("/api/live/rooms", headers=H)
    rooms = r8b.json().get("items", [])
    mine = next((x for x in rooms if x.get("id") == rid), None)
    chk("能在列表中找回该房间", mine is not None, f"列表 {len(rooms)} 条")
    chk("房间条目含 tag_id 字段（读出口不吞字段）",
        mine is not None and "tag_id" in mine,
        (mine or {}).get("tag_id", "<未找到条目>"))

    # 再验一次「绑了真标签时读回的值正确」（覆盖非空路径，不只是空串）
    from services import config_tag as _ct
    from services import app_config as _ac
    _tid = ""
    names = [t.get("id") for t in (_ct.list_tags() or [])]
    for n_ in names:
        if (_ct.get_tag(n_) or {}).get("name") == "联调标签":
            _tid = n_
            break
    if not _tid:
        _ct.save_tag("", "联调标签")
        for it in (_ct.list_tags() or []):
            if it.get("name") == "联调标签":
                _tid = it.get("id", "")
                break
    chk("造测试标签（供非空路径用）", bool(_tid), _tid or "仍取不到标签 id")
    if _tid:
        r8c = c.post("/api/live/rooms", headers=H,
                     json={"id": rid, "room_id": "992931212705",
                           "name": "联调房间B", "tag_id": _tid})
        chk("绑定真实标签写入成功", r8c.json().get("ok") is True, r8c.json().get("error"))
        got = next((x for x in c.get("/api/live/rooms", headers=H).json().get("items", [])
                    if x.get("id") == rid), {})
        chk("读回 tag_id == 所绑标签（非空路径）", got.get("tag_id") == _tid,
            f"{got.get('tag_id')!r} vs {_tid!r}")

    print("⑨ 删除幂等")
    d1 = c.request("DELETE", f"/api/crawl/policies/{pid}", headers=H).json()
    d2 = c.request("DELETE", f"/api/crawl/policies/{pid}", headers=H).json()
    chk("首次删除 deleted=True", d1.get("deleted") is True, d1)
    chk("重复删除仍 ok（幂等）", d2.get("ok") is True and d2.get("deleted") is False, d2)

    print("⑩ 孤儿模块检测：3 个端点全在 OpenAPI")
    paths = set(app_main.app.openapi().get("paths", {}).keys())
    for p in ("/api/crawl/policies", "/api/crawl/policies/{pid}",
              "/api/crawl/policies/{pid}/resolve"):
        chk(f"路由 {p}", p in paths)

    print()
    if FAILED:
        print(f"❌ {len(FAILED)} 项未通过: {FAILED}")
        return 1
    print("✅ 真实实例端到端验证：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
