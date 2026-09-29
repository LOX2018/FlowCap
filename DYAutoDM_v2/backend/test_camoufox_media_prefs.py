# -*- coding: utf-8 -*-
"""Camoufox 虚拟媒体（麦克风/摄像头）门禁 —— H-12 后端修复的机械守护。

判据（AST，无需浏览器）：
  G1 两处启动（sync/async）都传了 `firefox_user_prefs=`（Chromium flag 对 Firefox 无效）。
  G2 该 pref 来自 `_media_prefs(cfg)`（不是随手字面量），且开关存在（可显式关）。
  G3 `_CAMOUFOX_MEDIA_PREFS` 含与 Chromium 假媒体 flag **语义等价**的四个核心 pref。
  G4 负控：把 firefox_user_prefs 从源码里去掉 ⇒ G1/G2 必须变红（判据有区分度）。

运行：python -m unittest backend.test_camoufox_media_prefs -v
"""
from __future__ import annotations

import ast
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "vbrowser_camoufox.py")

# 与 Chromium `_FAKE_MEDIA_ARGS` 语义等价的 Firefox 原生 pref
REQUIRED_PREFS = {
    "media.navigator.streams.fake",          # ~ --use-fake-device-for-media-stream
    "media.navigator.permission.disabled",   # ~ --use-fake-ui-for-media-stream
    "permissions.default.microphone",        # 权限：自动允许麦克风
    "media.peerconnection.enabled",          # WebRTC 必须开启（连麦/语音）
}


def _launch_calls_without_media_kwarg(src: str):
    """返回「构造了 Camoufox/AsyncCamoufox 但未传 firefox_user_prefs」的调用位置。"""
    tree = ast.parse(src)
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fname = ""
        if isinstance(node.func, ast.Name):
            fname = node.func.id
        elif isinstance(node.func, ast.Attribute):
            fname = node.func.attr
        if fname not in ("Camoufox", "AsyncCamoufox"):
            continue
        kwargs = {k.arg for k in node.keywords if k.arg}
        if "firefox_user_prefs" not in kwargs:
            bad.append(f"line {node.lineno}: {fname}(...) 未传 firefox_user_prefs")
    return bad


def _count_launch_calls(src: str) -> int:
    tree = ast.parse(src)
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
            if name in ("Camoufox", "AsyncCamoufox"):
                n += 1
    return n


class TestCamoufoxMediaPrefs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = open(TARGET, encoding="utf-8").read()

    def test_g1_all_launches_pass_prefs(self):
        bad = _launch_calls_without_media_kwarg(self.src)
        self.assertEqual(_count_launch_calls(self.src), 2, "应恰好两处启动（sync+async）")
        self.assertEqual(bad, [], f"有启动调用未注入虚拟媒体 pref: {bad}")

    def test_g2_prefs_via_helper_not_literal(self):
        self.assertIn("firefox_user_prefs=_media_prefs(cfg)", self.src,
                      "应通过 _media_prefs(cfg) 注入（便于显式开关/单点维护）")
        self.assertIn("def _media_prefs(", self.src)
        self.assertIn("DY_FAKE_MEDIA_OFF", self.src, "缺少显式关闭开关")

    def test_g3_required_prefs_present(self):
        tree = ast.parse(self.src)
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "_CAMOUFOX_MEDIA_PREFS":
                        if isinstance(node.value, ast.Dict):
                            for k in node.value.keys:
                                if isinstance(k, ast.Constant):
                                    found.add(k.value)
        missing = REQUIRED_PREFS - found
        self.assertEqual(missing, set(), f"_CAMOUFOX_MEDIA_PREFS 缺关键 pref: {missing}")

    def test_g4_negative_control(self):
        """负控：去掉注入后，G1 判据必须能变红（证明判据非装饰）。"""
        stripped = self.src.replace("firefox_user_prefs=_media_prefs(cfg),", "")
        self.assertNotEqual(stripped, self.src, "负控替换未生效（源码形态变了？）")
        bad = _launch_calls_without_media_kwarg(stripped)
        self.assertEqual(len(bad), 2, "去掉注入后应被 G1 判据抓出 2 处")


if __name__ == "__main__":
    unittest.main(verbosity=2)
