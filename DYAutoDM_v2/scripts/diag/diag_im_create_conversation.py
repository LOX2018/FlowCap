# -*- coding: utf-8 -*-
"""cmd 609 判别工具：同一代码路径下，逐账号对比「探活 / 读会话(610) / 建会话(609)」。

## 用途（一步归因，不猜）

当私信链路报 `cmd 609 · unexepcted session length`（服务端原文，含拼写错误）时，
本工具直接给出归因方向：

| 观测 | 结论 |
|---|---|
| 某账号 **610 成功、609 全失败** | **账号级**：该账号 web 会话被服务端判为「只读」→ 重新授权（重新扫码）即可，**不得动协议层** |
| **两个账号都失败** | **协议级**：才需要考虑协议/签名素材（如 dtrait）变更 |
| 两账号都成功 | 链路健康（609 已消失；若仍复现则为时序竞态，重发一次） |

判据出处：`工作记忆/cases/2026-09-20_直播监听失败_cmd609session-length_取证与上游溯源.md` §八。

## 为什么用 create_conversation 做探针

`create_conversation` 只**建/取会话**、**不投递任何消息**（无消息 = 无对真人打扰），
因此适合做「写会话能力」的判别探针；目标默认取**账号自身 uid**（与引擎预检 `_verify_credential` 同参）。

## 用法

    python scripts/diag/diag_im_create_conversation.py [--target <uid>]

## 环境与安全

- 环境门禁：`DY_APP_ROOT` 必须指向**设计分支**数据根（默认 `C:\\temp\\dyautodm_design`）；
  指向 `C:\\temp\\dyautodm_test`（主分支）时**拒绝运行**——隔离靠代码门禁，不靠记忆。
- 零浏览器、零引擎、零常驻进程：只在当前进程内读 .env（会员空间自动解密）并发一次 IM 请求。
- **只打印键名 / 长度 / 命中与否，绝不打印任何 cookie、ticket、私钥值。**
- 账号名与 uid 全部**运行期从磁盘/接口取得**，脚本内不写死任何账号名或 uid。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

DESIGN_ROOT = os.path.abspath(os.environ.get("DY_APP_ROOT") or r"C:\temp\dyautodm_design")
_FORBIDDEN = {os.path.abspath(r"C:\temp\dyautodm_test")}

BACKEND = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))


def _bootstrap() -> dict:
    """环境门禁 + 会员态注入 + sys.path，返回 {"member_id":..., "acc_root":..., "db":...}。"""
    if DESIGN_ROOT in _FORBIDDEN:
        sys.exit("[环境门禁] DY_APP_ROOT 指向主分支环境(%s)，拒绝运行" % DESIGN_ROOT)
    os.environ["DY_APP_ROOT"] = DESIGN_ROOT
    sys.path.insert(0, BACKEND)
    os.chdir(BACKEND)

    sess_path = os.path.join(DESIGN_ROOT, "members", ".session.json")
    if not os.path.exists(sess_path):
        sys.exit("[环境] 未找到会员会话 %s" % sess_path)
    with open(sess_path, encoding="utf-8") as f:
        sess = json.load(f)
    os.environ["DY_MEMBER"] = sess["member_id"]
    os.environ["DY_MEMBER_KEY"] = sess["master_key"]
    member_id = sess["member_id"]
    return {
        "member_id": member_id,
        "acc_root": os.path.join(DESIGN_ROOT, "members", member_id, "auto_dm", "accounts"),
        "db": os.path.join(DESIGN_ROOT, "members", member_id, "data", "dyautodm.db"),
    }


def _hist_uid(env: dict, account: str):
    """从该账号历史 conv_id 推断自身 uid（只读 DB；失败返回 None）。"""
    try:
        from services import conv_identity
    except Exception:
        return None
    try:
        con = sqlite3.connect(f"file:{env['db']}?mode=ro", uri=True, timeout=5)
        conv_ids = [r[0] for r in con.execute(
            "select conv_id from dm_conversations where account=?", (account,))]
        con.close()
    except Exception:
        return None
    try:
        return conv_identity.infer_my_uid_from_conv_ids(conv_ids)
    except Exception:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="cmd 609 归因判别（探活/610 读/609 建 逐账号对比）")
    ap.add_argument("--target", type=int, default=None,
                    help="自定义 create 目标 uid（默认取各账号自身 uid，与引擎预检同参）")
    args = ap.parse_args(argv)

    env = _bootstrap()
    from dy_apis.login_api import DYLoginApi
    from dy_apis.douyin_api import DouyinAPI

    if not os.path.isdir(env["acc_root"]):
        sys.exit("[环境] 账号目录不存在：%s" % env["acc_root"])
    accounts = sorted(a for a in os.listdir(env["acc_root"])
                      if os.path.isdir(os.path.join(env["acc_root"], a)))

    print(f"数据根(DY_APP_ROOT) = {DESIGN_ROOT}")
    print(f"账号目录            = {env['acc_root']}（{len(accounts)} 个）")
    print(f"create 目标         = {'自定义 ' + str(args.target) if args.target else '各账号自身 uid'}")
    verdicts = {}
    for acct in accounts:
        print(f"\n{'=' * 74}\n账号：{acct}")
        try:
            auth = DYLoginApi._load_auth_from_env(os.path.join(env["acc_root"], acct, ".env"))
        except Exception as e:
            print(f"  .env 加载失败：{type(e).__name__}: {e}")
            continue
        print(f"  签名四件套：ticket={len(auth.ticket or '')} ts_sign={len(auth.ts_sign or '')} "
              f"client_cert={len(auth.client_cert or '')} private_key={len(auth.private_key or '')}")

        uid = None
        try:
            uid = DouyinAPI.get_my_uid(auth, force_probe=True)
        except Exception as e:
            print(f"  ① 探活失败：{type(e).__name__}: {str(e)[:120]}")
        hist = _hist_uid(env, acct)
        if uid:
            same = str(uid) == str(hist)
            print(f"  ① 探活 uid={uid}  历史推断 uid={hist}  一致={same}"
                  + ("" if same else "  ← AUTH-050 同族（陈旧/不可信 uid）"))

        # ② 只读：cmd 610
        try:
            DouyinAPI.get_conversation_list(auth, 0)
            print("  ② 610 读会话列表 -> 成功（只读通路正常）")
        except Exception as e:
            print(f"  ② 610 读会话列表 -> 失败：{str(e)[:140]}")

        # ③ 写：cmd 609（建/取会话，不投递消息）
        if not uid:
            print("  ③ 609 建会话 -> 跳过（无 uid）")
            verdicts[acct] = None
            continue
        tgt = int(args.target) if args.target else int(uid)
        try:
            r = DouyinAPI.create_conversation(auth, tgt)
            print(f"  ③ 609 建会话(target={tgt}) -> 成功 conv={str(r[0])[:40]}")
            verdicts[acct] = True
        except Exception as e:
            print(f"  ③ 609 建会话(target={tgt}) -> 失败：{str(e)[:160]}")
            verdicts[acct] = False

    # ---- 汇总判定 ----
    print(f"\n{'=' * 74}\n【判定】")
    bad = [a for a, v in verdicts.items() if v is False]
    good = [a for a, v in verdicts.items() if v is True]
    if not bad:
        print("  未见 609：链路健康（若仍复现，先按时序竞态重发一次 /api/engine/start）。")
    elif good:
        print(f"  写会话失败：{bad}")
        print(f"  写会话成功：{good}")
        print("  => **账号级**：失败账号的 web 会话被服务端判为只读。")
        print("     处置：对失败账号执行【重新扫码】重新捕获同一浏览器会话的 cookie + 四件套；")
        print("           **不得**改协议层（无证据支持协议变更）。")
    else:
        print(f"  全部账号写会话失败：{bad}")
        print("  => **协议级**：考虑协议/签名素材（dtrait 等）变更，按 knowledge case §八 判据推进。")
    print("\n  验收（重新授权后）：① 本脚本失败账号转为「成功」；"
          "② 引擎日志出现 [auth] 私信签名预检通过；③ dm_messages 落 role=me。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
