# -*- coding: utf-8 -*-
"""H-22 审计 P2 修复的机械门禁（防复发）—— 2026-09-26。

## 六条判据
  G1  daemon/bcc_audit.py   ：`set_visible` 的**每一个** return dict 都必须带
      `settled` 键 —— 否则调用方无法区分「已达成」与「缺键」，正是 idx6 的成因。
  G2  api/accounts.py       ：`/show` 必须对「缺 settled」有显式兜底；
      `hide-browser` **不得**把「BCC 受理」当「已切回」（`settled=bool(ok)` 形态）。
  G3  api/platform.py       ：`_login_state_reason` 必须是 async 且用 `asyncio.to_thread`
      包裹阻塞体；所有调用点必须 `await`。
  G4  vbrowser_camoufox.py  ：async 关闭路径的 reap 必须走 `await asyncio.to_thread`。
  G5  notify/channels.py    ：`send_image` 的最终 POST 必须被 try/except 包裹。
  G6  dy_apis/login_api.py  ：`phoneMain` 在调用不可用链路**之前**显式 fail-closed。

每条判据均配负控（旧形态必须判红）。
"""
from __future__ import annotations

import ast
import os
import re
import textwrap
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))


def _read(rel: str) -> str:
    with open(os.path.join(_HERE, rel), encoding="utf-8") as f:
        return f.read()


def _func_src(src: str, name: str, cls: str | None = None):
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)

    def _find(node):
        for n in node.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
                return n
            if isinstance(n, ast.ClassDef):
                r = _find(n)
                if r:
                    return r
        return None

    n = _find(tree)
    if n is None:
        return None
    return textwrap.dedent("".join(lines[n.lineno - 1:n.end_lineno]))


# ═════════════════════ 判据 ═════════════════════

def check_g1(source: str) -> tuple[bool, str]:
    fn = _func_src(source, "set_visible")
    if not fn:
        return False, "未找到 set_visible"
    tree = ast.parse(fn)
    missing = []
    total = 0
    for n in ast.walk(tree):
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict):
            keys = [k.value for k in n.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            if "ok" in keys and "headless" in keys:      # 只看「状态型」返回
                total += 1
                if "settled" not in keys:
                    missing.append(n.lineno)
    if total == 0:
        return False, "set_visible 内未找到状态型 return（判据失效？）"
    if missing:
        return False, f"{len(missing)}/{total} 个状态 return 缺 settled 键（行 {missing}）"
    return True, f"{total} 个状态 return 均含 settled 键"


def check_g2(source: str) -> tuple[bool, str]:
    if "settled is not None" not in source and "settled is not True and settled is not False" not in source:
        # 兜底形态：必须显式处理「缺键」
        if not re.search(r"settled\s+is\s+not\s+True\s+and\s+settled\s+is\s+not\s+False", source):
            return False, "/show 缺 settled 键时无显式兜底"
    # hide-browser：不得出现 settled=bool(out.get("ok"))
    if re.search(r'settled\s*=\s*bool\(out\.get\(["\']ok["\']\)\)', source):
        return False, "hide-browser 仍把「BCC 受理」当「已切回」（假阳性形态）"
    if "_settled = out.get(\"settled\")" not in source and "_settled = out.get('settled')" not in source:
        return False, "hide-browser 未读取 BCC 真实 settled"
    return True, "/show 缺键有兜底；hide 按 BCC 真实 settled 如实表述"


def check_g3(source: str) -> tuple[bool, str]:
    fn = _func_src(source, "_login_state_reason")
    if not fn:
        return False, "未找到 _login_state_reason"
    if not fn.lstrip().startswith("async def"):
        return False, "_login_state_reason 不是 async（仍在事件循环里同步阻塞）"
    if "asyncio.to_thread" not in fn:
        return False, "_login_state_reason 未用 asyncio.to_thread 包裹阻塞体"
    bare = re.findall(r"(?<!await )_login_state_reason\(req\.account\)", source)
    if bare:
        return False, f"存在未 await 的调用点（{len(bare)} 处）"
    n_await = len(re.findall(r"await _login_state_reason\(req\.account\)", source))
    if n_await < 6:
        return False, f"仅 {n_await}/6 个调用点被 await"
    return True, f"async + to_thread；{n_await} 个调用点全部 await"


def check_g4(source: str) -> tuple[bool, str]:
    fn = _func_src(source, "close_camoufox_context")
    if not fn:
        return False, "未找到 close_camoufox_context"
    if "await asyncio.to_thread(_reap_camoufox_processes" in fn:
        return True, "async 关闭路径的 reap 已走 asyncio.to_thread"
    if "_reap_camoufox_processes(_ud)" in fn:
        return False, "async 关闭路径仍同步调用 reap（阻塞事件循环）"
    return False, "未找到 reap 调用（判据失效？）"


def check_g5(source: str) -> tuple[bool, str]:
    fn = _func_src(source, "send_image")
    if not fn:
        return False, "未找到 send_image"
    # 最终 POST 必须处于 try/except 保护内
    if "except Exception" not in fn:
        return False, "send_image 无 except Exception（异常会冒出方法）"
    m = re.search(r"try:\s*\n\s*async with s\.post\(", fn)
    if not m:
        return False, "最终 POST 未被 try 包裹"
    return True, "send_image 最终 POST 已被 try/except 包裹"


def check_g6(source: str) -> tuple[bool, str]:
    fn = _func_src(source, "phoneMain")
    if not fn:
        return False, "未找到 phoneMain"
    i_raise = fn.find("AUTH-062")
    i_call = fn.find("self.dyGeneratePhoneVerificationCode")
    if i_raise < 0:
        return False, "phoneMain 未显式 fail-closed"
    if i_call >= 0 and i_call < i_raise:
        return False, "仍在调用不可用链路之后才 fail-closed（契约仍可能被误用）"
    return True, "phoneMain 在调用不可用链路之前显式 fail-closed"


# ═════════════════════ 正向断言 ═════════════════════

class TestP2RealFiles(unittest.TestCase):
    def test_g1_settled_key_present(self):
        ok, msg = check_g1(_read(os.path.join("daemon", "bcc_audit.py")))
        self.assertTrue(ok, f"G1 失败: {msg}")

    def test_g2_accounts_settled_semantics(self):
        ok, msg = check_g2(_read(os.path.join("api", "accounts.py")))
        self.assertTrue(ok, f"G2 失败: {msg}")

    def test_g3_login_state_reason_async(self):
        ok, msg = check_g3(_read(os.path.join("api", "platform.py")))
        self.assertTrue(ok, f"G3 失败: {msg}")

    def test_g4_reap_off_loop(self):
        ok, msg = check_g4(_read("vbrowser_camoufox.py"))
        self.assertTrue(ok, f"G4 失败: {msg}")

    def test_g5_send_image_contained(self):
        ok, msg = check_g5(_read(os.path.join("notify", "channels.py")))
        self.assertTrue(ok, f"G5 失败: {msg}")

    def test_g6_phonemain_failclosed(self):
        ok, msg = check_g6(_read(os.path.join("dy_apis", "login_api.py")))
        self.assertTrue(ok, f"G6 失败: {msg}")


# ═════════════════════ 负控 ═════════════════════

_G1_OLD = textwrap.dedent('''
    async def set_visible(self, visible: bool):
        if self._switching and self._headless == target:
            return {"ok": True, "headless": target, "changed": False,
                    "switching": True, "deduped": True, "msg": "去重"}
        if self._headless == target:
            return {"ok": True, "headless": target, "changed": False}
''')

_G2_OLD = textwrap.dedent('''
    return ScanLoginResponse(ok=bool(out.get("ok")),
                             settled=bool(out.get("ok")),
                             switching=False,
                             msg=f"已恢复无头模式 · {name}")
''')

_G3_OLD = textwrap.dedent('''
def _login_state_reason(account: str) -> tuple[str, str]:
    from auto_dm.accounts import verify_credential
    v = verify_credential(account, lightweight=False)
    return "", ""
''')

_G4_OLD = textwrap.dedent('''
async def close_camoufox_context(context, user_data_dir=None):
    try:
        pass
    finally:
        if _ud:
            _reap_camoufox_processes(_ud)
''')

_G5_OLD = textwrap.dedent('''
    async def send_image(self, target, image_path, caption=""):
        async with s.post(
            f"{self.base_url}/ilink/bot/sendmessage",
            json=payload, headers=self._headers(),
        ) as resp:
            body = await resp.json(content_type=None)
        return ChannelResult(True, self.name, raw={"body": body})
''')

_G6_OLD = textwrap.dedent('''
    async def phoneMain(self, phone_num, env_path=None):
        auth = await self.dyGenerateInitData(env_path=env_path)
        sendCodeRes = self.dyGeneratePhoneVerificationCode(phone_num, auth)
        return {"status": "ok"}
''')


class TestP2NegativeControls(unittest.TestCase):
    def test_g1_detects_missing_settled(self):
        ok, msg = check_g1(_G1_OLD)
        self.assertFalse(ok, f"G1 负控失效: {msg}")

    def test_g2_detects_false_positive_hide(self):
        ok, msg = check_g2(_G2_OLD)
        self.assertFalse(ok, f"G2 负控失效: {msg}")

    def test_g3_detects_sync_blocking(self):
        ok, msg = check_g3(_G3_OLD)
        self.assertFalse(ok, f"G3 负控失效: {msg}")

    def test_g4_detects_sync_reap(self):
        ok, msg = check_g4(_G4_OLD)
        self.assertFalse(ok, f"G4 负控失效: {msg}")

    def test_g5_detects_uncontained_post(self):
        ok, msg = check_g5(_G5_OLD)
        self.assertFalse(ok, f"G5 负控失效: {msg}")

    def test_g6_detects_late_failclosed(self):
        ok, msg = check_g6(_G6_OLD)
        self.assertFalse(ok, f"G6 负控失效: {msg}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
