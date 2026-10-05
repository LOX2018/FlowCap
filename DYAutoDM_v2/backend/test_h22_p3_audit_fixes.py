# -*- coding: utf-8 -*-
"""H-22 审计 P3 修复的机械门禁（防复发）—— 2026-09-26。

## 四条判据（一致性 / 卫生级）
  G1  backend/dy_apis/client_live.py ：REST `browser_version` 参数必须取**短版本号**
      （`get_profile()["browser_version"]`），**不得**取完整 UA（`get_profile()["ua"]`）。
      全仓 REST 参数 30+ 处统一为短版本号；`builder/proto.py` 的 IM protobuf 分支
      另有一套「UA 去掉 Mozilla/」的既定约定，**不属**本判据范围（REST vs protobuf 两码事）。
  G2  backend/link_resolve.py        ：`_reflow_resolve` 的 `ua` 默认值必须是 `None`
      并回落到档案 `_ua()`；**不得**保留硬编码 UA 默认值（该值会进入 X-Bogus 签名 payload）。
  G3  frontend                       ：取流失败必须有**重试入口**；`useAuthedMediaUrl`
      必须接受 `bust` 参数且重试时**清该 src 的负缓存**（否则被 30s FAIL_TTL 挡住）。
  G4  scripts/repair_conversation_identity.py ：退化分支必须按 `av_polluted` 保护
      **合法对端头像**，不得无条件把 `new_av` 置空（会把有效头像写 NULL）。

每条判据均配负控（旧形态必须判红）。
"""
from __future__ import annotations

import os
import re
import textwrap
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)          # DYAutoDM_v2/


def _read(rel: str) -> str:
    with open(os.path.join(_HERE, rel), encoding="utf-8") as f:
        return f.read()


def _read_repo(rel: str) -> str:
    with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
        return f.read()


# ═════════════════════ 判据 ═════════════════════

def check_g1(source: str) -> tuple[bool, str]:
    if re.search(r'add_param\(\s*"browser_version"\s*,\s*get_profile\(\)\["ua"\]', source):
        return False, 'REST browser_version 仍取完整 UA（应是短版本号）'
    if 'add_param("browser_version", get_profile()["browser_version"])' not in source:
        return False, "未找到规范的短版本号取值"
    return True, "REST browser_version 取短版本号（与全仓 30+ 处一致）"


def check_g2(source: str) -> tuple[bool, str]:
    m = re.search(r"def _reflow_resolve\(([^)]*)\)", source)
    if not m:
        return False, "未找到 _reflow_resolve"
    sig = m.group(1)
    if re.search(r"ua\s*=\s*['\"]Mozilla", sig):
        return False, "ua 默认值仍硬编码浏览器 UA"
    if not re.search(r"ua\s*=\s*None", sig):
        return False, "签名未改 ua=None"
    if "ua = ua or _ua()" not in source:
        return False, "函数体未回落到档案 _ua()"
    return True, "ua=None 缺省回落档案 _ua()"


def check_g3(stage_src: str, media_src: str) -> tuple[bool, str]:
    if "setBust" not in stage_src:
        return False, "失败分支无重试状态（setBust）"
    if "重试" not in stage_src:
        return False, "失败分支无重试文案/按钮"
    if not re.search(r"useAuthedMediaUrl\(src\?:\s*string,\s*bust\s*=\s*0\)", media_src):
        return False, "useAuthedMediaUrl 未接受 bust 参数"
    if "failCache.delete(src)" not in media_src:
        return False, "重试未清负缓存（会被 FAIL_TTL 挡住）"
    return True, "重试入口 + bust 清负缓存"


def check_g4(source: str) -> tuple[bool, str]:
    if re.search(r'old_av,\s*"",\s*"本号身份冒充且无真值', source):
        return False, "退化分支仍无条件清空头像（会误伤合法对端头像）"
    if '_na = "" if av_polluted else old_av' not in source:
        return False, "未按 av_polluted 保护头像"
    if 'old_av, _na, "本号身份冒充且无真值' not in source:
        return False, "未使用受保护的 _na"
    return True, "按 av_polluted 保护合法对端头像"


# ═════════════════════ 正向断言 ═════════════════════

class TestP3RealFiles(unittest.TestCase):
    def test_g1_browser_version_short(self):
        ok, msg = check_g1(_read(os.path.join("dy_apis", "client_live.py")))
        self.assertTrue(ok, f"G1 失败: {msg}")

    def test_g2_reflow_ua_from_profile(self):
        ok, msg = check_g2(_read("link_resolve.py"))
        self.assertTrue(ok, f"G2 失败: {msg}")

    def test_g3_frontend_retry(self):
        stage = _read_repo(os.path.join(
            "frontend", "src", "components", "player", "player-media-stage.tsx"))
        media = _read_repo(os.path.join(
            "frontend", "src", "lib", "authed-media.ts"))
        ok, msg = check_g3(stage, media)
        self.assertTrue(ok, f"G3 失败: {msg}")

    def test_g4_avatar_preserved(self):
        ok, msg = check_g4(_read_repo(os.path.join(
            "scripts", "repair_conversation_identity.py")))
        self.assertTrue(ok, f"G4 失败: {msg}")


# ═════════════════════ 负控 ═════════════════════

_G1_OLD = textwrap.dedent('''
    params.add_param("browser_version", get_profile()["ua"])
''')

_G2_OLD = textwrap.dedent('''
def _reflow_resolve(room_id, sec_user_id, auth=None, ua="Mozilla/5.0"):
    """调 reflow/info。"""
    params = {}
    xb = _xbogus_sign("q", ua)
''')

_G3_OLD_STAGE = textwrap.dedent('''
  if (resolvedUrl === null) return { ...media, url: "" };
  <span>{failed ? "视频加载失败" : "无可播放地址"}</span>
''')

_G3_OLD_MEDIA = textwrap.dedent('''
export function useAuthedMediaUrl(src?: string): string | null | undefined {
  const local = isLocalApiUrl(src);
}
''')

_G4_OLD = textwrap.dedent('''
        if not new_name:
            if self_masq:
                plan.append((rid, cid, old_name, str(r["peer_id"] or ""),
                             old_av, "", "本号身份冒充且无真值→退化裸uid"))
''')


class TestP3NegativeControls(unittest.TestCase):
    def test_g1_detects_ua_as_version(self):
        ok, msg = check_g1(_G1_OLD)
        self.assertFalse(ok, f"G1 负控失效: {msg}")

    def test_g2_detects_hardcoded_ua(self):
        ok, msg = check_g2(_G2_OLD)
        self.assertFalse(ok, f"G2 负控失效: {msg}")

    def test_g3_detects_no_retry(self):
        ok, msg = check_g3(_G3_OLD_STAGE, _G3_OLD_MEDIA)
        self.assertFalse(ok, f"G3 负控失效: {msg}")

    def test_g4_detects_avatar_wipe(self):
        ok, msg = check_g4(_G4_OLD)
        self.assertFalse(ok, f"G4 负控失效: {msg}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
