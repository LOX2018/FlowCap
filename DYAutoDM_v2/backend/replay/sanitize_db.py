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
| 数字 uid（conv_id 段、peer_id、peer_uid、msg_id） | → 18 位合成号（首见顺序分配，**同值同映射**） |
| 昵称（peer_name / nickname） | → `昵称_01`、`昵称_02` … |
| token 形（`MS4wLjAB…` 等 base64） | → `MS4wLjAB` + 确定性短哈希 |
| 消息正文 text | → `[脱敏]`（读取路径不消费正文） |
| `extra` JSON | 递归脱敏：数字串 → uid 映射；http(s) URL → 占位 |
| 联系方式（contact_value） | → `REDACTED` |
| `crawl_history.payload` / `tasks.config` / `tasks.records` | → `{}` / `[]`（大块抓取内容，与读取路径无关） |
| `kv_store` | **整表清空**（含 app_config / 探针读数 / 索引路径，读取路径不消费） |

⚠️ **本工具只写夹具文件，绝不改动源库**（源库以 `mode=ro` 打开）。
"""

import hashlib
import json
import os
import re
import sqlite3


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


def build(src_db: str, out_db: str) -> dict:
    src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    cur = src.cursor()
    m = _Mapper()
    stats = {}

    # 账号名映射（先扫出全集，保证确定性）
    accts = sorted({r[0] for r in cur.execute(
        "SELECT DISTINCT account FROM dm_conversations WHERE account IS NOT NULL")})
    amap = {a: f"acct_{chr(65 + i)}" for i, a in enumerate(accts)}
    stats["accounts"] = amap

    # 昵称映射（先扫全集，按排序保证确定性）
    names = sorted({r[0] for r in cur.execute(
        "SELECT DISTINCT peer_name FROM dm_conversations "
        "WHERE peer_name IS NOT NULL AND peer_name <> ''")})
    for n in names:
        m.name_of(n)

    if os.path.exists(out_db):
        os.remove(out_db)
    dst = sqlite3.connect(out_db)
    d = dst.cursor()

    # 建表：原样复制 DDL（保 schema 一致）；跳过 sqlite_sequence（保留名，AUTOINCREMENT 自动建）
    for (name, sql) in cur.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='table' AND sql IS NOT NULL").fetchall():
        if name == "sqlite_sequence":
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

    # ---- dm_conversations ----
    rows = cur.execute("SELECT * FROM dm_conversations").fetchall()
    for r in rows:
        d.execute(
            "INSERT INTO dm_conversations (id,account,conv_id,peer_id,peer_name,"
            "short_id,last_ts,unread,avatar,conv_type) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r["id"], amap.get(r["account"], r["account"]),
             _sanitize_conv(r["conv_id"], m),
             m.uid_of(r["peer_id"]) if r["peer_id"] else r["peer_id"],
             m.name_of(r["peer_name"]) if r["peer_name"] else r["peer_name"],
             (_sanitize_conv(r["short_id"], m) if r["short_id"] and
              not _is_token(r["short_id"]) else
              (m.tok_of(r["short_id"]) if r["short_id"] else None)),
             r["last_ts"], r["unread"],
             _maybe_url(r["avatar"], m),
             r["conv_type"]))
    stats["dm_conversations"] = len(rows)

    # ---- dm_messages ----
    rows = cur.execute("SELECT * FROM dm_messages").fetchall()
    for r in rows:
        d.execute(
            "INSERT INTO dm_messages (id,account,conv_id,role,text,msg_type,extra,ts,msg_id)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (r["id"], amap.get(r["account"], r["account"]),
             _sanitize_conv(r["conv_id"], m), r["role"], "[脱敏]",
             r["msg_type"], _maybe_json(r["extra"], m), r["ts"],
             m.uid_of(r["msg_id"]) if r["msg_id"] else r["msg_id"]))
    stats["dm_messages"] = len(rows)

    # ---- 其余表：逐表按列名做最小脱敏 ----
    def _row_sanitize(table, r):
        vals = []
        for k in r.keys():
            v = r[k]
            if v is None:
                vals.append(None)
                continue
            if table == "dm_uid_sink":
                if k in ("peer_uid",):
                    v = m.uid_of(v)
                elif k == "nickname":
                    v = m.name_of(v)
                elif k == "account":
                    v = amap.get(v, v)
            if table == "ai_leads":
                if k == "account":
                    v = amap.get(v, v)
                elif k == "conv_id":
                    v = _sanitize_conv(v, m)
                elif k == "peer_name":
                    v = m.name_of(v)
                elif k == "conv_id":
                    v = _sanitize_conv(v, m)
                elif k in ("contact_value", "source_text"):
                    v = "REDACTED"
            if table == "crawl_history":
                if k == "payload":
                    v = "{}"
                elif k == "keyword":
                    v = ""            # 搜索词可能含人名，一律清空
                elif k == "account":
                    v = amap.get(v, v)
            if table == "tasks":
                if k == "acct":
                    v = amap.get(v, v)
                elif k in ("config", "records", "live_id"):
                    v = "{}" if k != "live_id" else m.uid_of(v)
            if table == "kv_store":
                v = None  # 整表清空
            vals.append(v)
        return vals

    for t in ("dm_uid_sink", "ai_leads", "crawl_history", "tasks", "kv_store"):
        try:
            tbl_rows = cur.execute(f'SELECT * FROM "{t}"').fetchall()
        except sqlite3.OperationalError:
            continue
        if t == "kv_store":
            stats[t] = 0
            continue
        cols = tbl_rows[0].keys() if tbl_rows else [
            r[1] for r in cur.execute(f'PRAGMA table_info("{t}")')]
        ph = ",".join("?" for _ in cols)
        cl = ",".join(f'"{c}"' for c in cols)
        n = 0
        for r in tbl_rows:
            vals = _row_sanitize(t, r)
            if any(v is None for v in vals) and t == "kv_store":
                continue
            d.execute(f'INSERT INTO "{t}" ({cl}) VALUES ({ph})', vals)
            n += 1
        stats[t] = n

    dst.commit()
    dst.execute("VACUUM")
    dst.commit()
    dst.close()
    src.close()
    return stats


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="派生脱敏只读 DB 夹具")
    ap.add_argument("--src", required=True, help="活库路径（只读打开）")
    ap.add_argument("--out", required=True, help="输出夹具路径")
    a = ap.parse_args(argv)
    st = build(a.src, a.out)
    print(json.dumps(st, ensure_ascii=False, indent=2))
    print("out:", a.out, os.path.getsize(a.out), "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
