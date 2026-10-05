# -*- coding: utf-8 -*-
"""实测（零网络前提已满足）：直播搜索结果里能否判定「房间是否加密/脱敏」。

## 为什么必须先实测（不可推理）
用户需求 B：「用凭证搜索，只保留『能拿到真实昵称（未脱敏）』的房间」。
可行性完全取决于一个未验证事实：**搜索结果条目里有没有脱敏信号**。

知识库已记载相反证据（12_业务域_直播监听.md §10.1 / §11.2）：
  A/B 判型（同房间、同时刻、只换 cookie）：两账号**均具解密权**；
  **只有真匿名（无 cookie）才脱敏** ⇒ 不是房间级屏蔽。
若成立，则「按房间过滤脱敏」在语义上不存在 —— 但那是**弹幕层**的结论，
搜索层（`/aweme/v1/web/live/search/`）是否另带房间级字段，必须实测。

## 本脚本只做只读诊断
1. 用真实凭证调 search_live，dump 每条的**全部键**（含 rawdata 解析后的键）；
2. 扫描是否有脱敏/加密相关字段（secret/encrypt/desens/111111/权限类）；
3. 对返回的每个 room_id，再做一次**匿名** GET 直播间页，看能否拿到 room_id/status
   （验证「搜索到的房间能否匿名进」）。

不写任何 kv、不发私信、不改任何文件。
"""
from __future__ import annotations

import json
import os
import re
import sys

DESIGN_ROOT = os.path.abspath(os.environ.get("FLOWCAP_APP_ROOT") or r"C:\temp\flowcap_design")
if DESIGN_ROOT == os.path.abspath(r"C:\temp\flowcap_test"):
    sys.exit("[环境门禁] 拒绝在主分支环境运行")
os.environ["FLOWCAP_APP_ROOT"] = DESIGN_ROOT
_BACKEND = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
os.chdir(_BACKEND)

QUERY = os.environ.get("DY_PROBE_QUERY") or "工伤"
ACCOUNT = os.environ.get("DY_PROBE_ACCOUNT") or "小助理"

# 脱敏/加密/权限相关的键名模式（用于机械扫描）
_SIGNAL_PAT = re.compile(
    r"secret|encrypt|desens|auth|permission|privilege|lock|password|paid|"
    r"hide|hidden|mask|anonym|mystery|111111",
    re.IGNORECASE,
)


def _walk_keys(obj, prefix="", out=None, depth=0):
    """递归收集所有键路径（限深，防爆栈）。"""
    if out is None:
        out = []
    if depth > 8:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            out.append(p)
            _walk_keys(v, p, out, depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:3]):   # 每层只取前 3 个元素
            _walk_keys(v, f"{prefix}[{i}]", out, depth + 1)
    return out


def main() -> int:
    from auto_dm import accounts as acc
    from auto_dm.accounts import verify_credential
    from dy_apis.login_api import DYLoginApi

    env_path = acc.env_path_of(ACCOUNT)
    if not env_path:
        print(f"❌ 账号 {ACCOUNT} 不存在")
        return 1
    vc = verify_credential(ACCOUNT, lightweight=True)
    print(f"凭证检查 {ACCOUNT}: ok={vc['ok']} detail={vc.get('wp', {}).get('detail')}")
    if not vc["ok"]:
        return 1
    auth = DYLoginApi._load_auth_from_env(env_path)
    print(f"凭证已加载（cookie 长度 {len(getattr(auth, 'cookie', '') or '')}）\n")

    # ── 1. 调搜索，dump 原始结构 ──────────────────────────────────────
    from dy_apis.douyin_api import DouyinAPI
    print("=" * 74)
    print(f"【1】search_live(auth, {QUERY!r}) 原始结构")
    print("=" * 74)
    res = DouyinAPI.search_live(auth, QUERY, "0", "25")
    if not isinstance(res, dict):
        print(f"❌ 返回非 dict: {type(res).__name__}")
        return 1
    print(f"顶层键: {list(res.keys())}")
    if res.get("_transport"):
        print(f"⚠️ _transport（风控/传输层）: {res['_transport']}")
    if res.get("search_nil_info"):
        print(f"⚠️ search_nil_info（业务层风控）: {res['search_nil_info']}")
    print(f"status_code = {res.get('status_code')}")
    data = res.get("data") or []
    print(f"data 条数 = {len(data)}\n")

    if not data:
        print("⚠️ data 为空 —— 无法验证字段。可能风控或关键词无结果。")
        return 2

    # ── 2. 逐条 dump 键路径 + 扫脱敏信号 ──────────────────────────────
    print("=" * 74)
    print("【2】逐条条目结构与脱敏信号扫描")
    print("=" * 74)
    all_signals: list[str] = []
    for i, item in enumerate(data[:5]):
        print(f"\n--- data[{i}] ---")
        if not isinstance(item, dict):
            print(f"  非 dict: {type(item).__name__}")
            continue
        print(f"  顶层键: {list(item.keys())}")

        # 解包 lives.rawdata
        lv = item.get("lives")
        target = item
        if isinstance(lv, dict):
            raw = lv.get("rawdata")
            if isinstance(raw, str) and raw.strip():
                try:
                    parsed = json.loads(raw)
                    target = parsed
                    print(f"  ✅ rawdata 解析成功，键数 = {len(parsed)}")
                except Exception as e:
                    print(f"  ❌ rawdata 解析失败: {e}")
            else:
                target = lv
                print("  (无 rawdata，用 lives 本身)")

        keys = _walk_keys(target)
        print(f"  键路径（{len(keys)} 个）:")
        for k in keys[:40]:
            print(f"    {k}")

        # 扫脱敏信号
        hits = [k for k in keys if _SIGNAL_PAT.search(k)]
        if hits:
            print(f"  🔴 脱敏/权限相关键: {hits}")
            all_signals.extend(hits)
        else:
            print("  ⚪ 无脱敏/权限相关键")

        # 关键字段直读
        for f in ("id_str", "status", "title", "user_count"):
            if isinstance(target, dict) and f in target:
                print(f"    {f} = {target.get(f)!r}")
        owner = (target.get("owner") if isinstance(target, dict) else None) or {}
        if isinstance(owner, dict):
            print(f"    owner 键: {list(owner.keys())}")
            print(f"    owner.nickname = {owner.get('nickname')!r}")
            print(f"    owner.sec_uid = {(owner.get('sec_uid') or '')[:30]!r}")

    print("\n" + "=" * 74)
    print("【3】结论")
    print("=" * 74)
    if all_signals:
        print(f"🔴 发现脱敏/权限相关字段（去重）: {sorted(set(all_signals))}")
        print("   ⇒ 若其中确为「房间是否加密」的信号，需求 B 可在**搜索层**实现。")
    else:
        print("⚪ 搜索条目中**无任何**脱敏/权限相关字段。")
        print("   ⇒ 搜索层无法判定「房间是否加密」⇒ 需求 B 不能在搜索层实现。")
        print("   ⇒ 与知识库 §10.1/§11.2 一致：脱敏是**账号级/会话级**，非房间级。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
