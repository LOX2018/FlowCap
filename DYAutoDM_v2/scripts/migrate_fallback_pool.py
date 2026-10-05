# -*- coding: utf-8 -*-
"""迁移已持久化的 ai_reply_config 中不专业的话术（2026-09-29）。

## 为什么需要（本项目实测陷阱）

`services/ai_reply.py::_DEFAULT_CONFIG` 的改动**对已持久化用户不生效**：
`get_config()` 走 `_kv_get(_KV_CONFIG, ...)` 与默认值合并，**键已存在则用库内值**。
实测该用户 `kv_store["ai_reply_config"]` **含** `fallback_pool` / `system_prompt` /
`min_reply_len` 三个键 ⇒ 只改代码默认值，用户侧仍是旧拖延话术。

## 语义（严格限定，只动这三处）

- `fallback_pool`：旧拖延池 → 新的专业承接池（**仅当**内容与已知旧池一致时才替换，
  避免覆盖用户自己写过的文案）。
- `fallback_image`：同上。
- `min_reply_len`：保持 5 不变（该项与 prompt 的 15-100 字口径不冲突：
  5 是"残句下限"，不是"目标长度"）。
- **其余所有字段原样保留**（合并写回，绝不整体覆盖）。

## 用法
    python scripts/migrate_fallback_pool.py            # dry-run（默认，只打印）
    python scripts/migrate_fallback_pool.py --apply    # 实际写入（写前自动备份）
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

# 旧池的**逐字**快照（用于判定"这是默认旧值，可安全替换"，而非用户自写文案）
OLD_POOL = [
    "嗯嗯稍等哈，我问下马上回你",
    "这个我得确认一下哈，稍等",
    "收到，我问好了发你",
    "稍等哈，我看看",
]
OLD_IMAGE = "图我看到了哈，稍等我看看再回你"

# 新池（与 services/ai_reply._DEFAULT_CONFIG 保持一致）
NEW_POOL = [
    "您好，我是唐律工伤团队的理赔顾问。请补充：受伤部位、诊断结论、"
    "是否有劳动合同和社保，我帮您判断能否认定工伤、大概几级。",
    "您的情况需要看具体材料才能给准话。方便说下伤情诊断和所在城市吗？"
    "有劳动合同或工资记录的话，认定会顺利很多。",
    "收到。工伤认定看三点：劳动关系、受伤经过、诊断材料。"
    "您先说下受伤部位和现在有没有住院，我帮您对一下等级。",
    "理解您的情况。请您说下：哪里受的伤、什么时候、单位有没有买社保，"
    "我按劳动能力鉴定的标准帮您估一下。",
]
NEW_IMAGE = ("图片收到，我看下材料再给您准话。方便的话补充说明下"
             "受伤部位和所在城市，判断会更快。")


class _NoTargetDB(Exception):
    """I-4：没有任何候选库含 ai_reply_config ⇒ 无法定位真生产库（拒绝假阴性）。"""


def _find_db() -> Path:
    """定位生产库。

    registry.json 的真实结构是 `{"version":1, "members": {"m17...": {...}}}`
    —— 成员在 **members 字典**里（无 current/active 字段）。
    实测踩坑：按 `current`/`active`/`member_id` 取会取不到，退化路径又会
    先命中 `hotswap-probe`（探针库、无 ai_reply_config）⇒ 迁移"成功"但改错库。
    故：**优先选真正含 `ai_reply_config` 的那个库**。

    I-4 修复（2026-09-29 · OCR[29] · MED · 假阴性）：
    原逻辑在**没有任何候选**含 `ai_reply_config` 时 `return candidates[0]`
    （= docstring 明示的 `hotswap-probe` 形态）⇒ `main()` 读到缺键即退出 0 报
    「无需迁移（走代码默认值）」—— **假阴性**（真生产库根本没找到）。
    现改为：无候选含该键时**抛 `_NoTargetDB` 报错**，绝不静默退 0。
    """
    root = os.environ.get("DY_APP_ROOT") or r"C:\temp\dyautodm_design"
    root_p = Path(root)
    candidates: list[Path] = []
    reg = root_p / "members" / "registry.json"
    if reg.is_file():
        try:
            d = json.loads(reg.read_text(encoding="utf-8"))
            members = (d or {}).get("members") or {}
            for mid in members:
                p = root_p / "members" / str(mid) / "data" / "dyautodm.db"
                if p.is_file():
                    candidates.append(p)
        except Exception:
            pass
    # 补上所有 members/*/data/dyautodm.db（保持 registry 顺序在前）
    for sub in sorted((root_p / "members").glob("*/data/dyautodm.db")):
        if sub not in candidates:
            candidates.append(sub)

    for p in candidates:
        try:
            c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            hit = c.execute(
                "SELECT 1 FROM kv_store WHERE key='ai_reply_config'").fetchone()
            c.close()
            if hit:
                return p
        except Exception:
            continue
    if candidates:
        # I-4：没有任何候选含 ai_reply_config —— 不得拿候选[0] 冒充目标库
        # （那会让 main() 静默退 0 = 假阴性）。报错列出候选，交由人工核对。
        raise _NoTargetDB(
            f"找到 {len(candidates)} 个 dyautodm.db，但**没有任何一个**含 "
            f"ai_reply_config 键（docstring 所述 `hotswap-probe` 探针库即此形态）"
            f"⇒ 真生产库未定位，**拒绝静默退出 0**（假阴性）。\n候选:\n  "
            + "\n  ".join(str(p) for p in candidates))
    raise SystemExit("未找到 dyautodm.db")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写入（默认 dry-run）")
    args = ap.parse_args()

    try:
        db = _find_db()
    except _NoTargetDB as e:
        # I-4：明确报错退出（非静默退 0）—— 真生产库没找到就必须让人看到
        print(f"❌ 无法定位目标库：{e}")
        return 2
    print(f"目标库: {db}")
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT value FROM kv_store WHERE key='ai_reply_config'").fetchone()
    if not row:
        print("未找到 ai_reply_config 键 —— 无需迁移（走代码默认值）")
        return 0
    cfg = json.loads(row["value"])

    changed = []
    cur_pool = cfg.get("fallback_pool")
    if isinstance(cur_pool, list) and cur_pool == OLD_POOL:
        cfg["fallback_pool"] = list(NEW_POOL)
        changed.append("fallback_pool")
    elif cur_pool:
        print(f"⚠ fallback_pool 与已知旧池不一致（可能用户自写过）⇒ **不动**：{cur_pool}")
    else:
        print("fallback_pool 缺失 ⇒ 不动（走代码默认）")

    if cfg.get("fallback_image") == OLD_IMAGE:
        cfg["fallback_image"] = NEW_IMAGE
        changed.append("fallback_image")
    elif cfg.get("fallback_image"):
        print("⚠ fallback_image 与已知旧值不一致 ⇒ **不动**")

    if not changed:
        print("无可迁移项（已是最新或用户自定义）")
        return 0

    print(f"\n将替换字段: {changed}")
    # I-3 修复（2026-09-29 · OCR[28] · MED）：`fallback_pool` 可能**未迁移**
    # （else 分支「缺失 ⇒ 不动」）。原逻辑无条件取 `cfg["fallback_pool"]`
    # ⇒ KeyError；读回验证同样无条件取 `chk["fallback_pool"]`。现按 `changed`
    # 分键守卫：未迁移的键既不打印也不纳入校验。
    if "fallback_pool" in changed:
        print("新 fallback_pool:")
        for x in cfg["fallback_pool"]:
            print("   -", x)
    if "fallback_image" in changed:
        print("新 fallback_image:", cfg["fallback_image"])

    if not args.apply:
        print("\n(--dry-run：未写入；加 --apply 实际执行)")
        return 0

    # 写前备份（带时间戳）+ 读回比对
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = Path(str(db) + f".bak.poolmig.{ts}")
    shutil.copy(str(db), str(bak))
    _b = sqlite3.connect(f"file:{bak}?mode=ro", uri=True).execute(
        "SELECT value FROM kv_store WHERE key='ai_reply_config'").fetchone()[0]
    assert _b == row["value"], "备份内容与写前值不一致，中止"
    print(f"\n已备份: {bak}（读回比对通过）")

    # 合并写回（只改上述字段，其余原样）
    conn.execute("UPDATE kv_store SET value=? WHERE key='ai_reply_config'",
                 (json.dumps(cfg, ensure_ascii=False),))
    conn.commit()

    # 写后读回验证（I-3：只校验**本次真正迁移**的键，未迁移的键不参与）
    chk = json.loads(conn.execute(
        "SELECT value FROM kv_store WHERE key='ai_reply_config'").fetchone()[0])
    print("\n写后读回：")
    checks = []
    if "fallback_pool" in changed:
        print("  fallback_pool[0] =", (chk.get("fallback_pool") or [""])[0][:40], "…")
        checks.append(chk.get("fallback_pool") == NEW_POOL)
    if "fallback_image" in changed:
        print("  fallback_image =", str(chk.get("fallback_image"))[:40], "…")
        checks.append(chk.get("fallback_image") == NEW_IMAGE)
    print("  其余键数量 =", len(chk), "（写前", len(json.loads(row["value"])), "）")
    # 键数量守恒：迁移只改值、不增删键（合并写回的铁律）
    checks.append(len(chk) == len(json.loads(row["value"])))
    ok = all(checks)
    print("迁移结果:", "✅ 成功" if ok else "❌ 校验失败")
    conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
