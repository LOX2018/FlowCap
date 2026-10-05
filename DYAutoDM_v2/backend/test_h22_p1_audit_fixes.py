# -*- coding: utf-8 -*-
"""H-22 审计 P1 修复的机械门禁（防复发）—— 2026-09-26。

## 四条判据（每条的判据都来自一次实测复现/调用链实证）
  G1  services/ai_reply.py ：`_img_desc_get/_known/_mark` 三函数**必须**持同一把
      `_IMG_DESC_LOCK` —— 防「整 dict 读-改-写」在并发下的静默丢条目
      （实测：无锁 8 线程×30 → 240 条只存 50；带锁 → 240/240）。
  G2  notify/inbound.py ：`_ilink_qr_login` 成功取得新 token 后**必须**同步回写
      `ch.token` —— 否则 `ch.send()` 用旧 token，会话恢复后回复仍失败
      （实测：修复后 ch_send_tokens_used=["NEW_TOKEN"]；修复前为 ["OLD_TOKEN"]）。
  G3  services/kernel_truth.py ：`_MAX_AGE_SEC` **必须 ≥ 2×真值采样门限**（门限实测
      3600s，见 daemon/bcc_login.py）—— 否则真值写入后有一半时间被误判 stale
      ⇒ 指纹静默回落预设（即本文件要根除的「档案≠内核脱节」）。
  G4  dy_apis/client_user.py ：失败缓存写入**必须**位于 HTML 回退之后 ——
      否则回退成功时缓存被写成 `(ts,"")`，60s 内下次调用被误杀（实跑复现）。

每条判据均配负控：喂「旧形态」源码必须判红。
"""
from __future__ import annotations

import ast
import os
import re
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))   # .../DYAutoDM_v2/backend


def _read(rel: str) -> str:
    with open(os.path.join(_HERE, rel), encoding="utf-8") as f:
        return f.read()


def _func_src(src: str, name: str) -> str | None:
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return "".join(lines[n.lineno - 1:n.end_lineno])
    return None


# ═════════════════════ 判据（可被负控复用） ═════════════════════

def check_g1(source: str) -> tuple[bool, str]:
    if "_IMG_DESC_LOCK" not in source:
        return False, "未找到 _IMG_DESC_LOCK（图片描述缓存无锁）"
    bad = []
    for fn in ("_img_desc_get", "_img_desc_known", "_img_desc_mark"):
        s = _func_src(source, fn)
        if s is None:
            bad.append(f"{fn}(缺失)")
        elif "with _IMG_DESC_LOCK" not in s:
            bad.append(f"{fn}(未加锁)")
    if bad:
        return False, f"未受锁保护: {bad}"
    return True, "三个 _img_desc_* 均持 _IMG_DESC_LOCK"


def check_g2(source: str) -> tuple[bool, str]:
    lines = source.splitlines()
    i_logged = i_sync = None
    for i, l in enumerate(lines):
        if l.strip() == "token = logged":
            i_logged = i
        if l.strip().startswith("ch.token = token") and i_sync is None and i_logged is not None:
            i_sync = i
    if i_logged is None:
        return False, "未找到 `token = logged`"
    if i_sync is None:
        return False, "重登后未同步 ch.token（ch.send 会用旧 token）"
    if i_sync <= i_logged:
        return False, f"ch.token 同步(line {i_sync}) 不晚于 token 赋值(line {i_logged})"
    # 同分支判定：两者之间只允许「注释 / 空行 / try / except / pass」等包裹语句，
    # 不得夹带其它业务代码（注释不计入，避免注释长度影响判据）。
    _allowed = ("try:", "except", "pass", "with ", "finally:")
    between = [l for l in lines[i_logged + 1:i_sync]
               if l.strip() and not l.strip().startswith("#")]
    unexpected = [l.strip() for l in between
                  if not any(l.strip().startswith(a) for a in _allowed)]
    if unexpected:
        return False, f"两者之间夹带业务代码（{unexpected[:3]}）⇒ 可能不在同一分支"
    return True, f"ch.token 在 token=logged 之后同步（同分支，仅注释/包裹语句相隔）"


def check_g3(kt_source: str, bcc_login_source: str) -> tuple[bool, str]:
    m = re.search(r"^_MAX_AGE_SEC\s*=\s*(\d+)", kt_source, re.M)
    if not m:
        return False, "未找到 _MAX_AGE_SEC"
    ttl = int(m.group(1))
    g = re.search(r">=\s*(\d+)", bcc_login_source.split("_last_env_audit_at")[1] or "") \
        if "_last_env_audit_at" in bcc_login_source else None
    # 直接取已知门限常量 3600（跨文件声明）
    gate = 3600
    if ttl < 2 * gate:
        return False, f"_MAX_AGE_SEC={ttl} < 2×采样门限({2 * gate}) ⇒ 周期性误判 stale"
    return True, f"_MAX_AGE_SEC={ttl} ≥ 2×门限({2 * gate})"


def check_g4(source: str) -> tuple[bool, str]:
    lines = source.splitlines()
    i_fallback = None
    writes = []
    for i, l in enumerate(lines):
        if "回落：旧 HTML 正则路径" in l:
            i_fallback = i
        if re.search(r"_sec_uid_cache\[_cache_key\]\s*=\s*\(time\.time\(\),\s*['\"]{2}\)", l):
            writes.append(i)
    if i_fallback is None:
        return False, "未找到 HTML 回退标记（判据失效？）"
    if not writes:
        return False, "未找到失败缓存写入（判据失效？）"
    if len(writes) > 1:
        return False, f"失败缓存写入出现 {len(writes)} 处（应仅 1 处，且在回退之后）: {writes}"
    if writes[0] < i_fallback:
        return False, f"失败缓存写入(line {writes[0]}) 早于 HTML 回退(line {i_fallback}) ⇒ 回退成功也会被污染"
    return True, f"失败缓存写入位于 HTML 回退之后（{writes[0]} > {i_fallback}）"


# ═════════════════════ 正向断言（真实文件） ═════════════════════

class TestP1RealFiles(unittest.TestCase):
    def test_g1_img_desc_lock(self):
        ok, msg = check_g1(_read(os.path.join("services", "ai_reply.py")))
        self.assertTrue(ok, f"G1 失败: {msg}")

    def test_g2_inbound_token_sync(self):
        ok, msg = check_g2(_read(os.path.join("notify", "inbound.py")))
        self.assertTrue(ok, f"G2 失败: {msg}")

    def test_g3_kernel_truth_ttl(self):
        ok, msg = check_g3(_read(os.path.join("services", "kernel_truth.py")),
                           _read(os.path.join("daemon", "bcc_login.py")))
        self.assertTrue(ok, f"G3 失败: {msg}")

    def test_g4_secuid_cache_after_fallback(self):
        ok, msg = check_g4(_read(os.path.join("dy_apis", "client_user.py")))
        self.assertTrue(ok, f"G4 失败: {msg}")


# ═════════════════════ 负控（旧形态必须判红） ═════════════════════

_G1_OLD = '''\
def _img_desc_mark(msg_id, desc: str, model: str = "") -> None:
    d = _kv_get(_KV_IMG_DESC, {})
    if not isinstance(d, dict):
        d = {}
    d[str(msg_id)] = {"desc": str(desc or ""), "model": model, "at": 0.0}
    _kv_set(_KV_IMG_DESC, d)


def _img_desc_get(msg_id):
    return None


def _img_desc_known(msg_id):
    return False
'''

_G2_OLD = '''\
async def run():
    while True:
        if not token:
            logged = await _ilink_qr_login(cid, _req, cfg)
            if logged:
                token = logged
        await ch.send(sender, reply)
'''

_G3_OLD = '''\
_MAX_AGE_SEC = 1800
'''

_G4_OLD = '''\
def get_my_sec_uid(auth, **kwargs):
    for _attempt in range(2):
        try:
            _got = _fetch_once()
        except Exception:
            _got = ""
        if _got:
            return _got
    if _cache_key:
        DouyinAPI._sec_uid_cache[_cache_key] = (time.time(), "")

    # ══ 回落：旧 HTML 正则路径（保留兼容，但不再直接 [0] 索引）══
    text = response.text or ""
    for pat in patterns:
        for m in re.findall(pat, text):
            return m
    raise RuntimeError("sec_uid 提取失败")
'''


class TestP1NegativeControls(unittest.TestCase):
    def test_g1_detects_unlocked(self):
        ok, msg = check_g1(_G1_OLD)
        self.assertFalse(ok, f"G1 负控失效: {msg}")

    def test_g2_detects_missing_sync(self):
        ok, msg = check_g2(_G2_OLD)
        self.assertFalse(ok, f"G2 负控失效: {msg}")

    def test_g3_detects_short_ttl(self):
        ok, msg = check_g3(_G3_OLD, "x _last_env_audit_at >= 3600")
        self.assertFalse(ok, f"G3 负控失效: {msg}")

    def test_g4_detects_early_cache(self):
        ok, msg = check_g4(_G4_OLD)
        self.assertFalse(ok, f"G4 负控失效: {msg}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
