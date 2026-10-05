# -*- coding: utf-8 -*-
"""从**活库**派生「脱敏只读夹具」——供离线回放回归使用。

## 为什么需要它

`api/messages.py` 的会话读取路径（纯读 SQLite）此前**只能靠真机**验，而它恰恰是
事故高发区（§九·乙：排序把空会话顶前、轮询覆盖缓存、字段形状错配）。
活库不可入库（含真实昵称/账号/消息），故派生一份**双向映射脱敏**的只读快照。

## 脱敏规则（双向映射，可逆，保证关联关系不破坏）

| 字段类别 | 处理 |
|---|---|
| 账号名 | → `acct_A` / `acct_B`（按出现顺序） |
| 数字 uid（conv_id 段、peer_id、peer_uid、msg_id、live_id） | → 18 位合成号（首见顺序分配，**同值同映射**） |
| 昵称（peer_name / nickname） | → `昵称_01`、`昵称_02` … |
| token 形（`MS4wLjAB…` 等 base64） | → `MS4wLjAB` + 确定性短哈希 |
| 消息正文 text | → `[脱敏]`（读取路径不消费正文） |
| `extra` JSON | 递归脱敏：数字串 → uid 映射；http(s) URL → 占位 |
| 联系方式（contact_value / source_text） | → `REDACTED` |
| `crawl_history.payload` / `keyword` / `tasks.config` / `tasks.records` | → `{}` / `""`（大块抓取内容，与读取路径无关） |
| `kv_store` | **整表清空**（含 app_config / 探针读数 / 索引路径，读取路径不消费） |

## 三条硬门禁（2026-09-23 假绿修复）

1. **表清单从 schema 动态取** —— 不再硬编码表名（旧实现漏掉 `dm_cross_sink`，
   含 peer_uid/nickname PII 的表被静默丢弃）。源库出现**未登记表**即 `raise`
   `UndocumentedTable`（fail loud，绝不 `continue` 静默丢弃）。
2. **默认拒绝（default-deny）** —— 每张表的**每一列**都必须显式声明脱敏策略；
   未登记列即 `raise` `UndocumentedColumn`。杜绝「列名白名单未命中→原样透传」
   （旧实现 `_row_sanitize` 对未知列直接透传，schema 漂移即静默泄漏）。
3. **导出后自证** —— `assert_no_pii()` 逐表逐列扫描产物，断言**源库任何 PII
   原文都不出现在夹具中**；同时禁止未登记的 CJK 明文与真实 URL。门禁失败即
   `raise`，不产出半成品。

⚠️ **本工具只写夹具文件，绝不改动源库**（源库以 `mode=ro` 打开）。
"""

import hashlib
import json
import os
import re
import sqlite3


class UndocumentedTable(RuntimeError):
    """源库出现未登记脱敏策略的表 —— 拒绝导出（防止静默漏表泄漏）。"""


class UndocumentedColumn(RuntimeError):
    """表中出现未声明脱敏策略的列 —— 拒绝导出（防止未知列原样透传泄漏）。"""


class PiiLeak(RuntimeError):
    """导出产物中检出 PII 明文 —— 夹具不合格，拒绝交付。"""


class _Mapper:
    def __init__(self):
        self.uid = {}
        self.name = {}
        self.tok = {}

    def uid_of(self, s: str) -> str:
        s = str(s)
        if s not in self.uid:
            self.uid[s] = "9" + str(len(self.uid) + 1).zfill(16)
        return self.uid[s]

    def name_of(self, s: str) -> str:
        s = str(s)
        if s not in self.name:
            self.name[s] = f"昵称_{len(self.name) + 1:02d}"
        return self.name[s]

    def tok_of(self, s: str) -> str:
        if s not in self.tok:
            h = hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()[:24]
            self.tok[s] = "MS4wLjAB" + h
        return self.tok[s]


_ACCT = re.compile(r"^\d{6,22}$")


def _is_token(s: str) -> bool:
    return bool(re.match(r"^(MS4wLjAB|sec_|eyJ)[A-Za-z0-9_\-]{10,}$", s or ""))


def _sanitize_conv(conv: str, m: _Mapper) -> str:
    """`0:1:uid:uid` → 逐段映射。"""
    return re.sub(r"\d{6,22}", lambda x: m.uid_of(x.group(0)), conv or "")


def _sanitize_json_text(s, m: _Mapper, _key=None):
    """对 JSON 值递归脱敏。

    规则（顺序敏感）：
      ① 数字串 → uid 映射（须先于通配）
      ② http(s) URL → 占位
      ③ **其余字符串一律不透明化**（`昵称_NN` 式确定性映射）——
         因为 `extra` 里可能嵌昵称/正文片段（实测泄漏过 `'Leooo'`）；
         但 **键名与数值类型原样保留**（`created_at_us` 是读取路径的排序键）。
    """
    if isinstance(s, str):
        if re.match(r"^\d{6,22}$", s):
            return m.uid_of(s)
        if re.match(r"^https?://", s):
            return "https://example.invalid/redacted"
        return m.name_of(s)
    if isinstance(s, bool):
        return s
    if isinstance(s, (int, float)):
        return s
    if isinstance(s, list):
        return [_sanitize_json_text(x, m) for x in s]
    if isinstance(s, dict):
        return {k: _sanitize_json_text(v, m, k) for k, v in s.items()}
    return None


def _maybe_json(s, m: _Mapper):
    if not s:
        return s
    try:
        return json.dumps(_sanitize_json_text(json.loads(s), m), ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return re.sub(r"\d{8,22}", lambda x: m.uid_of(x.group(0)), str(s))


def _maybe_url(s: str, m: _Mapper):
    """URL 形值一律占位（avatar 等 CDN 链接实测会泄漏真实域名/路径）。"""
    if s and re.match(r"^https?://", s):
        return "https://example.invalid/redacted"
    return m.tok_of(s) if _is_token(s) else s


# ═══════════════════════════════════════════════════════════════════════════
# 脱敏策略表（**显式声明，默认拒绝**）
#
# 每个 transform 是「列名 → 语义」的显式登记：
#   safe    原样保留（已确认非 PII：时间戳/计数/枚举/主键）
#   acct    账号名 → acct_X
#   uid     数字 uid → 合成号
#   name    昵称 → 昵称_NN
#   conv    conv_id 逐段 uid 映射
#   shortid short_id：token 形 → tok 映射，否则 conv 映射
#   token   token 形 → 确定性短哈希
#   url     URL → 占位
#   json    JSON 递归脱敏
#   blank   清空
#   redact  固定 "REDACTED"
#   text    正文 → "[脱敏]"
#
# ⚠️ 新增表/列必须在此登记；否则 build() 直接 raise（不再静默丢弃/透传）。
# ═══════════════════════════════════════════════════════════════════════════
_POLICY = {
    "dm_conversations": {
        "id": "safe", "account": "acct", "conv_id": "conv", "peer_id": "uid",
        "peer_name": "name", "short_id": "shortid", "last_ts": "safe",
        "unread": "safe", "avatar": "url", "conv_type": "safe",
        # 2026-09-23 登记：消息预览含真实正文/人称 PII（读取路径不消费）
        "last_msg_preview": "blank",
    },
    "dm_messages": {
        "id": "safe", "account": "acct", "conv_id": "conv", "role": "safe",
        "text": "text", "msg_type": "safe", "extra": "json", "ts": "safe",
        "msg_id": "uid",
    },
    # 通用键值：整表清空（含 app_config/探针读数/索引路径，读取路径不消费）
    "kv_store": {"key": "safe", "value": "safe"},
    "dm_uid_sink": {
        "account": "acct", "peer_uid": "uid", "nickname": "name",
        "source": "safe", "first_seen_ts": "safe", "sent_ts": "safe",
        "send_count": "safe",
    },
    # 2026-09-23 补登记：跨账号沉淀池含 peer_uid/nickname PII（旧实现漏表 ⇒ 静默丢弃）
    "dm_cross_sink": {
        "peer_uid": "uid", "account_sent": "acct", "nickname": "name",
        "source": "safe", "sent_ts": "safe", "cool_until": "safe",
        "send_count": "safe",
    },
    "ai_leads": {
        "id": "safe", "account": "acct", "conv_id": "conv", "peer_name": "name",
        "contact_type": "safe", "contact_value": "redact", "source_text": "redact",
        "status": "safe", "created_at": "safe",
    },
    "crawl_history": {
        "id": "safe", "account": "acct", "kind": "safe", "keyword": "blank",
        "target": "uid", "result_count": "safe", "payload": "blank", "ts": "safe",
    },
    "tasks": {
        "id": "safe", "acct": "acct", "live_id": "uid", "start_ts": "safe",
        "end_ts": "safe", "status": "safe", "result_count": "safe",
        "config": "blank", "records": "blank", "created_at": "safe", "pid": "safe",
    },
}

#: 不复制内容的表（保留 DDL，行数=0）
_TABLE_MODE = {"kv_store": "empty"}

#: 完全不处理的 SQLite 内部表（AUTOINCREMENT 自动重建）
_SKIP_TABLES = {"sqlite_sequence"}


def _apply(transform: str, v, m: _Mapper, amap: dict):
    """按显式 transform 处理单值。None 一律保留为 None。"""
    if v is None:
        return None
    if transform == "safe":
        return v
    if transform == "acct":
        return amap.get(v, v)
    if transform == "uid":
        return m.uid_of(v)
    if transform == "name":
        return m.name_of(v) if str(v) != "" else v
    if transform == "conv":
        return _sanitize_conv(v, m)
    if transform == "shortid":
        if _is_token(v):
            return m.tok_of(v)
        return _sanitize_conv(v, m)
    if transform == "token":
        return m.tok_of(v)
    if transform == "url":
        return _maybe_url(v, m)
    if transform == "json":
        return _maybe_json(v, m)
    if transform == "blank":
        return "" if isinstance(v, str) else None
    if transform == "redact":
        return "REDACTED"
    if transform == "text":
        return "[脱敏]"
    raise UndocumentedColumn(f"未知脱敏 transform: {transform!r}")


def _discover_tables(cur) -> list:
    return [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND sql IS NOT NULL ORDER BY name")]


def _validate_schema(cur, tables) -> None:
    """默认拒绝：未登记的表/列一律 raise（绝不静默丢弃/透传）。"""
    unknown_tables = [t for t in tables
                      if t not in _SKIP_TABLES and t not in _POLICY]
    if unknown_tables:
        raise UndocumentedTable(
            f"源库含未登记脱敏策略的表：{unknown_tables}。"
            f"请在 sanitize_db._POLICY 显式登记每列 transform（默认拒绝）。")
    for t in tables:
        if t in _SKIP_TABLES:
            continue
        cols = [r[1] for r in cur.execute(f'PRAGMA table_info("{t}")')]
        unknown = [c for c in cols if c not in _POLICY[t]]
        if unknown:
            raise UndocumentedColumn(
                f"表 {t!r} 含未登记列：{unknown}。"
                f"请在 sanitize_db._POLICY[{t!r}] 显式登记（默认拒绝）。")


def build(src_db: str, out_db: str) -> dict:
    src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    cur = src.cursor()
    m = _Mapper()
    stats = {}

    tables = _discover_tables(cur)
    _validate_schema(cur, tables)

    # 账号名映射（先扫出全集，保证确定性）
    accts = sorted({r[0] for r in cur.execute(
        "SELECT DISTINCT account FROM dm_conversations WHERE account IS NOT NULL")})
    amap = {a: f"acct_{chr(65 + i)}" for i, a in enumerate(accts)}
    stats["accounts"] = amap

    # 昵称映射（先扫出全集，按排序保证确定性）
    names = sorted({r[0] for r in cur.execute(
        "SELECT DISTINCT peer_name FROM dm_conversations "
        "WHERE peer_name IS NOT NULL AND peer_name <> ''")})
    for n in names:
        m.name_of(n)

    if os.path.exists(out_db):
        os.remove(out_db)
    dst = sqlite3.connect(out_db)
    d = dst.cursor()

    # 建表：原样复制 DDL（保 schema 一致）；跳过 sqlite_sequence（AUTOINCREMENT 自动建）
    for (name, sql) in cur.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='table' AND sql IS NOT NULL").fetchall():
        if name in _SKIP_TABLES:
            continue
        d.execute(sql)
    # 索引也复制（读取路径的 ORDER BY/COUNT 需要正确执行计划，非必需但更真实）
    for (sql,) in cur.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
    ).fetchall():
        try:
            d.execute(sql)
        except sqlite3.OperationalError:
            pass

    # 逐表逐列按**显式策略**脱敏（动态表清单，不漏新表）
    for t in tables:
        if t in _SKIP_TABLES:
            continue
        if _TABLE_MODE.get(t) == "empty":
            stats[t] = 0
            continue
        rows = cur.execute(f'SELECT * FROM "{t}"').fetchall()
        cols = [c[1] for c in cur.execute(f'PRAGMA table_info("{t}")')]
        ph = ",".join("?" for _ in cols)
        cl = ",".join(f'"{c}"' for c in cols)
        n = 0
        for r in rows:
            vals = [_apply(_POLICY[t][c], r[c], m, amap) for c in cols]
            d.execute(f'INSERT INTO "{t}" ({cl}) VALUES ({ph})', vals)
            n += 1
        stats[t] = n

    dst.commit()
    dst.execute("VACUUM")
    dst.commit()
    dst.close()
    src.close()
    return stats


# ═══════════════════════════════════════════════════════════════════════════
# 产物自证：断言夹具中不含源库任何 PII 原文
# ═══════════════════════════════════════════════════════════════════════════
_URL_PLACEHOLDER = "example.invalid"
_TOKEN_RE = re.compile(r"^(MS4wLjAB|sec_|eyJ)[A-Za-z0-9_\-]{16,}$")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")


def _pii_columns(src_db: str) -> list:
    """返回源库中「声明为 PII 列」的 (表, 列, 该列全部非空字符串值)。

    `safe` 列是**显式声明为已核对非 PII**（时间戳/计数/枚举/pid），其原值允许保留；
    其余列（acct/uid/name/conv/shortid/token/url/json/blank/redact/text）都是
    PII 列 —— 其源库原文**一律不得出现在夹具中**。
    """
    src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    out = []
    cur = src.cursor()
    for (t,) in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"):
        if t in _SKIP_TABLES or t not in _POLICY:
            continue
        cols = [r[1] for r in cur.execute(f'PRAGMA table_info("{t}")')]
        pii = [c for c in cols if _POLICY[t].get(c, "safe") != "safe"]
        if not pii:
            continue
        vals = set()
        for r in cur.execute(f'SELECT * FROM "{t}"'):
            for c in pii:
                v = r[c]
                if isinstance(v, str) and len(v) >= 4:
                    vals.add(v)
        out.append((t, pii, vals))
    src.close()
    return out


def assert_no_pii(out_db: str, src_db: str | None = None) -> None:
    """机械自证：夹具中不得出现源库 PII 原文 / 真实 URL / 未登记 CJK 明文。

    判据：
      ① PII 列（策略非 safe）的**源库原文**不得出现在夹具任何单元格；
      ② 不得出现非占位 http(s) URL；
      ③ 不得出现未登记的 CJK 明文（只允许 `昵称_NN` / `[脱敏]` / `REDACTED`）。

    失败即 raise PiiLeak（绝不产出半成品）。
    """
    out = sqlite3.connect(f"file:{out_db}?mode=ro", uri=True)
    out.row_factory = sqlite3.Row
    leaks = []
    # ① 源库 PII 列原文集合（只取 PII 列，safe 列原值允许保留）
    pii_values = set()
    if src_db:
        for _t, _cols, vals in _pii_columns(src_db):
            pii_values |= vals
    cur = out.cursor()
    for (t,) in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"):
        if t in _SKIP_TABLES:
            continue
        cols = [r[1] for r in cur.execute(f'PRAGMA table_info("{t}")')]
        for r in cur.execute(f'SELECT * FROM "{t}"'):
            for c in cols:
                v = r[c]
                if not isinstance(v, str) or not v:
                    continue
                if v in pii_values:
                    leaks.append((t, c, "源库 PII 原文", v[:40]))
                    continue
                if re.search(r"https?://", v) and _URL_PLACEHOLDER not in v:
                    leaks.append((t, c, "真实 URL", v[:60]))
                if _CJK_RE.search(v) and not re.fullmatch(r"昵称_\d+", v) \
                        and v not in ("[脱敏]", "REDACTED"):
                    leaks.append((t, c, "未登记 CJK 明文", v[:40]))
    out.close()
    if leaks:
        raise PiiLeak(f"夹具检出 {len(leaks)} 处未脱敏内容（示例 {leaks[:5]}）")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="派生脱敏只读 DB 夹具")
    ap.add_argument("--src", required=True, help="活库路径（只读打开）")
    ap.add_argument("--out", required=True, help="输出夹具路径")
    ap.add_argument("--no-assert", action="store_true",
                    help="跳过产物自证（仅调试用；默认强制自证）")
    a = ap.parse_args(argv)
    st = build(a.src, a.out)
    if not a.no_assert:
        assert_no_pii(a.out, a.src)
    print(json.dumps(st, ensure_ascii=False, indent=2))
    print("out:", a.out, os.path.getsize(a.out), "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
